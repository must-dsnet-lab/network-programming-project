import unittest
from math import pi

from rover.core.vehicle_model import VehicleState, advance
from rover.core.control_target import ControlTarget, apply_target


class TestControlTarget(unittest.TestCase):
    def test_target_updates_motion_but_not_position(self):
        before = VehicleState(
            x_m=3.0,
            y_m=4.0,
            speed_mps=1.0,
        )
        target = ControlTarget(speed_mps=2.0, heading_rad=pi / 2)

        after = apply_target(before, target)

        self.assertAlmostEqual(after.x_m, 3.0)
        self.assertAlmostEqual(after.y_m, 4.0)
        self.assertAlmostEqual(after.speed_mps, 2.0)
        self.assertAlmostEqual(after.heading_rad, pi / 2)

    def test_zero_speed_target_stops_motion(self):
        before = VehicleState(
            x_m=3.0,
            y_m=4.0,
            speed_mps=2.0,
            heading_rad=pi / 2,
        )
        target = ControlTarget(
            speed_mps=0.0,
            heading_rad=before.heading_rad,
        )

        after = advance(apply_target(before, target), 1.0)

        self.assertAlmostEqual(after.x_m, 3.0)
        self.assertAlmostEqual(after.y_m, 4.0)
        self.assertAlmostEqual(after.speed_mps, 0.0)

    def test_apply_target_keeps_previous_state(self):
        before = VehicleState(speed_mps=1.0, heading_rad=0.2)
        target = ControlTarget(speed_mps=2.0, heading_rad=pi / 2)

        after = apply_target(before, target)

        self.assertAlmostEqual(before.speed_mps, 1.0)
        self.assertAlmostEqual(before.heading_rad, 0.2)
        self.assertIsNot(before, after)


if __name__ == "__main__":
    unittest.main()