from rover.protocol.management_codec import MAX_MANAGEMENT_MESSAGE_BYTES

RECORD_HEADER_BYTES = 4


# 让接收端记住上一次收到的字节
class TcpRecordBuffer:
    @property
    def has_partial_record(self) -> bool:
        return bool(self._buffer)

    def __init__(self) -> None:
        self._buffer = bytearray()  # 创建一个可变的字节数组对象

    # 每次接收一块字节，返回这次凑齐的正文列表
    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        payloads: list[bytes] = []
        # 如果下一条的头部或正文还没收齐，循环就停止，把剩余字节留到下次 feed()
        while len(self._buffer) >= RECORD_HEADER_BYTES:
            header = self._buffer[:RECORD_HEADER_BYTES]
            length = int.from_bytes(header, byteorder="big")
            _validate_payload_length(length)

            record_size = RECORD_HEADER_BYTES + length
            if len(self._buffer) < record_size:
                break

            record = bytes(self._buffer[:record_size])
            payloads.append(decode_record(record))
            del self._buffer[:record_size]

        return payloads

    def finish(self) -> None:
        # 到 EOF 时，只要缓冲区还有字节，就说明最后一条记录没收齐
        if self._buffer:
            raise ValueError("incomplete record at EOF")


def encode_record(payload: bytes) -> bytes:
    length = len(payload)  # 计算正文的字节数

    _validate_payload_length(length)

    # to_bytes(...)：把整数 length 按照数值转换成固定4字节的字节串
    # byteorder="big": 采用大端序(高位在前)
    header = length.to_bytes(RECORD_HEADER_BYTES, byteorder="big")

    return header + payload


def decode_record(record: bytes) -> bytes:
    if len(record) < RECORD_HEADER_BYTES:
        raise ValueError("incomplete header")

    header = record[:RECORD_HEADER_BYTES]
    # 通过解析 header 字节串为int来求出 payload 长度
    length = int.from_bytes(header, byteorder="big")

    _validate_payload_length(length)

    if len(record) != RECORD_HEADER_BYTES + length:
        raise ValueError("record payload length does not match header")

    return record[RECORD_HEADER_BYTES:]


def _validate_payload_length(length: int) -> None:
    if length == 0:
        raise ValueError("record payload is empty")

    if length > MAX_MANAGEMENT_MESSAGE_BYTES:
        raise ValueError("record payload is too large")
