import unittest
from math import pi

from rover.core.control_target import ControlTarget
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.core.vehicle_model import VehicleState
from rover.vehicle.vehicle_runtime import step_vehicle


class TestVehicleRuntime(unittest.TestCase):
    def test_fresh_target_changes_motion_before_advancing(self):
        before = VehicleState(x_m=3.0, y_m=4.0, speed_mps=1.0)
        accepted = AcceptedTarget(
            target=ControlTarget(speed_mps=2.0, heading_rad=pi / 2),
            accepted_at_s=100.0,
        )

        after = step_vehicle(before, accepted, now_s=100.1, dt_s=0.1)

        self.assertAlmostEqual(after.x_m, 3.0)
        self.assertAlmostEqual(after.y_m, 4.2)
        self.assertAlmostEqual(after.speed_mps, 2.0)
        self.assertAlmostEqual(after.heading_rad, pi / 2)

    def test_expired_target_stops_without_moving(self):
        before = VehicleState(
            x_m=3.0,
            y_m=4.0,
            heading_rad=0.7,
            speed_mps=2.0,
        )
        accepted = AcceptedTarget(
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
            accepted_at_s=100.0,
        )

        after = step_vehicle(before, accepted, now_s=100.5, dt_s=0.1)

        self.assertEqual(after.x_m, before.x_m)
        self.assertEqual(after.y_m, before.y_m)
        self.assertEqual(after.heading_rad, before.heading_rad)
        self.assertEqual(after.speed_mps, 0.0)

    def test_no_target_stops_without_moving(self):
        before = VehicleState(
            x_m=3.0,
            y_m=4.0,
            heading_rad=0.7,
            speed_mps=2.0,
        )

        after = step_vehicle(before, None, now_s=100.0, dt_s=0.1)

        self.assertEqual(after.x_m, before.x_m)
        self.assertEqual(after.y_m, before.y_m)
        self.assertEqual(after.heading_rad, before.heading_rad)
        self.assertEqual(after.speed_mps, 0.0)


if __name__ == "__main__":
    unittest.main()
