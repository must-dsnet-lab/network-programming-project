from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from ssl import SSLSocket
from threading import Lock
from time import monotonic
from uuid import uuid4

from rover.protocol.expert_management_codec import (
    decode_expert_management,
    encode_expert_management,
)
from rover.transport.parallel_tls_server import parallel_tls_server
from rover.transport.tcp_transport import iter_records, send_record
from rover.vehicle.control_authority import ControlAuthority, ControlSession
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.management_session import verify_client_credential

type _Reply = tuple[str, dict[str, object]]
type _Operation = Callable[[float], _Reply]


def _error(reason: str) -> _Reply:
    return "management_error", {"reason": reason}


class _ExpertManagementRuntime:
    def __init__(self, trusted: dict[str, bytes], operation_timeout_s: float):
        self.address: tuple[str, int]
        self._trusted = trusted
        self._operation_timeout_s = operation_timeout_s
        self._pending: Queue[tuple[_Operation, Future[_Reply], float]] = Queue(maxsize=2)
        self._admission = Lock()
        self._stopped = False
        self._closed_connections: list[str] = []
        self._sessions: dict[str, ControlSession] = {}
        self._authority = ControlAuthority()
        self._previous_tick_s: float | None = None
        self._tick_interval_ms = 0.0

    def _submit(self, operation: _Operation) -> _Reply:
        future: Future[_Reply] = Future()
        with self._admission:
            if self._stopped:
                raise CancelledError()
            try:
                self._pending.put_nowait((operation, future, monotonic()))
            except Full:
                return _error("queue_full")
        try:
            return future.result(timeout=self._operation_timeout_s)
        except TimeoutError:
            if future.cancel():
                return _error("simulation_timeout")
            # An operation already started on the simulation thread has one outcome.
            return future.result()

    def __call__(self, guard: ControlGuardState) -> ControlGuardState:
        now_s = monotonic()
        if self._previous_tick_s is not None:
            self._tick_interval_ms = (now_s - self._previous_tick_s) * 1000.0
        self._previous_tick_s = now_s

        with self._admission:
            closed, self._closed_connections = self._closed_connections, []
        for connection_id in closed:
            session = self._sessions.pop(connection_id, None)
            if session is not None:
                self._authority = self._authority.disconnect(session)

        try:
            operation, future, queued_at_s = self._pending.get_nowait()
        except Empty:
            return guard
        if future.set_running_or_notify_cancel():
            try:
                reply = operation((monotonic() - queued_at_s) * 1000.0)
            except Exception as error:
                future.set_exception(error)
            else:
                future.set_result(reply)
        return guard

    def _dispatch(
        self,
        client_id: str,
        connection_id: str,
        message: dict[str, object],
        queue_wait_ms: float,
    ) -> _Reply:
        message_type = message["type"]
        session = self._sessions.get(connection_id)
        if message_type == "open_session":
            if session is None:
                session = ControlSession(client_id, connection_id, uuid4().hex)
                self._sessions[connection_id] = session
            return "session_opened", {"session_id": session.session_id}
        if message_type != "query_authority":
            return _error("unsupported_message_type")
        session_id = message.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return _error("invalid_request")
        if session is None or session_id != session.session_id:
            return _error("invalid_session")
        grant = self._authority.grant
        return "query_authority_result", {
            "accepted": True,
            "reason": None,
            "phase": self._authority.phase.value,
            "owns_authority": grant is not None and grant.session == session,
            "online_sessions": len(self._sessions),
            "queue_wait_ms": round(queue_wait_ms, 3),
            "tick_interval_ms": round(self._tick_interval_ms, 3),
        }

    def serve(self, tls: SSLSocket) -> None:
        def send_reply(request_id: int, reply: _Reply) -> None:
            message_type, fields = reply
            send_record(tls, encode_expert_management(message_type, request_id, **fields))

        records = iter_records(tls, record_timeout_s=1.0)
        payload = next(records, None)
        if payload is None:
            return
        request = decode_expert_management(payload)
        last_request_id = request["request_id"]
        if request["type"] != "authenticate":
            send_reply(last_request_id, _error("authentication_required"))
            return
        client_id, credential = request.get("client_id"), request.get("credential")
        accepted = (
            isinstance(client_id, str)
            and isinstance(credential, str)
            and verify_client_credential(self._trusted, client_id, credential.encode("utf-8"))
        )
        send_reply(last_request_id, ("authentication_result", {"accepted": accepted}))
        if not accepted:
            return

        connection_id = uuid4().hex
        opened_session = False
        try:
            for payload in records:
                message = decode_expert_management(payload)
                request_id = message["request_id"]
                if request_id <= last_request_id:
                    reason = (
                        "duplicate_request_id" if request_id == last_request_id
                        else "out_of_order_request_id"
                    )
                    send_reply(request_id, _error(reason))
                    continue
                last_request_id = request_id
                reply = self._submit(
                    lambda wait_ms: self._dispatch(client_id, connection_id, message, wait_ms)
                )
                opened_session = opened_session or reply[0] == "session_opened"
                send_reply(request_id, reply)
        except CancelledError:
            return
        finally:
            if opened_session:
                with self._admission:
                    self._closed_connections.append(connection_id)

    def stop(self) -> None:
        with self._admission:
            self._stopped = True
            while True:
                try:
                    _, future, _ = self._pending.get_nowait()
                except Empty:
                    return
                future.cancel()


@contextmanager
def expert_management_listener(
    cert_dir: Path,
    trusted_credentials: dict[str, bytes],
    address: tuple[str, int] = ("127.0.0.1", 19177),
    *,
    operation_timeout_s: float = 1.0,
) -> Iterator[_ExpertManagementRuntime]:
    if not isfinite(operation_timeout_s) or operation_timeout_s <= 0:
        raise ValueError("operation_timeout_s must be positive and finite")
    runtime = _ExpertManagementRuntime(trusted_credentials, operation_timeout_s)
    with parallel_tls_server(cert_dir, address, runtime.serve) as bound_address:
        runtime.address = bound_address
        try:
            print(f"Expert management TLS listening on {bound_address}", flush=True)
            yield runtime
        finally:
            runtime.stop()
