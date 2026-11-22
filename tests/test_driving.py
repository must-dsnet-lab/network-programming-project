import unittest
from dataclasses import replace

from rover.core.driving import (
    BRAKE_DECEL_MPS2,
    COAST_DECEL_MPS2,
    DRIVE_ACCEL_MPS2,
    DRIVE_MAX_SPEED_MPS,
    DriveInput,
    advance_driving,
)
from rover.core.vehicle_model import VehicleState
from rover.vehicle.control_guard import ControlGuardState, update_control_guard
from rover.vehicle.control_watchdog import AcceptedTarget, CONTROL_TIMEOUT_S
from rover.vehicle.vehicle_runtime import step_vehicle


class TestDriving(unittest.TestCase):
    def test_axes_reject_non_numeric_non_finite_and_out_of_range_values(self):
        for values in (
            (True, 0, 0), (0, "1", 0), (0, 0, float("nan")),
            (0, float("inf"), 0), (1e308, 0, 0), (0, 0, 10**500),
            (-0.01, 0, 0), (0, 1.01, 0), (0, 0, -1.01),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                DriveInput(*values)
        self.assertEqual(DriveInput(0, 1, -1).steering, -1.0)

    def test_throttle_accelerates_then_holds_the_speed_cap(self):
        controls = DriveInput(1, 0, 0)
        dt = 0.2
        first = advance_driving(VehicleState(), controls, dt)
        self.assertAlmostEqual(first.speed_mps, DRIVE_ACCEL_MPS2 * dt)
        self.assertAlmostEqual(first.x_m, first.speed_mps * dt / 2)

        state = VehicleState(speed_mps=DRIVE_MAX_SPEED_MPS - 0.1)
        time_to_cap = 0.1 / DRIVE_ACCEL_MPS2
        after = advance_driving(state, controls, dt)
        self.assertEqual(after.speed_mps, DRIVE_MAX_SPEED_MPS)
        self.assertAlmostEqual(after.x_m, DRIVE_MAX_SPEED_MPS * dt - 0.1 * time_to_cap / 2)

    def test_releasing_throttle_coasts_and_brake_takes_priority(self):
        state = VehicleState(speed_mps=1.2)
        coast = advance_driving(state, DriveInput(0, 0, 0), 0.2)
        partial = advance_driving(state, DriveInput(0, 0.5, 0), 0.2)
        after = advance_driving(state, DriveInput(0, 1, 0), 1.0)

        self.assertAlmostEqual(coast.speed_mps, 1.2 - COAST_DECEL_MPS2 * 0.2)
        self.assertGreater(coast.speed_mps, partial.speed_mps)
        self.assertEqual(after.speed_mps, 0.0)
        self.assertAlmostEqual(after.x_m, state.speed_mps**2 / (2 * BRAKE_DECEL_MPS2))
        self.assertEqual(advance_driving(state, DriveInput(1, 1, 0), 1.0), after)
        self.assertEqual(advance_driving(after, DriveInput(0, 1, 0), 1.0), after)

    def test_steering_is_mirrored_speeds_up_with_motion_and_centers(self):
        initial = VehicleState(speed_mps=1.0)
        left = advance_driving(initial, DriveInput(0, 0, 1), 0.2)
        right = advance_driving(initial, DriveInput(0, 0, -1), 0.2)
        slow = advance_driving(replace(initial, speed_mps=0.5), DriveInput(0, 0, 1), 0.2)

        self.assertGreater(left.heading_rad, slow.heading_rad)
        self.assertGreater(slow.heading_rad, 0.0)
        self.assertAlmostEqual(left.heading_rad, -right.heading_rad)
        self.assertAlmostEqual(left.x_m, right.x_m)
        self.assertAlmostEqual(left.y_m, -right.y_m)
        centered = advance_driving(left, DriveInput(0, 0, 0), 0.2)
        self.assertEqual(centered.heading_rad, left.heading_rad)
        parked = VehicleState(x_m=2.5, y_m=0.5, heading_rad=0.2)
        self.assertEqual(advance_driving(parked, DriveInput(0, 0, -1), 1.0), parked)

    def test_step_validation_and_zero_elapsed_time(self):
        controls = DriveInput(1, 0, 0)
        state = VehicleState(speed_mps=1.5, heading_rad=0.25)
        self.assertEqual(advance_driving(state, controls, 0.0), state)
        for invalid in (-0.2, float("inf"), float("nan")):
            with self.subTest(dt=invalid), self.assertRaises(ValueError):
                advance_driving(state, controls, invalid)

    def test_live_intent_advances_but_watchdog_and_revocation_stop_immediately(self):
        state = VehicleState(x_m=2.5, y_m=0.5, heading_rad=0.2, speed_mps=1.0)
        stopped = replace(state, speed_mps=0.0)
        now_s = 100.0
        intent = DriveInput(1, 0, 1)
        guard = update_control_guard(ControlGuardState(), now_s, intent)
        accepted = guard.last_accepted
        self.assertEqual(accepted, AcceptedTarget(intent, now_s))
        moving = step_vehicle(state, accepted, now_s, 0.02)
        self.assertEqual(moving, advance_driving(state, intent, 0.02))

        deadline_s = now_s + CONTROL_TIMEOUT_S
        self.assertEqual(step_vehicle(state, accepted, deadline_s, 0.02), stopped)
        timed_out = update_control_guard(guard, deadline_s)
        self.assertTrue(timed_out.fault_latched)
        self.assertIsNone(timed_out.last_accepted)
        self.assertEqual(step_vehicle(state, timed_out.last_accepted, deadline_s, 0.02), stopped)
        self.assertEqual(step_vehicle(state, None, now_s, 0.02), stopped)
