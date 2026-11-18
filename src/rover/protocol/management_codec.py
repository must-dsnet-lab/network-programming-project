import json
from dataclasses import dataclass, field

MAX_MANAGEMENT_MESSAGE_BYTES = 4096

@dataclass(frozen=True)
# 申请建立会话
class OpenSessionRequest:
    request_id: int

@dataclass(frozen=True)
# 车端成功回复的编码
class SessionOpened:
    request_id: int
    session_id: str

@dataclass(frozen=True)
class AuthenticateRequest:
    request_id: int  # request_id 把「桌面端的此次认证请求」和「车端对此次请求的回复」对应起来
    client_id: str
    # `repr=False` 让 print(request) 省略凭据字段
    credential: str = field(repr=False)

@dataclass(frozen=True)
class AuthenticationResult:
    request_id: int  # request_id 把「车端对此次请求的回复」和「桌面端的此次认证请求」对应起来
    accepted: bool


@dataclass(frozen=True)
class EnableSessionRequest:
    request_id: int
    session_id: str


@dataclass(frozen=True)
class SessionEnableResult:
    request_id: int
    enabled: bool


def encode_session_enable_result(reply: SessionEnableResult) -> bytes:
    _validate_request_id(reply.request_id)
    _validate_enabled(reply.enabled)
    return _encode_management_message(
        {
            "version": 1,
            "type": "session_enable_result",
            "request_id": reply.request_id,
            "enabled": reply.enabled,
        }
    )


def decode_session_enable_result(data: bytes) -> SessionEnableResult:
    message = _decode_management_message(
        data,
        "session_enable_result",
        ("request_id", "enabled"),
    )
    _validate_request_id(message["request_id"])
    _validate_enabled(message["enabled"])
    return SessionEnableResult(
        request_id=message["request_id"],
        enabled=message["enabled"],
    )


def _validate_enabled(value: object) -> None:
    if type(value) is not bool:
        raise ValueError("enabled must be a boolean")


def encode_enable_session_request(request: EnableSessionRequest) -> bytes:
    _validate_request_id(request.request_id)
    _validate_session_id(request.session_id)
    return _encode_management_message(
        {
            "version": 1,
            "type": "enable_session",
            "request_id": request.request_id,
            "session_id": request.session_id,
        }
    )


def decode_enable_session_request(data: bytes) -> EnableSessionRequest:
    message = _decode_management_message(
        data,
        "enable_session",
        ("request_id", "session_id"),
    )
    return _enable_session_request_from_message(message)


def _open_session_request_from_message(message: dict) -> OpenSessionRequest:
    _validate_request_id(message["request_id"])
    return OpenSessionRequest(request_id=message["request_id"])


def _enable_session_request_from_message(message: dict) -> EnableSessionRequest:
    _validate_request_id(message["request_id"])
    _validate_session_id(message["session_id"])
    return EnableSessionRequest(
        request_id=message["request_id"],
        session_id=message["session_id"],
    )


def encode_open_session_request(request: OpenSessionRequest) -> bytes:
    _validate_request_id(request.request_id)
    message = {
        "version": 1,
        "type": "open_session",
        "request_id": request.request_id,
    }

    return _encode_management_message(message)


def decode_open_session_request(data: bytes) -> OpenSessionRequest:
    message = _decode_management_message(data, "open_session", ("request_id",))
    return _open_session_request_from_message(message)


def encode_session_opened(reply: SessionOpened) -> bytes:
    _validate_request_id(reply.request_id)
    _validate_session_id(reply.session_id)
    message = {
        "version": 1,
        "type": "session_opened",
        "request_id": reply.request_id,
        "session_id": reply.session_id,
    }

    return _encode_management_message(message)


def decode_session_opened(data: bytes) -> SessionOpened:
    message = _decode_management_message(
        data,
        "session_opened",
        ("request_id", "session_id"),
    )
    _validate_request_id(message["request_id"])
    _validate_session_id(message["session_id"])
    return SessionOpened(
        request_id=message["request_id"],
        session_id=message["session_id"],
    )


# 负责解析、检查版本和消息用途，返回字典
def _decode_management_message(
    data: bytes,
    expected_type: str | tuple[str, ...],
    required_fields: tuple[str, ...],
) -> dict:
    if len(data) > MAX_MANAGEMENT_MESSAGE_BYTES:
        raise ValueError("management message is too large")

    message = json.loads(data.decode("utf-8"))

    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")

    version = message.get("version")
    if type(version) is not int or version != 1:
        raise ValueError("unsupported protocol version")

    allowed_types = (
        (expected_type,) if isinstance(expected_type, str) else expected_type
    )
    if message.get("type") not in allowed_types:
        raise ValueError("unexpected message type")

    for field in required_fields:
        if field not in message:
            raise ValueError(f"missing required field: {field}")

    return message


def _validate_request_id(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("request_id must be a non-negative integer")


def _validate_session_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("session_id must be a non-blank string")


def _encode_management_message(message: dict) -> bytes:
    packet = json.dumps(message).encode("utf-8")
    if len(packet) > MAX_MANAGEMENT_MESSAGE_BYTES:
        raise ValueError("management message is too large")
    return packet


def encode_authenticate_request(request: AuthenticateRequest) -> bytes:
    _validate_request_id(request.request_id)
    _validate_auth_text(request.client_id, "client_id")
    _validate_auth_text(request.credential, "credential")
    return _encode_management_message(
        {
            "version": 1,
            "type": "authenticate",
            "request_id": request.request_id,
            "client_id": request.client_id,
            "credential": request.credential,
        }
    )


def decode_authenticate_request(data: bytes) -> AuthenticateRequest:
    message = _decode_management_message(
        data,
        "authenticate",
        ("request_id", "client_id", "credential"),
    )
    _validate_request_id(message["request_id"])
    _validate_auth_text(message["client_id"], "client_id")
    _validate_auth_text(message["credential"], "credential")
    return AuthenticateRequest(
        request_id=message["request_id"],
        client_id=message["client_id"],
        credential=message["credential"],
    )


def _validate_auth_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field_name} must be valid UTF-8 text") from None


def encode_authentication_result(reply: AuthenticationResult) -> bytes:
    _validate_request_id(reply.request_id)
    _validate_auth_accepted(reply.accepted)
    return _encode_management_message(
        {
            "version": 1,
            "type": "authentication_result",
            "request_id": reply.request_id,
            "accepted": reply.accepted,
        }
    )


def decode_authentication_result(data: bytes) -> AuthenticationResult:
    message = _decode_management_message(
        data,
        "authentication_result",
        ("request_id", "accepted"),
    )
    _validate_request_id(message["request_id"])
    _validate_auth_accepted(message["accepted"])
    return AuthenticationResult(
        request_id=message["request_id"],
        accepted=message["accepted"],
    )


def _validate_auth_accepted(value: object) -> None:
    if type(value) is not bool:
        raise ValueError("accepted must be a boolean")


def decode_session_request(data: bytes) -> OpenSessionRequest | EnableSessionRequest:
    message = _decode_management_message(
        data,
        ("open_session", "enable_session"),
        ("request_id",),
    )
    if message["type"] == "open_session":
        return _open_session_request_from_message(message)
    if "session_id" not in message:
        raise ValueError("missing required field: session_id")
    return _enable_session_request_from_message(message)
