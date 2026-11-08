import json
import select
import socket
import unittest

from telemetry_codec import (
    MAX_TELEMETRY_MESSAGE_BYTES,
    TelemetrySnapshot,
)
from udp_transport import send_telemetry, try_receive_telemetry
from vehicle_model import VehicleState


class TestUdpTelemetry(unittest.TestCase):
    def setUp(self):
        self.receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.receiver.close)
        self.receiver.bind(("127.0.0.1", 0))
        self.receiver.setblocking(False)

        self.sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(self.sender.close)
        self.sender.bind(("127.0.0.1", 0))

        self.snapshot = TelemetrySnapshot(
            state=VehicleState(
                x_m=3.0,
                y_m=-2.0,
                heading_rad=0.5,
                speed_mps=0.0,
            ),
            fault_latched=True,
        )
        self.packet = (
            b'{"version":1,"type":"vehicle_state",'
            b'"x_m":3.0,"y_m":-2.0,"heading_rad":0.5,'
            b'"speed_mps":0.0,"fault_latched":true}'
        )

    def _wait_for_packet(self):
        ready, _, _ = select.select([self.receiver], [], [], 1.0)
        self.assertIn(self.receiver, ready)

    def test_send_telemetry_sends_expected_datagram(self):
        sent = send_telemetry(
            self.sender,
            self.snapshot,
            self.receiver.getsockname(),
        )

        self.assertGreater(sent, 0)
        self._wait_for_packet()
        packet, peer = self.receiver.recvfrom(65535)

        self.assertEqual(
            json.loads(packet.decode("utf-8")),
            json.loads(self.packet.decode("utf-8")),
        )
        self.assertEqual(sent, len(packet))
        self.assertEqual(peer, self.sender.getsockname())

    def test_receive_distinguishes_idle_from_valid_snapshot(self):
        self.assertIsNone(try_receive_telemetry(self.receiver))

        self.sender.sendto(self.packet, self.receiver.getsockname())
        self._wait_for_packet()

        self.assertEqual(
            try_receive_telemetry(self.receiver),
            (self.snapshot, self.sender.getsockname()),
        )
        self.assertIsNone(try_receive_telemetry(self.receiver))

    def test_rejected_packet_does_not_prevent_next_reception(self):
        oversized = self.packet + b" " * (
            MAX_TELEMETRY_MESSAGE_BYTES + 1 - len(self.packet)
        )

        for name, packet in (
            ("invalid_json", b"{"),
            ("oversized", oversized),
        ):
            with self.subTest(case=name):
                self.sender.sendto(packet, self.receiver.getsockname())
                self._wait_for_packet()

                with self.assertRaises(ValueError):
                    try_receive_telemetry(self.receiver)

                self.sender.sendto(self.packet, self.receiver.getsockname())
                self._wait_for_packet()

                self.assertEqual(
                    try_receive_telemetry(self.receiver),
                    (self.snapshot, self.sender.getsockname()),
                )

    def test_closed_socket_errors_are_not_hidden(self):
        self.receiver.close()

        with self.subTest(operation="send"):
            with self.assertRaises(OSError):
                send_telemetry(
                    self.receiver,
                    self.snapshot,
                    self.sender.getsockname(),
                )

        with self.subTest(operation="receive"):
            with self.assertRaises(OSError):
                try_receive_telemetry(self.receiver)


if __name__ == "__main__":
    unittest.main()
