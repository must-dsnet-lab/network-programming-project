from control_target import ControlTarget
import json

#发送：ControlTarget → 字典 → JSON 文本 → UTF-8 字节
def encode_target(target: ControlTarget) -> bytes:
    message = {
        "version": 1,
        "type": "control_target",
        "speed_mps": target.speed_mps,
        "heading_rad": target.heading_rad,
    }
    #json.dumps(message)：字典转成 JSON 文本，结果是 str
    #.encode("utf-8")：文本转成待发送的 bytes
    return json.dumps(message).encode("utf-8")

#接收：UTF-8 字节 → JSON 文本 → 字典 → ControlTarget
def decode_target(data: bytes) -> ControlTarget:
    #data.decode("utf-8")：收到的字节还原成文本
    #json.loads(...)：解析文本得到字典，再取出字段构造 ControlTarget
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
        #类型必须是int或float，不能是其他类型
        if type(message[field]) not in (int, float):
            raise ValueError(f"{field} must be a number")

    return ControlTarget(
        speed_mps=message["speed_mps"],
        heading_rad=message["heading_rad"]
    )