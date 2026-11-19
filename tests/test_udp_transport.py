import socket
import unittest
import json
import select
from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    ControlCommand,
    decode_target,
    encode_authenticated_command,
    encode_command,
    encode_target,
)
from rover.transport.udp_transport import (
    receive_target,
    send_authenticated_control_command,
    send_control_command,
    send_target,
    try_receive_authenticated_control_command,
    try_receive_control_command,
    try_receive_target,
)

class TestUdpTransport(unittest.TestCase):
    # setUp() 每个测试前运行一次
    def setUp(self):
        # AF_INET、SOCK_DGRAM：创建 IPv4 UDP Socket
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # addCleanup()：系统会在测试结束后自动调用 self.receiver.close()，确保资源释放
        self.addCleanup(self.receiver.close)
        # 127.0.0.1：本机回环地址(网络包不出网卡)；绑定端口 0，让操作系统分配空闲端口
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.settimeout(1.0)  # 阻塞只持续1秒，避免测试卡死

        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        # 为了测试的确定性，实际情况中sender不需要bind()
        self.sender.bind(("127.0.0.1", 0))

    def test_send_target_sends_valid_datagram(self):
        target = ControlTarget(speed_mps=2.0, heading_rad=0.5)

        sent = send_target(
            self.sender,
            target,
            # getsockname()：取得实际绑定的地址和端口
            self.receiver.getsockname(),
        )

        self.assertGreater(sent, 0)  # 大于0，表示发送了数据

        # recvfrom() 接收数据并返回来源地址
        packet, peer = self.receiver.recvfrom(65535)

        self.assertEqual(decode_target(packet), target)
        self.assertEqual(sent, len(packet))
        self.assertEqual(peer, self.sender.getsockname())

    def test_receive_target_restores_message(self):
        target = ControlTarget(speed_mps=1.5, heading_rad=1.2)
        self.sender.sendto(  # sendto() 发送数据
            encode_target(target),
            self.receiver.getsockname(),
        )

        received, peer = receive_target(self.receiver)

        self.assertEqual(received, target)
        self.assertEqual(peer, self.sender.getsockname())

    def test_receive_target_rejects_invalid_message(self):
        self.sender.sendto(b"[]", self.receiver.getsockname())

        with self.assertRaises(ValueError):
            receive_target(self.receiver)

    def test_try_receive_target_returns_none_without_data(self):
        self.receiver.setblocking(False)

        received = try_receive_target(self.receiver)

        self.assertIsNone(received)

    # 函数在“非阻塞”模式下，能够成功接收来自 UDP 网络的数据，并将其正确解码还原为原始对象
    def test_try_receive_target_restores_message(self):
        self.receiver.setblocking(False)  # 无数据时，底层 recvfrom() 抛出 BlockingIOError
        target = ControlTarget(speed_mps=1.5, heading_rad=1.2)
        self.sender.sendto(
            encode_target(target),
            self.receiver.getsockname(),  # 获取当前 Socket 绑定在本机的 IP 和 端口
        )

        # select(监控读的列表, 监控写的列表, 监控异常的列表, 超时时间)
        ready, _, _ = select.select([self.receiver], [], [], 1.0) #超时且没有可读数据时，ready 是 []
        self.assertIn(self.receiver, ready)  # 断言 self.receiver 一定在 ready 里

        received = try_receive_target(self.receiver)

        self.assertEqual(
            received,
            (target, self.sender.getsockname()),
        )

    def test_try_receive_target_rejects_invalid_message(self):
        self.receiver.setblocking(False)
        self.sender.sendto(b"[]", self.receiver.getsockname())

        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)

        with self.assertRaises(ValueError):
            try_receive_target(self.receiver)

    def test_control_command_round_trip(self):
        self.receiver.setblocking(False)
        command = ControlCommand("session-a", 0, ControlTarget(2.0, 0.5))

        sent = send_control_command(
            self.sender, command, self.receiver.getsockname(),
        )
        self.assertEqual(sent, len(encode_command(command)))

        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)
        self.assertEqual(
            try_receive_control_command(self.receiver),
            (command, self.sender.getsockname()),
        )

    def test_try_receive_control_command_returns_none_when_empty(self):
        self.receiver.setblocking(False)
        self.assertIsNone(try_receive_control_command(self.receiver))

    def test_try_receive_control_command_rejects_legacy_target(self):
        self.receiver.setblocking(False)
        self.sender.sendto(
            encode_target(ControlTarget(2.0, 0.5)),
            self.receiver.getsockname(),
        )
        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)
        with self.assertRaisesRegex(ValueError, "unexpected message type"):
            try_receive_control_command(self.receiver)

    _auth_key = b"test-session-key"
    _auth_command = ControlCommand("session-a", 0, ControlTarget(1.0, 0.5))

    def _wait_for_packet(self):
        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)

    def test_send_authenticated_command_writes_mac(self):
        sent = send_authenticated_control_command(
            self.sender,
            self._auth_command,
            self.receiver.getsockname(),
            key=self._auth_key,
        )
        packet, peer = self.receiver.recvfrom(65535)

        self.assertEqual(
            packet,
            encode_authenticated_command(self._auth_command, self._auth_key),
        )
        self.assertEqual(sent, len(packet))
        self.assertEqual(peer, self.sender.getsockname())

    def test_receive_authenticated_command(self):
        self.receiver.setblocking(False)
        packet = encode_authenticated_command(self._auth_command, self._auth_key)
        self.sender.sendto(packet, self.receiver.getsockname())
        self._wait_for_packet()

        self.assertEqual(
            try_receive_authenticated_control_command(
                self.receiver, key=self._auth_key
            ),
            (self._auth_command, self.sender.getsockname()),
        )

    def test_authenticated_receiver_returns_none_when_empty(self):
        self.receiver.setblocking(False)

        for key in (None, self._auth_key):
            with self.subTest(key=key):
                self.assertIsNone(
                    try_receive_authenticated_control_command(self.receiver, key=key)
                )

    def test_bad_packet_does_not_block_next_valid_packet(self):
        self.receiver.setblocking(False)
        valid = encode_authenticated_command(self._auth_command, self._auth_key)
        changed = json.loads(valid)
        changed["speed_mps"] = 9.0

        cases = (
            (
                "wrong key",
                encode_authenticated_command(self._auth_command, b"wrong-key"),
                "MAC",
            ),
            ("unsigned", encode_command(self._auth_command), "MAC"),
            ("tampered", json.dumps(changed).encode("utf-8"), "MAC"),
            ("oversized", valid + b" " * (1201 - len(valid)), "too large"),
        )

        for label, packet, reason in cases:
            with self.subTest(case=label):
                self.sender.sendto(packet, self.receiver.getsockname())
                self._wait_for_packet()

                with self.assertRaisesRegex(ValueError, reason):
                    try_receive_authenticated_control_command(
                        self.receiver, key=self._auth_key
                    )

                self.assertIsNone(
                    try_receive_authenticated_control_command(
                        self.receiver, key=self._auth_key
                    )
                )

                self.sender.sendto(valid, self.receiver.getsockname())
                self._wait_for_packet()
                self.assertEqual(
                    try_receive_authenticated_control_command(
                        self.receiver, key=self._auth_key
                    ),
                    (self._auth_command, self.sender.getsockname()),
                )

    def test_missing_session_key_consumes_and_rejects_packet(self):
        self.receiver.setblocking(False)
        packet = encode_authenticated_command(self._auth_command, self._auth_key)

        for key in (None, b""):
            with self.subTest(key=key):
                self.sender.sendto(packet, self.receiver.getsockname())
                self._wait_for_packet()

                with self.assertRaisesRegex(ValueError, "session key"):
                    try_receive_authenticated_control_command(self.receiver, key=key)

                self.assertIsNone(
                    try_receive_authenticated_control_command(self.receiver, key=key)
                )

    def test_authenticated_sender_rejects_empty_key(self):
        self.receiver.setblocking(False)

        with self.assertRaisesRegex(ValueError, "key"):
            send_authenticated_control_command(
                self.sender,
                self._auth_command,
                self.receiver.getsockname(),
                key=b"",
            )

        with self.assertRaises(BlockingIOError):
            self.receiver.recvfrom(65535)


if __name__ == "__main__":
    unittest.main()
