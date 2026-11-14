import json
import unittest

from rover.protocol.management_codec import (
    MAX_MANAGEMENT_MESSAGE_BYTES,
    OpenSessionRequest,
    SessionOpened,
    encode_open_session_request,
    decode_open_session_request,
    encode_session_opened,
    decode_session_opened,
)


class TestManagementCodec(unittest.TestCase):
    def test_encode_open_session_request_contains_protocol_fields(self):
        request = OpenSessionRequest(request_id=7)

        packet = encode_open_session_request(request)

        self.assertIsInstance(packet, bytes)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(
            message,
            {
                "version": 1,
                "type": "open_session",
                "request_id": 7,
            },
        )

    def test_decode_open_session_request_restores_request_id(self):
        packet = b'{"version":1,"type":"open_session","request_id":9}'

        request = decode_open_session_request(packet)

        self.assertEqual(
            request,
            OpenSessionRequest(request_id=9),
        )

    def test_encode_session_opened_contains_protocol_fields(self):
        reply = SessionOpened(
            request_id=7,
            session_id="session-a",
        )

        packet = encode_session_opened(reply)

        self.assertIsInstance(packet, bytes)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(
            message,
            {
                "version": 1,
                "type": "session_opened",
                "request_id": 7,
                "session_id": "session-a",
            },
        )

    def test_decode_session_opened_restores_both_ids(self):
        packet = (
            b'{"version":1,"type":"session_opened",'
            b'"request_id":11,"session_id":"session-b"}'
        )

        reply = decode_session_opened(packet)

        self.assertEqual(
            reply,
            SessionOpened(request_id=11, session_id="session-b"),
        )

    def test_decoders_reject_invalid_versions(self):
        cases = (
            (
                decode_open_session_request,
                {
                    "version": 1,
                    "type": "open_session",
                    "request_id": 7,
                },
            ),
            (
                decode_session_opened,
                {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": 7,
                    "session_id": "session-a",
                },
            ),
        )

        for decoder, valid_message in cases:
            for bad_version in (2, True, 1.0):
                # subTest()：如果某次循环测试失败，不会直接抛出异常结束，而是继续执行循环直到遍历完所有测试用例
                # key=value 是子测试的标识符，用于在测试报告中定位具体的失败原因
                with self.subTest(decoder=decoder.__name__, version=bad_version):
                    message = valid_message.copy()
                    message["version"] = bad_version
                    packet = json.dumps(message).encode("utf-8")

                    with self.assertRaises(ValueError):
                        # 第一次循环：decoder 是 decode_open_session_request()
                        # 第二次循环：decoder 是 decode_session_opened()
                        decoder(packet)

    def test_decoders_reject_wrong_message_types(self):
        cases = (
            (
                decode_open_session_request,
                {
                    "version": 1,
                    "type": "open_session",
                    "request_id": 7,
                },
                "session_opened",
            ),
            (
                decode_session_opened,
                {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": 7,
                    "session_id": "session-a",
                },
                "open_session",
            ),
        )

        for decoder, valid_message, other_type in cases:
            for wrong_type in (other_type, "control_command"):
                with self.subTest(
                    decoder=decoder.__name__,
                    message_type=wrong_type,
                ):
                    message = valid_message.copy()
                    message["type"] = wrong_type
                    packet = json.dumps(message).encode("utf-8")

                    with self.assertRaises(ValueError):
                        decoder(packet)

    def test_decoders_reject_non_object_messages(self):
        for decoder in (decode_open_session_request, decode_session_opened):
            for packet in (b"[]", b"null", b"true", b"123", b'"text"'):
                with self.subTest(decoder=decoder.__name__, packet=packet):
                    with self.assertRaises(Exception) as caught:
                        decoder(packet)
                    self.assertIsInstance(caught.exception, ValueError)

    def test_decoders_reject_missing_required_fields(self):
        cases = (
            (
                decode_open_session_request,
                {
                    "version": 1,
                    "type": "open_session",
                    "request_id": 7,
                },
            ),
            (
                decode_session_opened,
                {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": 7,
                    "session_id": "session-a",
                },
            ),
        )

        for decoder, valid_message in cases:
            for missing_field in valid_message:
                with self.subTest(
                    decoder=decoder.__name__,
                    missing=missing_field,
                ):
                    message = valid_message.copy()
                    del message[missing_field]
                    packet = json.dumps(message).encode("utf-8")

                    with self.assertRaises(Exception) as caught:
                        decoder(packet)
                    self.assertIsInstance(caught.exception, ValueError)

    def test_codecs_reject_invalid_request_ids(self):
        for bad_id in (-1, True, False, 1.0, "7", None):
            request_packet = json.dumps(
                {
                    "version": 1,
                    "type": "open_session",
                    "request_id": bad_id,
                }
            ).encode("utf-8")

            reply_packet = json.dumps(
                {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": bad_id,
                    "session_id": "session-a",
                }
            ).encode("utf-8")

            cases = (
                (encode_open_session_request, OpenSessionRequest(request_id=bad_id)),
                (
                    encode_session_opened,
                    SessionOpened(
                        request_id=bad_id,
                        session_id="session-a",
                    ),
                ),
                (decode_open_session_request, request_packet),
                (decode_session_opened, reply_packet),
            )

            for operation, value in cases:
                with self.subTest(
                    operation=operation.__name__,
                    request_id=bad_id,
                ):
                    with self.assertRaises(ValueError):
                        operation(value)

    def test_zero_request_id_is_preserved(self):
        cases = (
            (encode_open_session_request, OpenSessionRequest(request_id=0)),
            (
                encode_session_opened,
                SessionOpened(
                    request_id=0,
                    session_id="session-a",
                ),
            ),
        )

        for encoder, value in cases:
            with self.subTest(operation=encoder.__name__):
                packet = encoder(value)
                self.assertIsInstance(packet, bytes)

                message = json.loads(packet.decode("utf-8"))
                self.assertIs(type(message["request_id"]), int)
                self.assertEqual(message["request_id"], 0)

        cases = (
            (
                decode_open_session_request,
                b'{"version":1,"type":"open_session","request_id":0}',
            ),
            (
                decode_session_opened,
                b'{"version":1,"type":"session_opened",'
                b'"request_id":0,"session_id":"session-a"}',
            ),
        )

        for decoder, packet in cases:
            with self.subTest(operation=decoder.__name__):
                value = decoder(packet)
                self.assertIs(type(value.request_id), int)
                self.assertEqual(value.request_id, 0)

    def test_session_opened_codecs_reject_invalid_session_ids(self):
        for bad_id in ("", "   ", "\t\n", 7, 1.5, True, None, [], {}):
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": 7,
                    "session_id": bad_id,
                }
            ).encode("utf-8")

            cases = (
                (encode_session_opened, SessionOpened(request_id=7, session_id=bad_id)),
                (decode_session_opened, packet),
            )

            for operation, value in cases:
                with self.subTest(
                    operation=operation.__name__,
                    session_id=bad_id,
                ):
                    with self.assertRaises(ValueError):
                        operation(value)

    def test_session_opened_codecs_preserve_session_id(self):
        session_id = "  session-a  "
        reply = SessionOpened(request_id=7, session_id=session_id)

        packet = encode_session_opened(reply)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(message["session_id"], session_id)

        packet = (
            b'{"version":1,"type":"session_opened",'
            b'"request_id":7,"session_id":"  session-a  "}'
        )
        reply = decode_session_opened(packet)
        self.assertEqual(reply.session_id, session_id)

    def test_decoders_accept_message_at_size_limit(self):
        cases = (
            (
                decode_open_session_request,
                b'{"version":1,"type":"open_session","request_id":7}',
            ),
            (
                decode_session_opened,
                b'{"version":1,"type":"session_opened",'
                b'"request_id":7,"session_id":"session-a"}',
            ),
        )

        for decoder, content in cases:
            with self.subTest(decoder=decoder.__name__):
                packet = content + b" " * (MAX_MANAGEMENT_MESSAGE_BYTES - len(content))
                self.assertEqual(len(packet), MAX_MANAGEMENT_MESSAGE_BYTES)
                self.assertEqual(decoder(packet).request_id, 7)

    def test_decoders_reject_oversized_messages_before_parsing(self):
        cases = (
            (
                decode_open_session_request,
                b'{"version":1,"type":"open_session","request_id":7}',
            ),
            (
                decode_session_opened,
                b'{"version":1,"type":"session_opened",'
                b'"request_id":7,"session_id":"session-a"}',
            ),
        )
        size = MAX_MANAGEMENT_MESSAGE_BYTES + 1

        for decoder, content in cases:
            packets = (
                ("valid_json", content + b" " * (size - len(content))),
                ("invalid_utf8", b"\xff" * size),
                ("invalid_json", b"{" * size),
            )

            for label, packet in packets:
                with self.subTest(decoder=decoder.__name__, case=label):
                    self.assertEqual(len(packet), size)
                    with self.assertRaisesRegex(ValueError, "too large"):
                        decoder(packet)

    def _management_cases_for_encoded_size(self, size_bytes):
        request_message = {
            "version": 1,
            "type": "open_session",
            "request_id": 0,
        }
        base_size = len(json.dumps(request_message).encode("utf-8"))
        request_message["request_id"] = 10 ** (size_bytes - base_size)
        request = OpenSessionRequest(
            request_id=request_message["request_id"],
        )

        reply_message = {
            "version": 1,
            "type": "session_opened",
            "request_id": 7,
            "session_id": "s",
        }
        base_size = len(json.dumps(reply_message).encode("utf-8"))
        reply_message["session_id"] = "s" * (1 + size_bytes - base_size)
        reply = SessionOpened(
            request_id=7,
            session_id=reply_message["session_id"],
        )

        return (
            (encode_open_session_request, request, request_message),
            (encode_session_opened, reply, reply_message),
        )

    def test_encoders_accept_packet_at_size_limit(self):
        size = MAX_MANAGEMENT_MESSAGE_BYTES
        for encoder, value, expected in self._management_cases_for_encoded_size(size):
            with self.subTest(encoder=encoder.__name__, size=size):
                self.assertEqual(len(json.dumps(expected).encode("utf-8")), size)

                packet = encoder(value)
                self.assertIsInstance(packet, bytes)
                self.assertEqual(len(packet), size)
                self.assertEqual(json.loads(packet.decode("utf-8")), expected)

    def test_encoders_reject_packet_over_size_limit(self):
        size = MAX_MANAGEMENT_MESSAGE_BYTES + 1
        for encoder, value, expected in self._management_cases_for_encoded_size(size):
            with self.subTest(encoder=encoder.__name__, size=size):
                self.assertEqual(len(json.dumps(expected).encode("utf-8")), size)

                with self.assertRaisesRegex(ValueError, "too large"):
                    encoder(value)

    # 非法 UTF-8、空报文、不完整 JSON 等输入会被拒绝
    # 拒绝之后，下一次独立调用仍能正常解码合法报文
    def test_decoders_reject_malformed_messages_and_allow_next_call(self):
        cases = (
            (
                decode_open_session_request,
                b'{"version":1,"type":"open_session","request_id":9}',
                OpenSessionRequest(request_id=9),
            ),
            (
                decode_session_opened,
                b'{"version":1,"type":"session_opened",'
                b'"request_id":11,"session_id":"session-b"}',
                SessionOpened(request_id=11, session_id="session-b"),
            ),
        )
        bad_packets = (
            ("empty", b""),
            ("invalid_utf8", b"\xff"),
            ("truncated_utf8", b"\xe4\xb8"),
            ("incomplete_json", b"{"),
            ("extra_json", b"{} {}"),
            ("deep_incomplete_json", b"[" * 1500),
        )

        for decoder, valid_packet, expected in cases:
            for label, packet in bad_packets:
                with self.subTest(decoder=decoder.__name__, case=label):
                    self.assertLessEqual(len(packet), MAX_MANAGEMENT_MESSAGE_BYTES)
                    with self.assertRaises(ValueError):
                        decoder(packet)

                    self.assertEqual(decoder(valid_packet), expected)


if __name__ == "__main__":
    unittest.main()
