import unittest
from unittest.mock import patch

from scripts.run_rover_desktop import main


class TestWindowsDesktopEntry(unittest.TestCase):
    @patch("rover.desktop.app.main", return_value=0)
    def test_default_opens_launcher_and_explicit_desktop_args_are_preserved(self, desktop):
        self.assertEqual(main([]), 0)
        desktop.assert_called_once_with(["--launcher"])
        desktop.reset_mock()

        self.assertEqual(main(["--connect"]), 0)
        desktop.assert_called_once_with(["--connect"])

    @patch("rover.vehicle.app.cli")
    def test_local_demo_child_accepts_loopback_only(self, vehicle):
        options = [
            "--bind-host", "127.0.0.1", "--telemetry-host", "127.0.0.1",
            "--telemetry-port", "23456", "--auto-control-port",
        ]
        self.assertEqual(main(["--vehicle", *options]), 0)
        vehicle.assert_called_once_with(options)

    @patch("rover.vehicle.app.cli")
    def test_rejects_nonlocal_or_secure_vehicle_options_before_launch(self, vehicle):
        options = [
            "--bind-host", "127.0.0.1", "--telemetry-host", "127.0.0.1",
            "--telemetry-port", "23456", "--auto-control-port",
        ]
        for invalid in ([*options, "--secure"], [*options[:3], "0.0.0.0", *options[4:]],
                        [*options[:5], "65536", *options[6:]]):
            with self.subTest(invalid=invalid), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as error:
                    main(["--vehicle", *invalid])
                self.assertEqual(error.exception.code, 2)
        vehicle.assert_not_called()
