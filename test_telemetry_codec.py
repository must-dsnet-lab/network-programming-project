import json
import unittest

from telemetry_codec import TelemetrySnapshot, encode_telemetry
from vehicle_model import VehicleState


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


if __name__ == "__main__":
    unittest.main()
