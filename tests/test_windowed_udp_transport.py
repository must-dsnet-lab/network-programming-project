import json
import select
import socket
import unittest

from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    ControlCommand,
    encode_authenticated_command,
    encode_windowed_command,
)
from rover.transport.udp_transport import (
    send_windowed_control_command,
    try_receive_windowed_control_command,
)


class TestWindowedUdpTransport(unittest.TestCase):
    def setUp(self):
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.receiver.close)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.setblocking(False)

        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.sender.bind(("127.0.0.1", 0))
        self.key = b"k" * 32
        self.command = ControlCommand(
            "session-a", 0, ControlTarget(1.0, 0.0), window_id="window-a"
        )

    def _send(self, packet):
        self.sender.sendto(packet, self.receiver.getsockname())
        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)

    def test_round_trip_preserves_window_and_peer(self):
        sent = send_windowed_control_command(
            self.sender, self.command, self.receiver.getsockname(), key=self.key
        )
        self.assertGreater(sent, 0)
        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)
        self.assertEqual(
            try_receive_windowed_control_command(self.receiver, key=self.key),
            (self.command, self.sender.getsockname()),
        )

    def test_empty_receiver_returns_none_even_without_key(self):
        for key in (None, self.key):
            with self.subTest(key=key):
                self.assertIsNone(
                    try_receive_windowed_control_command(self.receiver, key=key)
                )

    def test_invalid_packet_does_not_block_next_valid_packet(self):
        valid = encode_windowed_command(self.command, self.key)
        tampered = json.loads(valid)
        tampered["window_id"] = "changed-window"
        packets = (
            encode_windowed_command(self.command, b"wrong-key"),
            json.dumps(tampered).encode("utf-8"),
            encode_authenticated_command(self.command, self.key),
            valid + b" " * (1201 - len(valid)),
        )
        for packet in packets:
            with self.subTest(packet=packet[:60]):
                self._send(packet)
                with self.assertRaises(ValueError):
                    try_receive_windowed_control_command(self.receiver, key=self.key)
                self.assertIsNone(
                    try_receive_windowed_control_command(self.receiver, key=self.key)
                )
                self._send(valid)
                self.assertEqual(
                    try_receive_windowed_control_command(self.receiver, key=self.key),
                    (self.command, self.sender.getsockname()),
                )

    def test_missing_key_consumes_and_rejects_packet(self):
        for key in (None, b""):
            with self.subTest(key=key):
                self._send(encode_windowed_command(self.command, self.key))
                with self.assertRaisesRegex(ValueError, "session key"):
                    try_receive_windowed_control_command(self.receiver, key=key)
                self.assertIsNone(
                    try_receive_windowed_control_command(self.receiver, key=key)
                )

    def test_sender_rejects_empty_key_before_sending(self):
        with self.assertRaisesRegex(ValueError, "key"):
            send_windowed_control_command(
                self.sender, self.command, self.receiver.getsockname(), key=b""
            )
        with self.assertRaises(BlockingIOError):
            self.receiver.recvfrom(65535)
