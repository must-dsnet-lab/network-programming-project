from hmac import compare_digest
from dataclasses import dataclass, replace
from uuid import uuid4

from rover.protocol.management_codec import OpenSessionRequest, SessionOpened
from rover.vehicle.control_guard import (
    ControlGuardState,
    open_control_session,
    close_control_session,
    enable_control_session,
)


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
    session_id = guard.session_id  # guard.session_id 表示车端当前登记的会话
    if session_id is None:
        session_id = uuid4().hex
        guard = open_control_session(guard, session_id)
        connection = replace(connection, session_id=session_id)
    elif (
        session_id != connection.session_id
    ):  # `connection.session_id` 表示这条连接获分配的会话
        raise ValueError("another control session is already open")

    reply = SessionOpened(request_id=request.request_id, session_id=session_id)
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
