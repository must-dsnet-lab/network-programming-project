import json
import unittest

from rover.protocol.telemetry_codec import TelemetrySnapshot, encode_telemetry
from rover.core.vehicle_model import VehicleState
from rover.protocol.telemetry_codec import (
    MAX_TELEMETRY_MESSAGE_BYTES,
    decode_telemetry,
)

class TestTelemetryCodec(unittest.TestCase):
    def test_encode_contains_actual_state_and_protocol_fields(self):
        snapshot = TelemetrySnapshot(
            state=VehicleState(
                x_m=3.0,
                y_m=-2.0,
                heading_rad=0.5,
                speed_mps=1.5,
            ),
            fault_latched=False,
        )

        packet = encode_telemetry(snapshot)

        self.assertIsInstance(packet, bytes)
        message = json.loads(packet.decode("utf-8"))
        self.assertEqual(
            message,
            {
                "version": 1,
                "type": "vehicle_state",
                "x_m": 3.0,
                "y_m": -2.0,
                "heading_rad": 0.5,
                "speed_mps": 1.5,
                "fault_latched": False,
            },
        )

    def test_zero_speed_preserves_fault_status(self):
        for fault_latched in (False, True):
            # subTest 让同一个测试分别检查多组输入，一组失败后仍继续检查其他组
            with self.subTest(fault_latched=fault_latched):
                snapshot = TelemetrySnapshot(
                    state=VehicleState(speed_mps=0.0),
                    fault_latched=fault_latched,
                )

                packet = encode_telemetry(snapshot)
                message = json.loads(packet.decode("utf-8"))

                self.assertIs(
                    message.get("fault_latched"),
                    fault_latched,
                )

    def test_encode_rejects_invalid_motion_values(self):
        cases = (
            {"x_m": float("nan")},
            {"y_m": float("inf")},
            {"speed_mps": True},
            {"heading_rad": 10**400},
        )

        for changes in cases:
            with self.subTest(changes=changes):
                snapshot = TelemetrySnapshot(
                    # 将字典cases每一个键值对changes变成独立参数传给函数，
                    # 例如 {"speed_mps": True} 对应 VehicleState(speed_mps=True)
                    state=VehicleState(**changes),
                    fault_latched=False,
                )

                with self.assertRaises(ValueError):
                    encode_telemetry(snapshot)

    def test_encode_rejects_non_boolean_fault_status(self):
        for invalid in (0, 1, "false"):
            with self.subTest(fault_latched=invalid):
                snapshot = TelemetrySnapshot(
                    state=VehicleState(),
                    fault_latched=invalid,
                )

                with self.assertRaises(ValueError):
                    encode_telemetry(snapshot)

    def test_encode_rejects_oversized_message(self):
        huge = 10**308
        snapshot = TelemetrySnapshot(
            state=VehicleState(
                x_m=huge,
                y_m=huge,
                heading_rad=huge,
                speed_mps=huge,
            ),
            fault_latched=False,
        )

        with self.assertRaises(ValueError):
            encode_telemetry(snapshot)


class TestTelemetryDecoder(unittest.TestCase):
    def setUp(self):
        self.packet = (
            b'{"version":1,"type":"vehicle_state",'
            b'"x_m":3.0,"y_m":-2.0,"heading_rad":0.5,'
            b'"speed_mps":0.0,"fault_latched":true}'
        )
        self.expected = TelemetrySnapshot(
            state=VehicleState(
                x_m=3.0,
                y_m=-2.0,
                heading_rad=0.5,
                speed_mps=0.0,
            ),
            fault_latched=True,
        )

    def test_decode_restores_independent_message(self):
        snapshot = decode_telemetry(self.packet)

        self.assertEqual(snapshot, self.expected)

    def test_decode_rejects_invalid_message_structure(self):
        message = json.loads(self.packet.decode("utf-8"))
        invalid_packets = [b"\xff", b"{", b"[]"]

        for field, value in (
            ("version", True),
            ("version", 2),
            ("type", "control_target"),
        ):
            changed = message.copy()
            changed[field] = value
            invalid_packets.append(json.dumps(changed).encode("utf-8"))

        for field in message:
            incomplete = message.copy()
            del incomplete[field]
            invalid_packets.append(json.dumps(incomplete).encode("utf-8"))

        for index, packet in enumerate(invalid_packets):
            with self.subTest(case=index):
                with self.assertRaises(ValueError):
                    decode_telemetry(packet)

    def test_decode_rejects_invalid_field_values(self):
        message = json.loads(self.packet.decode("utf-8"))

        for field, value in (
            ("x_m", float("nan")),
            ("y_m", float("inf")),
            ("speed_mps", True),
            ("speed_mps", "1.5"),
            ("heading_rad", 10**400),
            ("fault_latched", 0),
            ("fault_latched", "false"),
        ):
            with self.subTest(field=field, value=value):
                changed = message.copy()
                changed[field] = value
                packet = json.dumps(changed).encode("utf-8")

                with self.assertRaises(ValueError):
                    decode_telemetry(packet)

    def test_decode_checks_message_size_boundary(self):
        padding = MAX_TELEMETRY_MESSAGE_BYTES - len(self.packet)
        packet = self.packet + b" " * padding

        self.assertEqual(len(packet), 1200)
        self.assertEqual(
            decode_telemetry(packet),
            self.expected,
        )

        with self.assertRaises(ValueError):
            decode_telemetry(packet + b" ")

    def test_round_trip_preserves_snapshot(self):
        snapshot = TelemetrySnapshot(
            state=VehicleState(
                x_m=-1.0,
                y_m=2.0,
                heading_rad=-0.8,
                speed_mps=-1.5,
            ),
            fault_latched=False,
        )

        restored = decode_telemetry(encode_telemetry(snapshot))

        self.assertEqual(restored, snapshot)


if __name__ == "__main__":
    unittest.main()
