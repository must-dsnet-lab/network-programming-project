from collections.abc import Callable
from socket import socket

from rover.protocol.management_codec import (
    AuthenticationResult,
    ControlWindowResult,
    EnableSessionRequest,
    IssueControlWindowRequest,
    OpenSessionRequest,
    SessionEnableResult,
    SessionOpened,
    decode_authenticate_request,
    decode_session_request,
    encode_authentication_result,
    encode_control_window_result,
    encode_session_enable_result,
    encode_session_opened,
    EmergencyStopRequest,
    EmergencyStopResult,
    encode_emergency_stop_result,
)
from rover.transport.tcp_transport import iter_records, send_record
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    verify_client_credential,
)


def serve_management_connection(
    sock: socket,
    trusted_credentials: dict[str, bytes],
    # 回调: 把函数当作参数传进去，等里面的代码调用它
    # open_session(connection, request) 处理申请，返回更新后的连接上下文和回复。
    open_session: Callable[
        [AuthenticatedConnection, OpenSessionRequest],  # 参数
        tuple[AuthenticatedConnection, SessionOpened],  # 返回值
    ],
    # close_connection(connection) 在处理结束时撤销该连接仍持有的会话。
    close_connection: Callable[[AuthenticatedConnection], None],
    *,
    enable_session: (
        Callable[[AuthenticatedConnection, EnableSessionRequest], bool] | None
    ) = None,
    issue_control_window: (
        Callable[
            [AuthenticatedConnection, IssueControlWindowRequest],
            ControlWindowResult,
        ]
        | None
    ) = None,
    emergency_stop: (
        Callable[
            [AuthenticatedConnection, EmergencyStopRequest],
            EmergencyStopResult,
        ]
        | None
    ) = None,
) -> None:
    records = iter_records(sock, record_timeout_s=1.0)
    payload = next(records, None)
    if payload is None:
        return

    request = decode_authenticate_request(payload)
    accepted = verify_client_credential(
        trusted_credentials,
        request.client_id,
        request.credential.encode("utf-8"),
    )
    result = AuthenticationResult(request_id=request.request_id, accepted=accepted)
    send_record(sock, encode_authentication_result(result))
    if not accepted:
        return

    connection = AuthenticatedConnection(client_id=request.client_id)
    try:
        for payload in records:
            session_request = decode_session_request(payload)

            if isinstance(session_request, EmergencyStopRequest):
                reply = EmergencyStopResult(session_request.request_id, False)
                if emergency_stop is not None:
                    reply = emergency_stop(connection, session_request)
                send_record(sock, encode_emergency_stop_result(reply))
                continue

            if isinstance(session_request, EnableSessionRequest):
                enabled = False
                if enable_session is not None:
                    enabled = enable_session(connection, session_request)

                enable_result = SessionEnableResult(
                    request_id=session_request.request_id, enabled=enabled,
                )
                send_record(sock, encode_session_enable_result(enable_result))
                continue

            if isinstance(session_request, IssueControlWindowRequest):
                window_result = ControlWindowResult(
                    request_id=session_request.request_id,
                    granted=False,
                )
                if issue_control_window is not None:
                    window_result = issue_control_window(connection, session_request)
                send_record(sock, encode_control_window_result(window_result))
                continue

            connection, reply = open_session(connection, session_request)
            """等价于：
            def open_session(connection, request):
                nonlocal guard #允许直接修改函数外部的变量值
                guard, next_connection, reply = handle_open_session(guard, connection, request)
                return next_connection, reply"""

            send_record(sock, encode_session_opened(reply))
    finally:
        close_connection(connection)
        """等价于：
        def close_connection(connection):
            nonlocal guard
            guard = handle_connection_closed(guard, connection)"""
