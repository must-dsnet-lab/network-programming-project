import socket
from time import monotonic
from collections.abc import Iterator
from rover.transport.tcp_records import TcpRecordBuffer, encode_record

TCP_RECEIVE_CHUNK_BYTES = 4096


def send_record(sock: socket.socket, payload: bytes) -> None:
    record = encode_record(payload)
    # `sendall()` 会不断地尝试调用底层的 send()，直到所有的数据都成功进入发送缓冲区
    sock.sendall(record)


def iter_records(
    sock: socket.socket,
    *,
    record_timeout_s: float | None = None,  # 一条记录允许花多久来收齐
) -> Iterator[bytes]:
    buffer = TcpRecordBuffer()
    deadline_s: float | None = None  # 	当前记录最晚何时收齐

    while True:
        # 收到第一个块之前 deadline_s == None 是完全正常的
        chunk = _recv_chunk(sock, deadline_s)
        if not chunk:
            buffer.finish()  # 数据截断则抛出异常
            return

        received_at_s = monotonic()  # recv() 返回这批字节时，时钟上的读数
        if deadline_s is not None and received_at_s >= deadline_s:
            raise TimeoutError("record assembly timed out")

        # `feed()` 负责跨多次读取拼接记录
        payloads = buffer.feed(chunk)

        if not buffer.has_partial_record:
            deadline_s = None  # 表示当前没有正在拼接的记录
        # 第二次接收之后，payloads 为空的时候代表只收到包的一半，还没有写入 payloads，
        # 上一轮的计时仍在进行，所以这种情况不会刷新deadline
        elif record_timeout_s is not None and (deadline_s is None or payloads):
            deadline_s = received_at_s + record_timeout_s

        # `yield from` 把已经拼好的正文逐条交付
        yield from payloads
        """ 等价写法:
        for payload in payloads:
            yield payload
        """


def _recv_chunk(sock: socket.socket, deadline_s: float | None) -> bytes:
    if deadline_s is None:  # 没有拼接记录就接收下一条消息
        return sock.recv(TCP_RECEIVE_CHUNK_BYTES)

    remaining_s = deadline_s - monotonic()
    if remaining_s <= 0:
        raise TimeoutError("record assembly timed out")

    original_timeout_s = (
        sock.gettimeout()
    )  # Socket 底层的 timeout 比剩余期限严格，就沿用底层超时
    if original_timeout_s is not None and original_timeout_s <= remaining_s:
        return sock.recv(TCP_RECEIVE_CHUNK_BYTES)

    # 如果 remaining_s 秒后数据没到达接收缓冲区就抛出 TimeoutError
    sock.settimeout(remaining_s)
    try:
        return sock.recv(TCP_RECEIVE_CHUNK_BYTES)
    except TimeoutError as exc:
        raise TimeoutError("record assembly timed out") from exc
    finally:
        sock.settimeout(original_timeout_s)
