import unittest

from control_target import ControlTarget
from control_watchdog import AcceptedTarget, target_for_tick
from vehicle_model import VehicleState


class TestControlWatchdog(unittest.TestCase):
    def test_no_accepted_target_stops_vehicle(self):
        state = VehicleState(speed_mps=1.0, heading_rad=0.7)

        actual = target_for_tick(state, None, now_s=100.0)

        self.assertEqual(
            actual,
            ControlTarget(speed_mps=0.0, heading_rad=0.7),
        )

    def test_target_expires_at_timeout_boundary(self):
        state = VehicleState(speed_mps=2.0, heading_rad=0.8)
        accepted = AcceptedTarget(
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
            accepted_at_s=100.0,
        )
        stop = ControlTarget(speed_mps=0.0, heading_rad=0.8)

        self.assertEqual(
            target_for_tick(state, accepted, now_s=100.499),
            accepted.target,
        )
        self.assertEqual(
            target_for_tick(state, accepted, now_s=100.500),
            stop,
        )
        self.assertEqual(
            target_for_tick(state, accepted, now_s=100.520),
            stop,
        )

    def test_newer_accepted_target_moves_timeout_boundary(self):
        state = VehicleState(speed_mps=2.0, heading_rad=0.8)
        accepted = AcceptedTarget(
            target=ControlTarget(speed_mps=1.5, heading_rad=1.2),
            accepted_at_s=100.25,
        )

        self.assertEqual(
            target_for_tick(state, accepted, now_s=100.50),
            accepted.target,
        )
        self.assertEqual(
            target_for_tick(state, accepted, now_s=100.75),
            ControlTarget(speed_mps=0.0, heading_rad=0.8),
        )


if __name__ == "__main__":
    unittest.main()
