import socket
import unittest

from control_codec import decode_target, encode_target
from control_target import ControlTarget
from udp_transport import receive_target, send_target


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


if __name__ == "__main__":
    unittest.main()
