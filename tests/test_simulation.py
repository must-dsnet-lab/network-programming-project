import unittest

from rover.core.vehicle_model import VehicleState
from rover.core.simulation import run_steps


class TestSimulation(unittest.TestCase):
    def test_records_each_step(self):
        initial = VehicleState(speed_mps=2.0)
        states = run_steps(initial, 0.1, 5)

        expected_x = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        self.assertEqual(len(states), len(expected_x))

        for state, x_m in zip(states, expected_x):
            self.assertAlmostEqual(state.x_m, x_m)
            self.assertAlmostEqual(state.y_m, 0.0)

    def test_zero_steps_returns_initial_state(self):
        initial = VehicleState(x_m=3.0, speed_mps=2.0)

        self.assertEqual(
            run_steps(initial, 0.1, 0),
            [initial],
        )

    def test_negative_step_count_is_rejected(self):
        with self.assertRaises(ValueError):
            run_steps(VehicleState(), 0.1, -1)

    def test_negative_time_step_is_rejected_even_without_steps(self):
        with self.assertRaises(ValueError):
            run_steps(VehicleState(), -0.1, 0)


if __name__ == "__main__":
    unittest.main()