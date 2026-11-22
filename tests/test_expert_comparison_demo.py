import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from examples import demo_expert_comparison as demo
from rover.core.control_target import ControlTarget


class TestExpertComparisonDemo(unittest.TestCase):
    def test_shared_trial_stops_cleanly_then_checks_watchdog_and_recovery(self):
        for expert in (False, True):
            with self.subTest(expert=expert):
                worker = Mock()
                worker.status.return_value = SimpleNamespace(
                    state="ready", authority=SimpleNamespace(allowed_actions={"reset_control"}),
                )
                telemetry = object()
                checkpoints = []
                with (
                    patch.object(demo, "_wait_for_telemetry") as observe,
                    patch.object(demo, "_acquire") as acquire,
                ):
                    demo._exercise(worker, telemetry, expert, lambda: True, checkpoints)

                self.assertEqual(observe.call_args_list, [
                    call(worker, telemetry, 0.0, False),
                    call(worker, telemetry, 1.0, False),
                    call(worker, telemetry, 0.0, False),
                    call(worker, telemetry, 1.0, False),
                    call(worker, telemetry, 0.0, True),
                    call(worker, telemetry, 0.0, False),
                    call(worker, telemetry, 1.0, False),
                    call(worker, telemetry, 0.0, False),
                ])
                self.assertEqual([item.args[0] for item in worker.apply_target.call_args_list], [
                    ControlTarget(speed, 0.0) for speed in (1.0, 0.0, 1.0, 1.0, 0.0)
                ])
                worker.stop_sending.assert_called_once_with()
                worker.reset_control.assert_called_once_with()
                self.assertEqual(acquire.call_count, 2 if expert else 0)
                self.assertIn("zero_latched_with_tls", checkpoints)
                self.assertIn("reset_and_reapply", checkpoints)

    def test_transport_close_cannot_pass_as_watchdog_zero(self):
        worker = Mock()
        worker.status.return_value = SimpleNamespace(state="ready")
        checkpoints = []
        with (
            patch.object(demo, "_wait_for_telemetry"),
            self.assertRaisesRegex(RuntimeError, "TLS connection closed"),
        ):
            demo._exercise(worker, object(), False, lambda: False, checkpoints)

        worker.reset_control.assert_not_called()
        self.assertNotIn("zero_latched_with_tls", checkpoints)

    def test_vehicle_command_uses_loopback_and_expert_flag_only_for_expert(self):
        for expert in (False, True):
            with self.subTest(expert=expert):
                argv = demo._vehicle_args(expert, 39001, 39002, 39003, "certs", "trusted.json", "events.csv")
                self.assertIn("rover.vehicle.app", argv)
                self.assertIn("--secure", argv)
                self.assertEqual("--expert-management" in argv, expert)
                for option in ("--bind-host", "--telemetry-host"):
                    self.assertEqual(argv[argv.index(option) + 1], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
