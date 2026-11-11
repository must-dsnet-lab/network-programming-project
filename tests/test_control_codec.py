import json
import unittest

from rover.protocol.control_codec import (
    MAX_CONTROL_MESSAGE_BYTES,
    ControlCommand,
    decode_target,
    encode_command,
    encode_target,
    decode_command,
)
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


class TestControlCommandCodec(unittest.TestCase):
    def test_encode_command_contains_session_and_sequence(self):
        command = ControlCommand(
            session_id="session-a",
            seq=12,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        packet = encode_command(command)

        self.assertIsInstance(packet, bytes)
        self.assertEqual(
            json.loads(packet.decode("utf-8")),
            {
                "version": 1,
                "type": "control_command",
                "session_id": "session-a",
                "seq": 12,
                "speed_mps": 2.0,
                "heading_rad": 0.5,
            },
        )

    def test_decode_command_restores_session_sequence_and_target(self):
        packet = (
            b'{"version":1,"type":"control_command",'
            b'"session_id":"session-b","seq":7,'
            b'"speed_mps":1.5,"heading_rad":1.2}'
        )

        self.assertEqual(
            decode_command(packet),
            ControlCommand(
                session_id="session-b",
                seq=7,
                target=ControlTarget(speed_mps=1.5, heading_rad=1.2),
            ),
        )

    def test_round_trip_preserves_reverse_command(self):
        before = ControlCommand(
            session_id=" session-reverse ",
            seq=3,
            target=ControlTarget(speed_mps=-1.5, heading_rad=-0.8),
        )

        packet = encode_command(before)
        after = decode_command(packet)

        self.assertEqual(after, before)

    def test_decode_command_rejects_unsupported_version(self):
        packet = (
            b'{"version":2,"type":"control_command",'
            b'"session_id":"session-a","seq":1,'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_command(packet)

    def test_decode_command_rejects_wrong_message_type(self):
        packet = (
            b'{"version":1,"type":"control_target",'
            b'"session_id":"session-a","seq":1,'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )

        with self.assertRaises(ValueError):
            decode_command(packet)

    def test_decode_command_rejects_non_object_message(self):
        with self.assertRaises(Exception) as caught:
            decode_command(b"[]")

        self.assertIsInstance(caught.exception, ValueError)

    def test_decode_command_rejects_missing_required_fields(self):
        base = {
            "version": 1,
            "type": "control_command",
            "session_id": "session-a",
            "seq": 1,
            "speed_mps": 2.0,
            "heading_rad": 0.5,
        }

        for field in base:
            # subTest()分别记录六个缺字段场景，一个失败后仍继续检查其他场景。
            with self.subTest(field=field):
                # base是完整模板，message是副本，保证每次只缺一个字段。
                message = base.copy()
                del message[field]
                packet = json.dumps(message).encode("utf-8")

                with self.assertRaises(Exception) as caught:
                    decode_command(packet)

                self.assertIsInstance(caught.exception, ValueError)

    def test_decode_command_rejects_invalid_session_or_sequence(self):
        base = {
            "version": 1,
            "type": "control_command",
            "session_id": "session-a",
            "seq": 1,
            "speed_mps": 2.0,
            "heading_rad": 0.5,
        }
        cases = (
            ("session_id", ""),
            ("session_id", "   "),
            ("session_id", 123),
            ("session_id", None),
            ("seq", -1),
            ("seq", True),
            ("seq", 1.0),
            ("seq", "1"),
            ("seq", None),
        )

        for field, value in cases:
            with self.subTest(field=field, value=value):
                message = base.copy()
                message[field] = value
                packet = json.dumps(message).encode("utf-8")

                with self.assertRaises(ValueError):
                    decode_command(packet)

    def test_decode_command_accepts_zero_sequence(self):
        packet = (
            b'{"version":1,"type":"control_command",'
            b'"session_id":"session-a","seq":0,'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )
        command = decode_command(packet)

        self.assertIs(type(command.seq), int)
        self.assertEqual(command.seq, 0)

    def test_decode_command_rejects_invalid_motion_values(self):
        base = {
            "version": 1,
            "type": "control_command",
            "session_id": "session-a",
            "seq": 1,
            "speed_mps": 2.0,
            "heading_rad": 0.5,
        }
        cases = (
            ("string_speed", "speed_mps", "2.0"),
            ("boolean_heading", "heading_rad", True),
            ("null_speed", "speed_mps", None),
            ("nan_speed", "speed_mps", float("nan")),
            ("infinite_heading", "heading_rad", float("inf")),
            ("overflowing_speed", "speed_mps", 10**400),
        )

        for case, field, value in cases:
            with self.subTest(case=case):
                message = base.copy()
                message[field] = value
                packet = json.dumps(message).encode("utf-8")

                with self.assertRaises(ValueError):
                    decode_command(packet)

    def test_decode_command_accepts_integer_motion_values(self):
        packet = (
            b'{"version":1,"type":"control_command",'
            b'"session_id":"session-a","seq":1,'
            b'"speed_mps":2,"heading_rad":0}'
        )
        self.assertEqual(
            decode_command(packet),
            ControlCommand(
                session_id="session-a",
                seq=1,
                target=ControlTarget(speed_mps=2.0, heading_rad=0.0),
            ),
        )

    def test_decode_command_accepts_message_at_size_limit(self):
        content = (
            b'{"version":1,"type":"control_command",'
            b'"session_id":"session-a","seq":1,'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )
        packet = content + b" " * (MAX_CONTROL_MESSAGE_BYTES - len(content))

        self.assertEqual(len(packet), MAX_CONTROL_MESSAGE_BYTES)
        self.assertEqual(
            decode_command(packet),
            ControlCommand("session-a", 1, ControlTarget(2.0, 0.5)),
        )

    def test_decode_command_rejects_message_over_size_limit(self):
        content = (
            b'{"version":1,"type":"control_command",'
            b'"session_id":"session-a","seq":1,'
            b'"speed_mps":2.0,"heading_rad":0.5}'
        )
        packet = content + b" " * (MAX_CONTROL_MESSAGE_BYTES + 1 - len(content))

        self.assertEqual(len(packet), MAX_CONTROL_MESSAGE_BYTES + 1)
        with self.assertRaises(ValueError):
            decode_command(packet)

    # 确保先检查长度，后解析，尤其是对于不合法数据
    def test_decode_command_checks_size_before_decoding(self):
        cases = (
            ("invalid_utf8", b"\xff" * (MAX_CONTROL_MESSAGE_BYTES + 1)),
            ("invalid_json", b"{" * (MAX_CONTROL_MESSAGE_BYTES + 1)),
        )
        for case, packet in cases:
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, "too large"):
                    decode_command(packet)

    def test_encode_command_rejects_invalid_session_or_sequence(self):
        cases = (
            ("empty_session", "", 1),
            ("blank_session", "   ", 1),
            ("numeric_session", 123, 1),
            ("null_session", None, 1),
            ("negative_seq", "session-a", -1),
            ("boolean_seq", "session-a", True),
            ("float_seq", "session-a", 1.0),
            ("string_seq", "session-a", "1"),
            ("null_seq", "session-a", None),
        )

        for case, session_id, seq in cases:
            with self.subTest(case=case):
                command = ControlCommand(
                    session_id=session_id,
                    seq=seq,
                    target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
                )

                with self.assertRaises(ValueError):
                    encode_command(command)

    def test_encode_command_accepts_zero_sequence(self):
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )
        packet = encode_command(command)
        message = json.loads(packet.decode("utf-8"))

        self.assertIs(type(message["seq"]), int)
        self.assertEqual(message["seq"], 0)

    def _command_for_encoded_size(self, size_bytes):
        target = ControlTarget(speed_mps=2.0, heading_rad=0.5)
        base = ControlCommand(session_id="s", seq=1, target=target)
        base_size = len(encode_command(base))

        session_id = "s" * (1 + size_bytes - base_size)
        return ControlCommand(session_id=session_id, seq=1, target=target)

    def test_encode_command_accepts_packet_at_size_limit(self):
        command = self._command_for_encoded_size(MAX_CONTROL_MESSAGE_BYTES)
        packet = encode_command(command)

        self.assertEqual(len(packet), MAX_CONTROL_MESSAGE_BYTES)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(message["session_id"], command.session_id)

    def test_encode_command_rejects_packet_over_size_limit(self):
        command = self._command_for_encoded_size(MAX_CONTROL_MESSAGE_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "too large"):
            encode_command(command)

    # 新入口严格检查版本类型
    def test_decode_command_rejects_non_integer_version(self):
        for version in (True, 1.0):
            with self.subTest(version=version):
                message = {
                    "version": version,
                    "type": "control_command",
                    "session_id": "session-a",
                    "seq": 1,
                    "speed_mps": 2.0,
                    "heading_rad": 0.5,
                }
                packet = json.dumps(message).encode("utf-8")

                with self.assertRaises(ValueError):
                    decode_command(packet)

    # 旧入口不会误收新格式
    def test_legacy_decoder_rejects_control_command(self):
        command = ControlCommand("session-a", 1, ControlTarget(2.0, 0.5))
        packet = encode_command(command)

        with self.assertRaises(ValueError):
            decode_target(packet)

    # 编码端独立拒绝坏运动值
    def test_encode_command_rejects_invalid_motion_values(self):
        cases = (
            ("string_speed", "2.0", 0.5),
            ("boolean_heading", 2.0, True),
            ("null_speed", None, 0.5),
            ("nan_speed", float("nan"), 0.5),
            ("infinite_heading", 2.0, float("inf")),
            ("overflowing_speed", 10**400, 0.5),
        )
        for case, speed_mps, heading_rad in cases:
            with self.subTest(case=case):
                command = ControlCommand(
                    session_id="session-a",
                    seq=1,
                    target=ControlTarget(speed_mps, heading_rad),
                )

                with self.assertRaises(ValueError):
                    encode_command(command)


if __name__ == "__main__":
    unittest.main()
