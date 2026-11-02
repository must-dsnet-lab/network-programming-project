import json
import unittest

from control_codec import decode_target, encode_target
from control_target import ControlTarget


class TestControlCodec(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()