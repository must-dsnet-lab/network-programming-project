from rover.core.control_target import ControlTarget
import json
from dataclasses import dataclass
from math import isfinite
from rover.protocol.control_auth import (
    compute_control_mac,
    verify_control_mac,
)

MAX_CONTROL_MESSAGE_BYTES = 1200


class ControlMacVerificationError(ValueError):
    """The received control MAC failed verification."""


class ControlProtocolVersionError(ValueError):
    """A validly signed control datagram used an unexpected protocol version."""


@dataclass(frozen=True)
class AuthorityGeneration:
    """An authority grant number scoped to one vehicle runtime."""

    epoch: str
    number: int


@dataclass(frozen=True)
# 在已有会话中更新运动目标
# controlCommand控制的是一个具体的报文
class ControlCommand:
    session_id: str  # 属于哪次控制会话
    seq: int  # 该会话的第几条控制报文
    target: ControlTarget  # 速度与朝向目标
    window_id: str | None = None  # 带编号的指令有效时间段
    generation: AuthorityGeneration | None = None


def _validate_motion_value(value: object, field_name: str) -> None:
    if type(value) not in (int, float):
        raise ValueError(f"{field_name} must be a number")

    try:
        numeric_value = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} is outside float range") from exc

    if not isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")


def _validate_command_metadata(session_id: object, seq: object) -> None:
    # session_id必须是字符串，且不能是空串或纯空白
    if not isinstance(session_id, str) or not session_id.strip():#.strip()：去掉字符串开头和结尾的空白字符
        raise ValueError("session_id must be a non-blank string")
    # seq必须是非负整数，允许0；拒绝布尔值、浮点数和字符串
    if type(seq) is not int or seq < 0:
        raise ValueError("seq must be a non-negative integer")


# 发送：ControlTarget → 字典 → JSON 文本 → UTF-8 字节
def encode_target(target: ControlTarget) -> bytes:
    _validate_motion_value(target.speed_mps, "speed_mps")
    _validate_motion_value(target.heading_rad, "heading_rad")

    message = {
        "version": 1,
        "type": "control_target",
        "speed_mps": target.speed_mps,
        "heading_rad": target.heading_rad,
    }
    #json.dumps(message)：字典转成 JSON 文本，结果是 str
    #.encode("utf-8")：文本转成待发送的 bytes
    return json.dumps(message).encode("utf-8")

# 接收：UTF-8 字节 → JSON 文本 → 字典 → ControlTarget
def decode_target(data: bytes) -> ControlTarget:
    # data.decode("utf-8")：收到的字节还原成文本
    # json.loads(...)：解析文本得到字典，再取出字段构造 ControlTarget

    if len(data) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    message = json.loads(data.decode("utf-8"))

    if not isinstance(message, dict): #dict 是 Python 语言内部的叫法(检查用)
        # JSON object 是网络传输协议（JSON 标准）的叫法（报错用）
        raise ValueError("message must be a JSON object")

    version = message.get("version")
    if type(version) is not int or version != 1:
        raise ValueError("unsupported protocol version")

    if message.get("type") != "control_target":
        raise ValueError("unexpected message type")

    if "speed_mps" not in message or "heading_rad" not in message:
        raise ValueError("missing motion fields")

    for field in ("speed_mps", "heading_rad"):
        _validate_motion_value(message[field], field)

    return ControlTarget(
        speed_mps=message["speed_mps"],
        heading_rad=message["heading_rad"]
    )


def encode_command(command: ControlCommand) -> bytes:
    _validate_command_metadata(command.session_id, command.seq)
    _validate_motion_value(command.target.speed_mps, "speed_mps")
    _validate_motion_value(command.target.heading_rad, "heading_rad")

    message = {
        "version": 1,
        "type": "control_command",
        "session_id": command.session_id,
        "seq": command.seq,
        "speed_mps": command.target.speed_mps,
        "heading_rad": command.target.heading_rad,
    }

    packet = json.dumps(message).encode("utf-8")
    # 检查的是完整编码结果的字节数，包含字段名、数值和JSON格式开销
    if len(packet) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    return packet


def decode_command(data: bytes) -> ControlCommand:
    # 一定要先检查长度再decode -> 减小开销，防止报文既超长又无法解析
    if len(data) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    message = json.loads(data.decode("utf-8"))

    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")

    version = message.get("version")
    if type(version) is not int or version != 1:
        raise ValueError("unsupported protocol version")

    if message.get("type") != "control_command":
        raise ValueError("unexpected message type")

    for field in ("session_id", "seq", "speed_mps", "heading_rad"):
        if field not in message:
            raise ValueError(f"missing required field: {field}")

    _validate_command_metadata(message["session_id"], message["seq"])

    for field in ("speed_mps", "heading_rad"):
        _validate_motion_value(message[field], field)

    return ControlCommand(
        session_id=message["session_id"],
        seq=message["seq"],
        target=ControlTarget(
            speed_mps=message["speed_mps"],
            heading_rad=message["heading_rad"],
        ),
    )


