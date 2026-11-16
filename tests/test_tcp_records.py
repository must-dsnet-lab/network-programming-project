import unittest

from rover.protocol.management_codec import MAX_MANAGEMENT_MESSAGE_BYTES
from rover.transport.tcp_records import (
    RECORD_HEADER_BYTES,
    TcpRecordBuffer,
    decode_record,
    encode_record,
)


class TestTcpRecords(unittest.TestCase):

    def test_encode_record_prefixes_payload_length(self):
        payload = b"hello"

        record = encode_record(payload)

        self.assertEqual(record, b"\x00\x00\x00\x05hello")

    def test_encode_record_handles_multibyte_length(self):
        payload = b"x" * 258

        record = encode_record(payload)

        self.assertEqual(record, b"\x00\x00\x01\x02" + payload)

    def test_encode_record_accepts_payload_at_size_limit(self):
        payload = b"x" * MAX_MANAGEMENT_MESSAGE_BYTES

        record = encode_record(payload)

        self.assertEqual(
            len(record),
            RECORD_HEADER_BYTES + MAX_MANAGEMENT_MESSAGE_BYTES,
        )
        self.assertEqual(
            # int.from_bytes()把头部读回整数，算出来的是被测payload长度
            int.from_bytes(record[:RECORD_HEADER_BYTES], byteorder="big"),
            MAX_MANAGEMENT_MESSAGE_BYTES,
        )
        self.assertEqual(record[RECORD_HEADER_BYTES:], payload)

    def test_encode_record_rejects_invalid_payload_lengths(self):
        cases = (
            ("empty", b"", "empty"),
            ("oversized", b"x" * (MAX_MANAGEMENT_MESSAGE_BYTES + 1), "too large"),
        )
        for label, payload, reason in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, reason):
                    encode_record(payload)

    def test_decode_record_returns_payload(self):
        record = b"\x00\x00\x00\x05hello"

        payload = decode_record(record)

        self.assertEqual(payload, b"hello")

    def test_decode_record_rejects_payload_length_mismatch(self):
        cases = (
            ("truncated", b"\x00\x00\x00\x05hell"),
            ("trailing_bytes", b"\x00\x00\x00\x05hello!"),
        )

        for label, record in cases:
            with self.subTest(case=label):
                with self.assertRaises(ValueError):
                    decode_record(record)

    def test_decode_record_rejects_invalid_declared_lengths(self):
        cases = (
            ("empty", b"\x00\x00\x00\x00", "empty"),
            ("oversized", b"\x00\x00\x10\x01", "too large"),
        )

        for label, header, reason in cases:
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, reason):
                    decode_record(header)

    def test_decode_record_rejects_incomplete_header(self):
        header = b"\x00\x00\x01\x02"

        for size in range(RECORD_HEADER_BYTES):
            with self.subTest(header_bytes=size):
                with self.assertRaisesRegex(ValueError, "incomplete header"):
                    decode_record(header[:size])

    def test_decode_record_accepts_valid_payload_lengths(self):
        cases = (
            (b"\x00\x00\x00\x01", 1),
            (b"\x00\x00\x01\x02", 258),
            (b"\x00\x00\x10\x00", MAX_MANAGEMENT_MESSAGE_BYTES),
        )

        for header, size in cases:
            with self.subTest(payload_bytes=size):
                payload = b"x" * size
                self.assertEqual(decode_record(header + payload), payload)

    def test_buffer_collects_split_header_and_payload(self):
        buffer = TcpRecordBuffer()

        self.assertEqual(buffer.feed(b"\x00\x00\x00"), [])
        self.assertEqual(buffer.feed(b"\x05he"), [])
        self.assertEqual(buffer.feed(b"llo"), [b"hello"])

    def test_buffer_returns_multiple_records_from_one_chunk(self):
        buffer = TcpRecordBuffer()
        chunk = b"\x00\x00\x00\x05hello" + b"\x00\x00\x00\x03bye"

        self.assertEqual(buffer.feed(chunk), [b"hello", b"bye"])

    def test_buffer_keeps_partial_next_record(self):
        first = b"\x00\x00\x00\x05hello"
        second = b"\x00\x00\x00\x03bye"

        for split_at in (RECORD_HEADER_BYTES - 1, RECORD_HEADER_BYTES + 1):
            with self.subTest(second_record_bytes=split_at):
                buffer = TcpRecordBuffer()
                self.assertEqual(buffer.feed(first + second[:split_at]), [b"hello"])
                self.assertEqual(buffer.feed(second[split_at:]), [b"bye"])

    def test_buffer_checks_declared_length_when_header_is_complete(self):
        cases = (
            (b"\x00\x00\x00\x00", "empty"),
            (b"\x00\x00\x10\x01", "too large"),
        )

        for header, reason in cases:
            with self.subTest(reason=reason):
                buffer = TcpRecordBuffer()
                self.assertEqual(buffer.feed(header[:-1]), [])
                # 补齐头部的最后一个字节，触发对长度头的完整解析与合法性校验
                with self.assertRaisesRegex(ValueError, reason):
                    buffer.feed(header[-1:])

    def test_buffer_finish_accepts_record_boundary(self):
        buffer = TcpRecordBuffer()
        self.assertEqual(buffer.feed(b"\x00\x00\x00\x05hello"), [b"hello"])
        self.assertIsNone(buffer.finish())

    def test_buffer_finish_rejects_incomplete_record(self):
        cases = (
            ("partial_header", b"\x00\x00\x00"),
            ("partial_payload", b"\x00\x00\x00\x05he"),
        )

        for label, chunk in cases:
            with self.subTest(case=label):
                buffer = TcpRecordBuffer()
                self.assertEqual(buffer.feed(chunk), [])
                self.assertEqual(buffer.feed(b""), [])

                with self.assertRaisesRegex(ValueError, "incomplete record"):
                    buffer.finish()


if __name__ == "__main__":
    unittest.main()
