from rover.core.control_target import ControlTarget
import json
from dataclasses import dataclass
from math import isfinite


MAX_CONTROL_MESSAGE_BYTES = 1200

@dataclass(frozen=True)
# controlCommand控制的是一个具体的报文
class ControlCommand:
    session_id: str  # 属于哪次控制会话
    seq: int  # 该会话的第几条控制报文
    target: ControlTarget  # 速度与朝向目标


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
