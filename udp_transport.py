import socket

from control_codec import decode_target, encode_target
from control_target import ControlTarget

# 65535 是接收缓冲上限；收到消息后，编解码模块仍执行 1200 字节的应用层限制
UDP_RECEIVE_BUFFER_BYTES = 65535


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
