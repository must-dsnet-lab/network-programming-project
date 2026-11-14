import json
from dataclasses import dataclass

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


def encode_open_session_request(request: OpenSessionRequest) -> bytes:
    _validate_request_id(request.request_id)
    message = {
        "version": 1,
        "type": "open_session",
        "request_id": request.request_id,
    }

    return _encode_management_message(message)


def decode_open_session_request(data: bytes) -> OpenSessionRequest:
    message = _decode_management_message(
        data, "open_session", ("request_id",),
    )
    _validate_request_id(message["request_id"])
    return OpenSessionRequest(
        request_id=message["request_id"],
    )


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
    expected_type: str,
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

    if message.get("type") != expected_type:
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