def _encode_control_fields(message: dict) -> bytes:
    return json.dumps(
        message,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def encode_authenticated_command(command: ControlCommand, key: bytes) -> bytes:
    message = json.loads(encode_command(command))
    message["type"] = "authenticated_control_command"

    # _encode_control_fields()固定字段排序和空格格式，让双方计算 HMAC 时使用一致的字节。
    payload = _encode_control_fields(message)
    message["mac"] = compute_control_mac(payload, key)
    packet = _encode_control_fields(message)

    if len(packet) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    return packet


def decode_authenticated_command(data: bytes, key: bytes) -> ControlCommand:
    if len(data) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    message = json.loads(data.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("message must be a JSON object")

    # 接收时先取出 mac，用其余全部字段重新计算并核对
    received_mac = message.pop("mac", None)
    payload = _encode_control_fields(message)
    if not verify_control_mac(payload, key, received_mac):
        raise ValueError("control MAC verification failed")

    if message.get("type") != "authenticated_control_command":
        raise ValueError("unexpected message type")

    # 核对成功后交给已有 decode_command()检查版本、会话、序号和运动参数。最后修改 type 是为了复用这个已有入口
    message["type"] = "control_command"
    return decode_command(_encode_control_fields(message))


def _validate_window_id(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("window_id must be a non-blank string")


def _windowed_control_fields(command: ControlCommand, version: int) -> dict:
    _validate_window_id(command.window_id)
    message = json.loads(encode_command(command))
    message["version"] = version
    message["type"] = "authenticated_control_command"
    message["window_id"] = command.window_id
    return message


def _encode_signed_control_fields(message: dict, key: bytes) -> bytes:
    payload = _encode_control_fields(message)
    message["mac"] = compute_control_mac(payload, key)
    packet = _encode_control_fields(message)

    if len(packet) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    return packet


def encode_windowed_command(command: ControlCommand, key: bytes) -> bytes:
    return _encode_signed_control_fields(_windowed_control_fields(command, 2), key)


def _verify_windowed_control_fields(data: bytes, key: bytes, expected_version: int) -> dict:
    if len(data) > MAX_CONTROL_MESSAGE_BYTES:
        raise ValueError("control message is too large")

    try:
        message = json.loads(data.decode("utf-8"))
        if not isinstance(message, dict):
            raise ValueError("message must be a JSON object")

        received_mac = message.pop("mac", None)
        payload = _encode_control_fields(message)
    except RecursionError as exc:
        raise ValueError("control message is too deeply nested") from exc

    if not verify_control_mac(payload, key, received_mac):
        raise ControlMacVerificationError("control MAC verification failed")

    version = message.get("version")

    if type(version) is not int or version != expected_version:
        raise ControlProtocolVersionError("unsupported protocol version")

    if message.get("type") != "authenticated_control_command":
        raise ValueError("unexpected message type")

    _validate_window_id(message.get("window_id"))
    return message


def _command_from_windowed_fields(
    message: dict, generation: AuthorityGeneration | None = None
) -> ControlCommand:
    window_id = message.pop("window_id")

    # 内部适配已认证的字典，复用原来的控制字段校验。
    message["version"] = 1
    message["type"] = "control_command"
    base = decode_command(_encode_control_fields(message))

    return ControlCommand(
        session_id=base.session_id,
        seq=base.seq,
        target=base.target,
        window_id=window_id,
        generation=generation,
    )


def decode_windowed_command(data: bytes, key: bytes) -> ControlCommand:
    return _command_from_windowed_fields(_verify_windowed_control_fields(data, key, 2))


def _validate_generation_fields(epoch: object, number: object) -> None:
    if not isinstance(epoch, str) or not epoch.strip():
        raise ValueError("generation.epoch must be a non-blank string")
    if type(number) is not int or number <= 0:
        raise ValueError("generation.number must be a positive integer")


def encode_expert_command(command: ControlCommand, key: bytes) -> bytes:
    generation = command.generation
    if not isinstance(generation, AuthorityGeneration):
        raise ValueError("generation is required for expert control commands")
    _validate_generation_fields(generation.epoch, generation.number)

    message = _windowed_control_fields(command, 3)
    message["generation"] = {"epoch": generation.epoch, "number": generation.number}
    return _encode_signed_control_fields(message, key)


def decode_expert_command(data: bytes, key: bytes) -> ControlCommand:
    message = _verify_windowed_control_fields(data, key, 3)
    generation = message.pop("generation", None)
    if not isinstance(generation, dict):
        raise ValueError("generation must be a JSON object")

    epoch, number = generation.get("epoch"), generation.get("number")
    _validate_generation_fields(epoch, number)
    return _command_from_windowed_fields(message, AuthorityGeneration(epoch, number))
