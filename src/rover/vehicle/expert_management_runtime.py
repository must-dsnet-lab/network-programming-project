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

from rover.core.vehicle_model import VehicleState
from rover.protocol.expert_management_codec import (
    decode_expert_management,
    encode_expert_management,
)
from rover.transport.parallel_tls_server import parallel_tls_server
from rover.transport.tcp_transport import iter_records, send_record
from rover.vehicle.control_authority import AuthorityPhase, ControlAuthority, ControlSession
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.expert_control import (
    EXPERT_CONTROL_ACTIONS, AuthorityLeaseExpiry, ExpertVehicleControl,
)
from rover.vehicle.management_session import verify_client_credential

type _Reply = tuple[str, dict[str, object]]
type _Operation = Callable[[ControlGuardState, float, float], tuple[ControlGuardState, _Reply]]

def _error(reason: str) -> _Reply:
    return "management_error", {"reason": reason}


class ExpertManagementRuntime:
    def __init__(self, trusted: dict[str, bytes], operation_timeout_s: float):
        self.address: tuple[str, int]
        self._trusted = trusted
        self._operation_timeout_s = operation_timeout_s
        self._pending: Queue[tuple[_Operation, Future[_Reply], float]] = Queue(maxsize=2)
        self._admission = Lock()
        self._stopped = False
        self._closed_connections: list[str] = []
        self._sessions: dict[str, ControlSession] = {}
        self._last_authority_results: dict[str, dict[str, object]] = {}
        self._control = ExpertVehicleControl()
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

    @property
    def authority(self) -> ControlAuthority:
        return self._control.authority

    @property
    def last_lease_expiry(self) -> AuthorityLeaseExpiry | None:
        return self._control.last_lease_expiry

    def advance_time(self, guard: ControlGuardState, now_s: float) -> ControlGuardState:
        return self._control.advance_time(guard, now_s)

    def _apply_closed_connections(self, guard: ControlGuardState) -> ControlGuardState:
        with self._admission:
            closed, self._closed_connections = self._closed_connections, []
        for connection_id in closed:
            session = self._sessions.pop(connection_id, None)
            self._last_authority_results.pop(connection_id, None)
            if session is not None:
                guard = self._control.disconnect(guard, session)
        return guard

    def observe_vehicle(
        self, guard: ControlGuardState, vehicle: VehicleState, *, now_s: float | None = None,
    ) -> ControlGuardState:
        guard = self._apply_closed_connections(self._control.sync_fault(guard))
        return self._control.observe_vehicle(
            guard, vehicle, now_s=monotonic() if now_s is None else now_s,
        )

    def __call__(self, guard: ControlGuardState) -> ControlGuardState:
        now_s = monotonic()
        if self._previous_tick_s is not None:
            self._tick_interval_ms = (now_s - self._previous_tick_s) * 1000.0
        self._previous_tick_s = now_s
        guard = self.advance_time(guard, now_s)
        guard = self._apply_closed_connections(guard)

        try:
            operation, future, queued_at_s = self._pending.get_nowait()
        except Empty:
            return guard
        if future.set_running_or_notify_cancel():
            try:
                started_at_s = monotonic()
                guard = self.advance_time(guard, started_at_s)
                guard, reply = operation(guard, started_at_s, (started_at_s - queued_at_s) * 1000.0)
            except Exception as error:
                future.set_exception(error)
            else:
                future.set_result(reply)
        return guard

    def _dispatch(
        self,
        guard: ControlGuardState,
        client_id: str,
        connection_id: str,
        message: dict[str, object],
        now_s: float,
        queue_wait_ms: float,
    ) -> tuple[ControlGuardState, _Reply]:
        message_type = message["type"]
        session = self._sessions.get(connection_id)
        if message_type == "open_session":
            if session is None:
                session = ControlSession(client_id, connection_id, uuid4().hex)
                self._sessions[connection_id] = session
            return guard, ("session_opened", {"session_id": session.session_id})
        if message_type != "query_authority" and message_type not in EXPERT_CONTROL_ACTIONS:
            return guard, _error("unsupported_message_type")
        session_id = message.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return guard, _error("invalid_request")
        if session is None or session_id != session.session_id:
            return guard, _error("invalid_session")

        if message_type == "query_authority":
            return guard, ("query_authority_result", self._status(guard, session, now_s, queue_wait_ms))

        recipient = None
        if message_type == "transfer_authority":
            target_id = message.get("target_session_id")
            recipient = next(
                (candidate for candidate in self._sessions.values() if candidate.session_id == target_id),
                None,
            )
        guard, fields = self._control.dispatch(
            guard, session, message, recipient=recipient, now_s=now_s
        )
        if message_type != "issue_control_window":
            self._last_authority_results[connection_id] = {
                "action": message_type,
                "accepted": fields["accepted"],
                "reason": fields["reason"],
            }
        return guard, (f"{message_type}_result", fields)

    def _status(
        self, guard: ControlGuardState, session: ControlSession, now_s: float, queue_wait_ms: float,
    ) -> dict[str, object]:
        authority = self.authority
        grant = authority.grant
        owns_authority = grant is not None and grant.session == session
        generation = (
            {"epoch": grant.generation.epoch, "number": grant.generation.number}
            if grant is not None else None
        )
        allowed_actions: list[str] = []
        result: dict[str, object] = {
            "accepted": True,
            "reason": None,
            "phase": authority.phase.value,
            "owns_authority": owns_authority,
            "online_sessions": len(self._sessions),
            "queue_wait_ms": round(queue_wait_ms, 3),
            "tick_interval_ms": round(self._tick_interval_ms, 3),
            "holder_client_id": grant.session.client_id if grant is not None else None,
            "authority_generation": generation,
            "allowed_actions": allowed_actions,
            "last_authority_result": self._last_authority_results.get(session.connection_id),
        }
        if grant is not None and grant.session == session:
            result["generation"] = generation
            if grant.lease_expires_at_s is not None:
                result["lease_remaining_s"] = max(0.0, grant.lease_expires_at_s - now_s)
            targets = [
                candidate.session_id for candidate in self._sessions.values() if candidate != session
            ]
            result["target_session_ids"] = targets
            allowed_actions.extend((
                "set_authority_enabled", "renew_authority", "release_authority", "emergency_stop",
            ))
            if targets:
                allowed_actions.append("transfer_authority")
        elif authority.phase is AuthorityPhase.IDLE:
            allowed_actions.append("acquire_authority")
        elif self._control.reset_rejection(guard) is None:
            allowed_actions.append("reset_control")
        return result

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
                def dispatch(guard, now_s, wait_ms):
                    return self._dispatch(guard, client_id, connection_id, message, now_s, wait_ms)

                reply = self._submit(dispatch)
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
) -> Iterator[ExpertManagementRuntime]:
    if not isfinite(operation_timeout_s) or operation_timeout_s <= 0:
        raise ValueError("operation_timeout_s must be positive and finite")
    runtime = ExpertManagementRuntime(trusted_credentials, operation_timeout_s)
    with parallel_tls_server(cert_dir, address, runtime.serve) as bound_address:
        runtime.address = bound_address
        try:
            print(f"Expert management TLS listening on {bound_address}", flush=True)
            yield runtime
        finally:
            runtime.stop()
