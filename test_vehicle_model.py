import unittest
from math import pi

from vehicle_model import VehicleState, advance


class TestVehicleModel(unittest.TestCase):
    #向右以 2 m/s 运动 0.5 s，到达 (1, 0)
    def test_forward_motion(self):
        before = VehicleState(speed_mps=2.0)
        after = advance(before, 0.5)

        self.assertAlmostEqual(after.x_m, 1.0)
        self.assertAlmostEqual(after.y_m, 0.0)

    #朝向上方(Y轴)运动相同时间，到达 (0, 1)
    def test_heading_changes_direction(self):
        before = VehicleState(
            heading_rad=pi / 2,
            speed_mps=2.0,
        )
        after = advance(before, 0.5)

        self.assertAlmostEqual(after.x_m, 0.0)
        self.assertAlmostEqual(after.y_m, 1.0)

    #时间步长为负数时抛出 ValueError
    def test_negative_time_step_is_rejected(self):
        with self.assertRaises(ValueError):
            advance(VehicleState(), -0.1)


    def test_advance_keeps_previous_state(self):
        before = VehicleState(x_m=3.0, speed_mps=2.0)
        after = advance(before, 0.5)

        self.assertAlmostEqual(before.x_m, 3.0)
        self.assertAlmostEqual(after.x_m, 4.0)
        self.assertIsNot(before, after)   
        
if __name__ == "__main__":
    unittest.main()