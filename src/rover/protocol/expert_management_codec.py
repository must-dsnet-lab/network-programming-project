import json
from typing import NoReturn

from rover.protocol.management_codec import MAX_MANAGEMENT_MESSAGE_BYTES


_EXPERT_MANAGEMENT_VERSION = 2


def encode_expert_management(message_type: str, request_id: int, **fields: object) -> bytes:
    if "version" in fields or "type" in fields:
        raise ValueError("expert management envelope fields are reserved")
    message = {
        "version": _EXPERT_MANAGEMENT_VERSION,
        "type": message_type,
        "request_id": request_id,
        **fields,
    }
    _validate_envelope(message)
    payload = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8")
    _check_size(payload)
    return payload


def decode_expert_management(payload: bytes) -> dict[str, object]:
    _check_size(payload)
    try:
        message = json.loads(payload.decode("utf-8"), parse_constant=_reject_constant)
    except RecursionError as exc:
        raise ValueError("expert management message is nested too deeply") from exc
    if not isinstance(message, dict):
        raise ValueError("expert management message must be a JSON object")
    _validate_envelope(message)
    return message


def _validate_envelope(message: dict[str, object]) -> None:
    version = message.get("version")
    if type(version) is not int or version != _EXPERT_MANAGEMENT_VERSION:
        raise ValueError("unsupported expert management version")
    message_type = message.get("type")
    if not isinstance(message_type, str) or not message_type.strip():
        raise ValueError("expert management type must be a non-blank string")
    request_id = message.get("request_id")
    if type(request_id) is not int or request_id <= 0:
        raise ValueError("expert management request_id must be a positive integer")


def _check_size(payload: bytes) -> None:
    if len(payload) > MAX_MANAGEMENT_MESSAGE_BYTES:
        raise ValueError("expert management message is too large")


def _reject_constant(_value: str) -> NoReturn:
    raise ValueError("expert management message must contain valid JSON")
