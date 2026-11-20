from hmac import compare_digest
from dataclasses import dataclass, replace
from uuid import uuid4
from secrets import token_bytes
from rover.protocol.management_codec import (
    ControlWindowResult,
    IssueControlWindowRequest,
    OpenSessionRequest,
    SessionOpened,
    EmergencyStopRequest,
    EmergencyStopResult,
    ResetControlRequest,
    ResetControlResult,
)
from rover.vehicle.control_guard import (
    ControlGuardState,
    open_control_session,
    close_control_session,
    enable_control_session,
    update_control_guard,
)
from rover.vehicle.control_window import issue_control_window

def verify_client_credential(
    trusted_credentials: dict[str, bytes],  # 正确答案
    client_id: str,  # 相当于账号
    credential: bytes,  # 相当于密码
) -> bool:
    expected = trusted_credentials.get(client_id)
    if not expected or not credential:
        return False

    return compare_digest(expected, credential)


@dataclass(frozen=True)
class AuthenticatedConnection:  # 由车端为验证通过的连接创建
    client_id: str
    session_id: str | None = None


def handle_open_session(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
    request: OpenSessionRequest,
) -> tuple[ControlGuardState, AuthenticatedConnection, SessionOpened]:
    session_id = guard.session_id

    if session_id is None:
        session_id = uuid4().hex
        guard = open_control_session(guard, session_id)
        guard = replace(
            guard,
            control_key=token_bytes(32),
            control_window=None,
        )
        connection = replace(connection, session_id=session_id)
    elif session_id != connection.session_id:
        raise ValueError("another control session is already open")

    if guard.control_key is None:
        raise ValueError("control session has no HMAC key")

    reply = SessionOpened(
        request_id=request.request_id,
        session_id=session_id,
        control_key=guard.control_key.hex(),
    )
    return guard, connection, reply


def handle_connection_closed(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
) -> ControlGuardState:
    session_id = connection.session_id  # session_id的来源是connection
    # session_id is None 表示这条连接没有绑定会话
    # session_id != guard.session_id 表示这条连接绑定的会话已经不是车端当前登记的会话
    if session_id is None or session_id != guard.session_id:
        return guard

    # 只有断开的连接持有车端当前登记的ID时，才调用 close_control_session()
    return close_control_session(guard, session_id)


def handle_enable_session(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
    session_id: str,
    *,
    now_s: float,
) -> ControlGuardState:
    if (
        guard.session_id is None
        or session_id != guard.session_id
        or session_id != connection.session_id
    ):
        raise ValueError("connection does not own this control session")

    return enable_control_session(guard, now_s)


def handle_issue_control_window(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
    request: IssueControlWindowRequest,
    *,
    now_s: float,
) -> tuple[ControlGuardState, ControlWindowResult]:
    guard = update_control_guard(guard, now_s=now_s)
    if (
        guard.fault_latched
        or not guard.control_key
        or guard.session_id is None
        or connection.session_id != guard.session_id
        or request.session_id != guard.session_id
    ):
        return guard, ControlWindowResult(request.request_id, False)

    window = issue_control_window(uuid4().hex, now_s=now_s)
    guard = replace(guard, control_window=window)
    return guard, ControlWindowResult(
        request_id=request.request_id,
        granted=True,
        window_id=window.window_id,
        lifetime_s=window.expires_at_s - window.issued_at_s,
    )


def handle_emergency_stop(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
    request: EmergencyStopRequest,
) -> tuple[ControlGuardState, EmergencyStopResult]:
    if (
        guard.session_id is None
        or connection.session_id != guard.session_id
        or request.session_id != guard.session_id
    ):
        return guard, EmergencyStopResult(request.request_id, False)

    guard = replace(
        guard,
        last_accepted=None,
        enabled=False,
        enabled_at_s=None,
        control_window=None,
        fault_latched=True,
    )
    return guard, EmergencyStopResult(request.request_id, True)

# 当前会话的拥有者，在故障锁存、已停用且没有有效控制目标时，才能复位
def handle_reset_control(
    guard: ControlGuardState,
    connection: AuthenticatedConnection,
    request: ResetControlRequest,
) -> tuple[ControlGuardState, AuthenticatedConnection, ResetControlResult]:
    if (
        guard.session_id is None
        or connection.session_id != guard.session_id
        or request.session_id != guard.session_id
        or not guard.fault_latched
        or guard.enabled
        or guard.last_accepted is not None
    ):
        return guard, connection, ResetControlResult(request.request_id, False)

    guard = close_control_session(guard, guard.session_id)
    guard = replace(guard, fault_latched=False)
    connection = replace(connection, session_id=None)
    return guard, connection, ResetControlResult(request.request_id, True)
