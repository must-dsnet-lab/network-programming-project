import unittest

from rover.vehicle.control_window import (
    ControlWindow,
    control_window_rejection_reason,
    is_control_window_valid,
    issue_control_window,
)


class TestControlWindow(unittest.TestCase):
    def test_issue_uses_vehicle_time(self):
        window = issue_control_window("window-a", now_s=100.0)

        self.assertEqual(
            window,
            ControlWindow("window-a", 100.0, 100.25),
        )

    def test_accepts_matching_window_before_expiry(self):
        window = ControlWindow("window-a", 100.0, 100.25)

        for now_s in (100.0, 100.125, 100.249):
            with self.subTest(now_s=now_s):
                self.assertTrue(
                    is_control_window_valid(window, "window-a", now_s)
                )

    def test_rejects_at_and_after_expiry(self):
        window = ControlWindow("window-a", 100.0, 100.25)

        for now_s in (100.25, 100.5):
            with self.subTest(now_s=now_s):
                self.assertFalse(
                    is_control_window_valid(window, "window-a", now_s)
                )

    def test_rejects_missing_wrong_or_invalid_window_time(self):
        window = ControlWindow("window-a", 100.0, 100.25)
        cases = (
            (None, "window-a", 100.1),
            (window, "window-other", 100.1),
            (window, "window-a", 99.9),
            (window, "window-a", float("nan")),
            (window, "window-a", float("inf")),
            (window, "window-a", True),
            (window, "window-a", "100.1"),
        )

        for current, window_id, now_s in cases:
            with self.subTest(window_id=window_id, now_s=now_s):
                self.assertFalse(
                    is_control_window_valid(current, window_id, now_s)
                )

    def test_custom_lifetime_does_not_slide(self):
        window = issue_control_window(
            "window-a", now_s=100.0, lifetime_s=0.125
        )

        self.assertTrue(is_control_window_valid(window, "window-a", 100.12))
        self.assertFalse(is_control_window_valid(window, "window-a", 100.125))
        self.assertEqual(window.expires_at_s, 100.125)

    def test_reports_why_a_window_check_failed(self):
        window = ControlWindow("window-a", 100.0, 100.25)
        cases = (
            (None, "window-a", 100.1, "window_missing"),
            (window, "window-other", 100.1, "window_mismatch"),
            (window, "window-a", float("nan"), "window_time_invalid"),
            (window, "window-a", 99.9, "window_not_started"),
            (window, "window-a", 100.0, None),
            (window, "window-a", 100.249, None),
            (window, "window-a", 100.25, "window_expired"),
            (window, "window-a", 100.5, "window_expired"),
            (ControlWindow("window-a", float("nan"), 100.25),
             "window-a", 100.1, "window_time_invalid"),
            (ControlWindow("window-a", 100.0, float("nan")),
             "window-a", 100.1, "window_time_invalid"),
        )

        for current, window_id, now_s, expected in cases:
            with self.subTest(window_id=window_id, now_s=now_s, reason=expected):
                self.assertEqual(
                    control_window_rejection_reason(current, window_id, now_s),
                    expected,
                )

    def test_issue_rejects_invalid_parameters(self):
        cases = (
            ("", 100.0, 0.25),
            (7, 100.0, 0.25),
            ("window-a", True, 0.25),
            ("window-a", float("nan"), 0.25),
            ("window-a", float("inf"), 0.25),
            ("window-a", 100.0, 0),
            ("window-a", 100.0, -0.25),
            ("window-a", 100.0, float("nan")),
            ("window-a", 100.0, float("inf")),
            ("window-a", 1e308, 1e308),
        )

        for window_id, now_s, lifetime_s in cases:
            with self.subTest(
                window_id=window_id,
                now_s=now_s,
                lifetime_s=lifetime_s,
            ):
                with self.assertRaises(ValueError):
                    issue_control_window(
                        window_id, now_s, lifetime_s=lifetime_s
                    )
