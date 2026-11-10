import unittest

from trajectory import TrajectoryHistory
from vehicle_model import VehicleState


class TestTrajectoryHistory(unittest.TestCase):
    def test_starts_empty(self):
        history = TrajectoryHistory()

        self.assertEqual(history.points(), ())

    def test_records_world_positions_in_order(self):
        history = TrajectoryHistory()
        history.record(VehicleState(x_m=1.5, y_m=-2.0))
        history.record(VehicleState(x_m=2.5, y_m=3.0))

        self.assertEqual(
            history.points(),
            ((1.5, -2.0), (2.5, 3.0)),
        )

    def test_same_position_is_not_added_twice(self):
        history = TrajectoryHistory()
        history.record(VehicleState(x_m=1.0, y_m=2.0, speed_mps=2.0))
        history.record(
            VehicleState(
                x_m=1.0,
                y_m=2.0,
                heading_rad=1.2,
                speed_mps=0.0,
            )
        )

        self.assertEqual(history.points(), ((1.0, 2.0),))

    def test_return_to_previous_position_is_kept(self):
        history = TrajectoryHistory()
        for x_m in (0.0, 1.0, 0.0):
            history.record(VehicleState(x_m=x_m))

        self.assertEqual(
            history.points(),
            ((0.0, 0.0), (1.0, 0.0), (0.0, 0.0)),
        )

    def test_capacity_keeps_most_recent_positions(self):
        history = TrajectoryHistory(max_points=3)
        for x_m in (0.0, 1.0, 2.0, 3.0, 4.0):
            history.record(VehicleState(x_m=x_m))

        self.assertEqual(
            history.points(),
            ((2.0, 0.0), (3.0, 0.0), (4.0, 0.0)),
        )

    def test_returned_points_are_independent_snapshots(self):
        history = TrajectoryHistory()
        history.record(VehicleState(x_m=1.0, y_m=2.0))
        earlier = history.points()

        history.record(VehicleState(x_m=3.0, y_m=4.0))

        self.assertEqual(earlier, ((1.0, 2.0),))
        self.assertEqual(
            history.points(),
            ((1.0, 2.0), (3.0, 4.0)),
        )

    def test_invalid_capacity_is_rejected(self):
        for max_points in (0, -1, 1.5, True):
            with self.subTest(max_points=max_points):
                with self.assertRaises(ValueError):
                    TrajectoryHistory(max_points=max_points)


if __name__ == "__main__":
    unittest.main()