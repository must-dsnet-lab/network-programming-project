import unittest

from map_geometry import world_to_screen


class TestMapGeometry(unittest.TestCase):
    def test_world_origin_maps_to_canvas_origin(self):
        point = world_to_screen(
            0.0,
            0.0,
            origin_x_px=200.0,
            origin_y_px=150.0,
            pixels_per_meter=50.0,
        )

        self.assertEqual(point, (200.0, 150.0))

    def test_positive_axes_have_correct_directions(self):
        for x_m, y_m, expected in (
            (1.0, 0.0, (250.0, 150.0)),
            (0.0, 1.0, (200.0, 100.0)),
        ):
            with self.subTest(x_m=x_m, y_m=y_m):
                point = world_to_screen(
                    x_m,
                    y_m,
                    origin_x_px=200.0,
                    origin_y_px=150.0,
                    pixels_per_meter=50.0,
                )

                self.assertEqual(point, expected)

    def test_negative_coordinates_respect_scale(self):
        for scale, expected in (
            (50.0, (100.0, 200.0)),
            (25.0, (150.0, 175.0)),
        ):
            with self.subTest(scale=scale):
                point = world_to_screen(
                    -2.0,
                    -1.0,
                    origin_x_px=200.0,
                    origin_y_px=150.0,
                    pixels_per_meter=scale,
                )

                self.assertEqual(point, expected)

    def test_non_positive_scale_is_rejected(self):
        for scale in (0.0, -50.0):
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError):
                    world_to_screen(
                        1.0,
                        1.0,
                        origin_x_px=200.0,
                        origin_y_px=150.0,
                        pixels_per_meter=scale,
                    )


if __name__ == "__main__":
    unittest.main()
