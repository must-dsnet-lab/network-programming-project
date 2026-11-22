from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import CancelledError, Future
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from queue import Empty, Full, Queue
from ssl import SSLSocket
from threading import Lock
from time import monotonic
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from rover.vehicle.accounts import AccountStore
    from rover.vehicle.telemetry_registration import TelemetryRegistry

PRODUCT_SESSION_LIMIT = 6

type _Reply = tuple[str, dict[str, object]]
type _Operation = Callable[[ControlGuardState, float, float], tuple[ControlGuardState, _Reply]]

def _error(reason: str) -> _Reply:
    return "management_error", {"reason": reason}


class ExpertManagementRuntime:
    def __init__(
        self, trusted: dict[str, bytes], operation_timeout_s: float, *,
        accounts: AccountStore | None = None,
    ):
        self.address: tuple[str, int]
        self._trusted = trusted
        self._accounts = accounts
        self._operation_timeout_s = operation_timeout_s
        capacity = PRODUCT_SESSION_LIMIT + 2 if accounts is not None else 2
        self._pending: Queue[tuple[_Operation, Future[_Reply], float]] = Queue(maxsize=capacity)
        self._admission = Lock()
        self._stopped = False
        self._closed_connections: list[str] = []
        self._sessions: dict[str, ControlSession] = {}
        self._last_authority_results: dict[str, dict[str, object]] = {}
        self._control = ExpertVehicleControl()
        self._previous_tick_s: float | None = None
        self._tick_interval_ms = 0.0
        self._telemetry: TelemetryRegistry | None = None
        self._registration_port: int | None = None
        self._session_addresses: dict[str, tuple[str, int] | None] = {}
        self._session_activity: dict[str, float] = {}
        self._session_events: dict[str, deque[str]] = {}
        self._session_send_results: dict[str, str] = {}
        self._recently_closed: deque[dict[str, object]] = deque(maxlen=24)
        self._published_sessions: tuple[dict[str, object], ...] = ()

    @property
    def dynamic_telemetry(self) -> bool:
        return self._accounts is not None

    def configure_telemetry_registration(self, port: int) -> None:
        from rover.vehicle.telemetry_registration import TelemetryRegistry

        self._telemetry = TelemetryRegistry()
        self._registration_port = port

    def register_telemetry(self, packet: bytes, peer: tuple[str, int], now_s: float) -> None:
        if self._telemetry is not None:
            session_id = self._telemetry.accept(packet, peer, now_s=now_s)
            if session_id is not None:
                for key, session in self._sessions.items():
                    if session.session_id == session_id:
                        self._session_activity[key] = now_s
                        self._session_events[key].append("遥测登记")
                        self._session_send_results.pop(key, None)
                        break

    def telemetry_destinations(self) -> tuple[tuple[str, int], ...]:
        return self._telemetry.destinations() if self._telemetry is not None else ()

    def _can_control(self, session: ControlSession) -> bool:
        return session.role == "operator" and (
            not self.dynamic_telemetry or (
                self._telemetry is not None and self._telemetry.destination(session.session_id) is not None
            )
        )

    def record_telemetry_result(self, destination: tuple[str, int], sent: bool) -> None:
        if self._telemetry is not None:
            for key, session in self._sessions.items():
                if self._telemetry.destination(session.session_id) == destination:
                    self._session_send_results[key] = "已发送" if sent else "发送失败"

    def _session_row(self, session: ControlSession, *, online: bool) -> dict[str, object]:
        key = session.connection_id
        destination = self._telemetry.destination(session.session_id) if self._telemetry is not None else None
        grant = self.authority.grant
        return {
            "session_id": session.session_id, "client_id": session.client_id, "role": session.role,
            "peer_address": self._session_addresses.get(key), "telemetry_address": destination,
            "last_activity_s": self._session_activity.get(key),
            "telemetry_result": self._session_send_results.get(key, "等待发送" if destination else "待登记"),
            "holds_authority": online and grant is not None and grant.session == session,
            "online": online, "events": tuple(self._session_events.get(key, ())),
        }

    def publish_admin_snapshot(self) -> None:
        if self.dynamic_telemetry:
            self._published_sessions = (
                *(self._session_row(session, online=True) for session in self._sessions.values()),
                *reversed(self._recently_closed),
            )

    def admin_snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(row.copy() for row in self._published_sessions)

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
                if connection_id in self._session_events:
                    self._session_events[connection_id].append("断开")
                    self._recently_closed.append(self._session_row(session, online=False))
                    self._session_events.pop(connection_id)
                    self._session_addresses.pop(connection_id, None)
                    self._session_activity.pop(connection_id, None)
                    self._session_send_results.pop(connection_id, None)
                if self._telemetry is not None:
                    self._telemetry.disconnect(session.session_id)
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
        *,
        role: str = "operator",
        peer_address: tuple[str, int] | None = None,
    ) -> tuple[ControlGuardState, _Reply]:
        message_type = message["type"]
        session = self._sessions.get(connection_id)
        if message_type == "open_session":
            if session is None:
                if self._accounts is not None and len(self._sessions) >= PRODUCT_SESSION_LIMIT:
                    return guard, _error("capacity_reached")
                session = ControlSession(client_id, connection_id, uuid4().hex, role)
                self._sessions[connection_id] = session
                if self.dynamic_telemetry:
                    self._session_addresses[connection_id] = peer_address
                    self._session_activity[connection_id] = now_s
                    self._session_events[connection_id] = deque(["登录"], maxlen=20)
            return guard, ("session_opened", {"session_id": session.session_id})
        if message_type not in ("query_authority", "prepare_telemetry") and message_type not in EXPERT_CONTROL_ACTIONS:
            return guard, _error("unsupported_message_type")
        session_id = message.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return guard, _error("invalid_request")
        if session is None or session_id != session.session_id:
            return guard, _error("invalid_session")
        if self.dynamic_telemetry:
            self._session_activity[connection_id] = now_s

        if message_type == "query_authority":
            return guard, ("query_authority_result", self._status(guard, session, now_s, queue_wait_ms))

        if message_type == "prepare_telemetry":
            if self._telemetry is None or peer_address is None:
                return guard, _error("telemetry_registration_unavailable")
            token = self._telemetry.issue(session_id, now_s=now_s, expected_ip=peer_address[0])
            return guard, ("prepare_telemetry_result", {"token": token, "port": self._registration_port})

        if session.role != "operator":
            return guard, (f"{message_type}_result", {
                "accepted": False, "reason": "permission_denied", "phase": self.authority.phase.value,
            })
        if not self._can_control(session):
            return guard, (f"{message_type}_result", {
                "accepted": False, "reason": "telemetry_not_registered", "phase": self.authority.phase.value,
            })

        recipient = None
        if message_type == "transfer_authority":
            target_id = message.get("target_session_id")
            recipient = next(
                (candidate for candidate in self._sessions.values()
                 if candidate.session_id == target_id and self._can_control(candidate)),
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
            if self.dynamic_telemetry and message_type != "renew_authority":
                event = {
                    "acquire_authority": "申请控制权", "set_authority_enabled": "设置控制使能",
                    "release_authority": "归还控制权", "transfer_authority": "交接控制权",
                    "emergency_stop": "急停", "reset_control": "显式复位",
                }[message_type]
                self._session_events[connection_id].append(f"{event} · {'已接受' if fields['accepted'] else '已拒绝'}")
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
        if self._accounts is not None:
            result["account_role"] = session.role
            result["telemetry_registered"] = (
                self._telemetry is not None and self._telemetry.destination(session.session_id) is not None
            )
        if session.role != "operator":
            return result
        if grant is not None and grant.session == session:
            result["generation"] = generation
            if grant.lease_expires_at_s is not None:
                result["lease_remaining_s"] = max(0.0, grant.lease_expires_at_s - now_s)
            candidates = [
                candidate for candidate in self._sessions.values()
                if candidate != session and self._can_control(candidate)
            ]
            result["target_session_ids"] = [candidate.session_id for candidate in candidates]
            if self._accounts is not None:
                result["transfer_candidates"] = [
                    {"session_id": candidate.session_id, "client_id": candidate.client_id}
                    for candidate in candidates
                ]
            allowed_actions.extend((
                "set_authority_enabled", "renew_authority", "release_authority", "emergency_stop",
            ))
            if candidates:
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
        client_id, credential = request.get("client_id"), request.get("credential")
        if request["type"] == "register_account":
            reason = self._register_account(request, client_id, credential)
            send_reply(last_request_id, ("registration_result", {"accepted": reason is None, "reason": reason}))
            return
        if request["type"] != "authenticate":
            send_reply(last_request_id, _error("authentication_required"))
            return
        role = self._authenticate(client_id, credential)
        accepted = role in ("observer", "operator")
        send_reply(last_request_id, ("authentication_result", {"accepted": accepted}))
        if not accepted or not isinstance(client_id, str):
            return

        connection_id = uuid4().hex
        peer_address = tls.getpeername() if self._accounts is not None else None
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
                if self._accounts is not None and self._accounts.role(client_id) != role:
                    send_reply(request_id, _error("account_role_changed"))
                    return
                def dispatch(guard, now_s, wait_ms):
                    return self._dispatch(
                        guard, client_id, connection_id, message, now_s, wait_ms,
                        role=role, peer_address=peer_address,
                    )

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

    def _register_account(
        self, request: dict[str, object], client_id: object, credential: object,
    ) -> str | None:
        if self._accounts is None:
            return "registration_unavailable"
        if request.keys() != {"version", "type", "request_id", "client_id", "credential"}:
            return "invalid_account"
        if not isinstance(client_id, str):
            return "invalid_account"
        if not isinstance(credential, str):
            return "invalid_credential"
        return self._accounts.register(client_id, credential)

    def _authenticate(self, client_id: object, credential: object) -> str | None:
        if not isinstance(client_id, str) or not isinstance(credential, str):
            return None
        if self._accounts is not None:
            return self._accounts.authenticate(client_id, credential)
        if verify_client_credential(self._trusted, client_id, credential.encode("utf-8")):
            return "operator"
        return None


@contextmanager
def expert_management_listener(
    cert_dir: Path,
    trusted_credentials: dict[str, bytes],
    address: tuple[str, int] = ("127.0.0.1", 19177),
    *,
    operation_timeout_s: float = 1.0,
    accounts: AccountStore | None = None,
) -> Iterator[ExpertManagementRuntime]:
    if not isfinite(operation_timeout_s) or operation_timeout_s <= 0:
        raise ValueError("operation_timeout_s must be positive and finite")
    runtime = ExpertManagementRuntime(trusted_credentials, operation_timeout_s, accounts=accounts)
    options = {"max_connections": PRODUCT_SESSION_LIMIT + 2} if accounts is not None else {}
    with parallel_tls_server(cert_dir, address, runtime.serve, **options) as bound_address:
        runtime.address = bound_address
        try:
            print(f"Expert management TLS listening on {bound_address}", flush=True)
            yield runtime
        finally:
            runtime.stop()
