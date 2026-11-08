import socket

from control_codec import decode_target, encode_target
from control_target import ControlTarget
from telemetry_codec import (
    TelemetrySnapshot,
    decode_telemetry,
    encode_telemetry,
)

# 65535 是接收缓冲上限；收到消息后，编解码模块仍执行 1200 字节的应用层限制
UDP_RECEIVE_BUFFER_BYTES = 65535

# 发送一份期望状态，返回发送字节数
def send_target(
    sock: socket.socket,  # 网络电话机
    target: ControlTarget,
    address: tuple[str, int],  # IP + 端口
) -> int:  # 返回发送的字节数
    packet = encode_target(target)
    # sendto() 将编码后的字节发往指定地址，返回发送字节数
    return sock.sendto(packet, address)


def receive_target(
    sock: socket.socket,
) -> tuple[ControlTarget, tuple[str, int]]:
    # recvfrom() 返回收到的字节和发送端地址，再交给 decode_target() 校验、还原
    # 真正的数据由 recvfrom() 从操作系统的 Socket 接收队列中取得
    packet, peer = sock.recvfrom(UDP_RECEIVE_BUFFER_BYTES)
    target = decode_target(packet)
    return target, peer


def try_receive_target(
    sock: socket.socket,
) -> tuple[ControlTarget, tuple[str, int]] | None:
    """尝试接收一次；要求调用方已设置 sock.setblocking(False)。"""
    try:
        return receive_target(sock)
    except BlockingIOError:
        return None

# 发送一份实际状态，返回发送字节数
def send_telemetry(
    sock: socket.socket,
    snapshot: TelemetrySnapshot,
    address: tuple[str, int],
) -> int:
    packet = encode_telemetry(snapshot)
    return sock.sendto(packet, address)

# 有合法报文时返回快照与来源地址，空闲时返回 None，坏报文抛出 ValueError
def try_receive_telemetry(
    sock: socket.socket,
) -> tuple[TelemetrySnapshot, tuple[str, int]] | None:
    """要求调用方已设置 sock.setblocking(False)。"""
    try:
        # recvfrom() 会把报文从接收队列中取走，解码失败也不会放回去
        packet, peer = sock.recvfrom(UDP_RECEIVE_BUFFER_BYTES)
    except BlockingIOError:
        return None

    snapshot = decode_telemetry(packet)
    return snapshot, peer
