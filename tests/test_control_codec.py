import json
import unittest

from rover.protocol.control_codec import decode_target, encode_target
from rover.core.control_target import ControlTarget


class TestControlCodec(unittest.TestCase):

    #################### 解码端 ####################
    def test_encode_target_contains_protocol_fields(self):
        target = ControlTarget(speed_mps=2.0, heading_rad=0.5)

        packet = encode_target(target)

        self.assertIsInstance(packet, bytes)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(
            message,
            {
                "version": 1,
                "type": "control_target",
                "speed_mps": 2.0,
                "heading_rad": 0.5,
            },
        )

    def test_decode_target_restores_motion_parameters(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":1.5,"heading_rad":1.2}'
        )

        target = decode_target(packet)

        self.assertEqual(
            target,
            ControlTarget(speed_mps=1.5, heading_rad=1.2),
        )

    def test_round_trip_preserves_reverse_target(self):
        before = ControlTarget(speed_mps=-1.5, heading_rad=-0.8)

        after = decode_target(encode_target(before))

        self.assertEqual(after, before)

    # version 是否为当前支持的版本
    def test_decode_rejects_unsupported_version(self):
        packet = (
            #b代表 bytes（字节串）
            b'{"version":2,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    # type 是否为当前解码器负责的消息类型
    def test_decode_rejects_wrong_message_type(self):
        packet = (
            #b代表 bytes（字节串）
            b'{"version":1,"type":"telemetry",'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    def test_decode_rejects_non_object_message(self):
        with self.assertRaises(Exception) as caught:
            decode_target(b"[]")

        self.assertIsInstance(caught.exception, ValueError)

    def test_decode_rejects_missing_speed(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"heading_rad":0.5}'
        )

        with self.assertRaises(Exception) as caught:
            decode_target(packet)

        self.assertIsInstance(caught.exception, ValueError)

    def test_decode_rejects_missing_heading(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":2.0}'
        )

        with self.assertRaises(Exception) as caught:
            decode_target(packet)

        self.assertIsInstance(caught.exception, ValueError)

    def test_decode_rejects_string_speed(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":"2.0","heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    def test_decode_rejects_boolean_heading(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":true}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    def test_decode_rejects_boolean_version(self):
        packet = (
            b'{"version":true,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    # NaN、无穷大本身就是 float，但计算会产生无效结果或触发异常
    '''对于32位浮点数，IEEE 754 标准规定了三种特殊值：'''
    # 正无穷：0 11111111 00000000000000000000000 (尾数全为0)
    # 负无穷：1 11111111 00000000000000000000000（尾数全为0）
    # NaN  : 0 11111111 10000000000000000000000 (至少一个1在尾数中)

    # python3的int无限精度，所以超大整数转为 float 则会抛出 OverflowError

    # NaN：不是有效的数值结果
    def test_decode_rejects_nan_speed(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":NaN,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    # Infinity：无穷大
    def test_decode_rejects_infinite_heading(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":Infinity}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    # 在数学上有限，但超出浮点表示范围
    def test_decode_rejects_out_of_float_range_speed(self):
        packet = (
            b'{"version":1,"type":"control_target","speed_mps":'
            + b"9" * 400 #Python中，字符串或字节串可以做乘法
            + b',"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_target(packet)

    # JSON文件中穿插的空白字符（空格、换行、制表符）不影响解析
    def test_decode_accepts_message_at_size_limit(self):
        content = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )
        packet = content + b" " * (1200 - len(content))

        target = decode_target(packet)

        self.assertEqual(
            target,
            ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

    def test_decode_rejects_message_over_size_limit(self):
        content = (
            b'{"version":1,"type":"control_target",'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )
        packet = content + b" " * (1201 - len(content))

        with self.assertRaises(ValueError):
            decode_target(packet)

    #################### 编码端 ####################
    def test_encode_rejects_string_speed(self):
        target = ControlTarget(speed_mps="2.0", heading_rad=0.5)

        with self.assertRaises(ValueError):
            encode_target(target)

    def test_encode_rejects_boolean_heading(self):
        target = ControlTarget(speed_mps=2.0, heading_rad=True)

        with self.assertRaises(ValueError):
            encode_target(target)

    def test_encode_rejects_nan_speed(self):
        target = ControlTarget(speed_mps=float("nan"), heading_rad=0.5)

        with self.assertRaises(ValueError):
            encode_target(target)

if __name__ == "__main__":
    unittest.main()
