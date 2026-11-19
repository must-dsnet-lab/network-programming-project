from ssl import SSLSocket

from rover.protocol.management_codec import (
    AuthenticateRequest,
    AuthenticationResult,
    ControlWindowResult,
    EnableSessionRequest,
    IssueControlWindowRequest,
    OpenSessionRequest,
    SessionEnableResult,
    SessionOpened,
    decode_authentication_result,
    decode_control_window_result,
    decode_session_enable_result,
    decode_session_opened,
    encode_authenticate_request,
    encode_enable_session_request,
    encode_issue_control_window_request,
    encode_open_session_request,
)
from rover.transport.tcp_transport import iter_records, send_record


class ManagementClient:
    """Synchronous management requests on one verified TLS connection."""

    def __init__(self, tls: SSLSocket):
        self._tls = tls
        self._records = iter_records(tls, record_timeout_s=1.0)
        self._next_request_id = 1
        self._usable = True

    def _exchange(self, request, encode, decode):
        if not self._usable:
            raise ConnectionError("management connection is unusable")

        packet = encode(request)
        self._next_request_id += 1
        try:
            send_record(self._tls, packet)
            payload = next(self._records, None) # 阻塞等待先于判断超时执行
            if payload is None:
                raise ConnectionError("management connection closed before reply")
            reply = decode(payload)
            if reply.request_id != request.request_id:
                raise ValueError("reply request_id does not match request")
        except (OSError, ValueError):
            self._usable = False
            raise

        return reply

    def authenticate(self, client_id: str, credential: str) -> AuthenticationResult:
        request = AuthenticateRequest(self._next_request_id, client_id, credential)
        reply = self._exchange(
            request, encode_authenticate_request, decode_authentication_result
        )
        if not reply.accepted:
            self._usable = False
        return reply

    def open_session(self) -> SessionOpened:
        request = OpenSessionRequest(self._next_request_id)
        return self._exchange(
            request, encode_open_session_request, decode_session_opened
        )

    def enable_session(self, session_id: str) -> SessionEnableResult:
        request = EnableSessionRequest(self._next_request_id, session_id)
        return self._exchange(
            request, encode_enable_session_request, decode_session_enable_result
        )

    def issue_control_window(self, session_id: str) -> ControlWindowResult:
        request = IssueControlWindowRequest(self._next_request_id, session_id)
        return self._exchange(
            request,
            encode_issue_control_window_request,
            decode_control_window_result,
        )
