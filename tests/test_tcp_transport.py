import socket
import unittest
from unittest.mock import Mock, patch
from rover.transport.tcp_transport import send_record, iter_records
from rover.protocol.management_codec import (
    OpenSessionRequest,
    SessionOpened,
    decode_open_session_request,
    decode_session_opened,
    encode_open_session_request,
    encode_session_opened,
)

class TestTcpTransport(unittest.TestCase):
    def test_send_record_writes_length_and_payload(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)

            with socket.create_connection(
                # .getsockname() 获取 listener 的 IP 和端口
                listener.getsockname(),
                timeout=1.0,
            ) as sender:
                # listener 阻塞等待,一旦 sender 连上来，accept() 返回一个新的 receiver
                receiver, _ = listener.accept()
                with receiver:
                    receiver.settimeout(1.0)
                    send_record(sender, b"hello")

                    # `shutdown(socket.SHUT_WR)` 告诉接收端发送已结束，
                    # 所以测试可以一直读到 EOF，比较收到的完整字节
                    sender.shutdown(socket.SHUT_WR)

                    # 把 TCP 的 Socket 包装成了一个标准的 Python 文件对象。
                    # rb 代表只读的二进制模式。
                    with receiver.makefile("rb") as stream:
                        received = stream.read()

        self.assertEqual(received, b"\x00\x00\x00\x05hello")

    def test_iter_records_yields_payload_before_eof(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)

            with socket.create_connection(
                listener.getsockname(), timeout=1.0
            ) as sender:
                receiver, _ = listener.accept()
                with receiver:
                    receiver.settimeout(1.0)
                    send_record(sender, b"hello")

                    records = iter_records(receiver)
                    # next(records, None) 尝试取第一条正文
                    self.assertEqual(next(records, None), b"hello")

                    sender.shutdown(socket.SHUT_WR)
                    with self.assertRaises(StopIteration):
                        # next(records) 会让生成器一直运行，直到它 yield 一个值、正常结束，或者抛出异常
                        next(records)

    def test_management_request_and_reply_share_connection(self):
        request = OpenSessionRequest(request_id=7)

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)

            with socket.create_connection(  # 客户端连接
                listener.getsockname(), timeout=1.0
            ) as desktop:
                vehicle, _ = listener.accept()  # 服务端接受连接，拿到 vehicle 套接字
                with vehicle:
                    vehicle.settimeout(1.0)
                    # 两端都启动了生成器，并处在“阻塞监听”状态
                    desktop_records = iter_records(desktop)
                    vehicle_records = iter_records(vehicle)

                    send_record(desktop, encode_open_session_request(request))
                    received_request = decode_open_session_request(
                        next(vehicle_records)
                    )
                    self.assertEqual(received_request, request)

                    reply = SessionOpened(
                        request_id=received_request.request_id,
                        session_id="session-a",
                    )
                    send_record(vehicle, encode_session_opened(reply))
                    self.assertEqual(
                        decode_session_opened(next(desktop_records)), reply
                    )

    def test_iter_records_rejects_partial_payload_at_eof(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)

            with socket.create_connection(
                listener.getsockname(), timeout=1.0
            ) as sender:
                receiver, _ = listener.accept()
                with receiver:
                    receiver.settimeout(1.0)
                    sender.sendall(b"\x00\x00\x00\x05he")
                    sender.shutdown(socket.SHUT_WR)

                    with self.assertRaisesRegex(ValueError, "incomplete record"):
                        next(iter_records(receiver))

    def test_iter_records_times_out_waiting_for_more_bytes(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.settimeout(1.0)

            with socket.create_connection(
                listener.getsockname(), timeout=1.0
            ) as sender:
                receiver, _ = listener.accept()
                with receiver:
                    sender.sendall(b"\x00\x00\x00\x05he")
                    receiver.settimeout(0.1)
                    # TimeoutError 是 sock.recv() 抛出的
                    with self.assertRaises(TimeoutError):
                        next(iter_records(receiver))

    def test_iter_records_enforces_total_assembly_timeout(self):
        # iter(...) 使每次调用 next(arrivals) 会吐出下一个元素，并永远记住当前的位置
        arrivals = iter(
            (
                (10.00, b"\x00"),
                (10.08, b"\x00"),
                (10.16, b"\x00\x05he"),
                (10.24, b"llo"),
            )
        )
        now_s = 0.0

        def recv_chunk(_size):
            nonlocal now_s # nonlocal 可修改函数外的 now_s 变量
            now_s, chunk = next(arrivals)
            return chunk

        # 严格模仿真实的 socket 对象
        receiver = Mock(spec=socket.socket)
        # 调用 receiver.gettimeout() 时，Mock 会永远返回 0.1
        receiver.gettimeout.return_value = 0.1
        # 调用 receiver.recv() 时，Mock 不再返回固定值，而是转去执行 recv_chunk
        receiver.recv.side_effect = recv_chunk

        with patch(
            # 每次调用 monotonic() 想要获取当前时间，实际上执行的是 patch 里绑定的 lambda: now_s
            "rover.transport.tcp_transport.monotonic",
            side_effect=lambda: now_s,
        ):
            with self.assertRaisesRegex(TimeoutError, "record assembly timed out"):
                next(iter_records(receiver, record_timeout_s=0.20))

    # 把底层 Socket 的超时时间动态更新缩短
    def test_iter_records_limits_recv_to_remaining_assembly_time(self):
        arrivals = iter(
            (
                (10.00, b"\x00"),
                (10.08, b"\x00"),
                (10.20, None),
            )
        )
        now_s = 0.0
        socket_timeout_s = 1.0
        timeouts_seen = []

        def set_timeout(timeout_s):
            nonlocal socket_timeout_s
            socket_timeout_s = timeout_s

        def recv_chunk(_size):
            nonlocal now_s
            timeouts_seen.append(socket_timeout_s)
            now_s, chunk = next(arrivals)
            if chunk is None:
                raise TimeoutError("timed out")
            return chunk

        receiver = Mock(spec=socket.socket)
        receiver.gettimeout.side_effect = lambda: socket_timeout_s
        receiver.settimeout.side_effect = set_timeout
        receiver.recv.side_effect = recv_chunk

        with patch(
            "rover.transport.tcp_transport.monotonic", side_effect=lambda: now_s
        ):
            with self.assertRaises(TimeoutError):
                next(iter_records(receiver, record_timeout_s=0.20))

        self.assertEqual(len(timeouts_seen), 3)
        self.assertEqual(timeouts_seen[0], 1.0)
        self.assertAlmostEqual(timeouts_seen[1], 0.20)
        self.assertAlmostEqual(timeouts_seen[2], 0.12)
        self.assertEqual(socket_timeout_s, 1.0)
