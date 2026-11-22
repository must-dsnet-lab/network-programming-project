import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rover.desktop.local_vehicle import LocalVehicleProcess, LocalVehicleStartupError


class SimulatedVehicleProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.code = None
        self.wait_for_eof = True
        self.terminate_called = False

    def poll(self):
        return self.code

    def wait(self, timeout):
        if self.stdin.closed and self.wait_for_eof:
            self.code = 0
        if self.code is None:
            raise subprocess.TimeoutExpired("vehicle", timeout)
        return self.code

    def terminate(self):
        self.terminate_called = True
        self.code = 1


class TestLocalVehicleProcess(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_directory = Path(temporary.name) / "vehicles"
        self.child = SimulatedVehicleProcess()

    def start(self):
        with patch("rover.desktop.local_vehicle.subprocess.Popen", return_value=self.child) as launch:
            vehicle = LocalVehicleProcess.start(41000, self.log_directory)
        self.addCleanup(vehicle.stop)
        return vehicle, launch.call_args

    def test_source_spawn_uses_local_ephemeral_control_and_piped_input(self):
        with patch.object(sys, "frozen", False, create=True):
            vehicle, call = self.start()
        self.assertEqual(
            call.args[0],
            [sys.executable, "-X", "utf8", "-u", "-B", "-m", "rover.vehicle.app",
             "--bind-host", "127.0.0.1", "--telemetry-host", "127.0.0.1",
             "--telemetry-port", "41000", "--auto-control-port"],
        )
        self.assertEqual(call.kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(call.kwargs["stderr"], subprocess.STDOUT)
        self.assertFalse(call.kwargs.get("shell", False))
        self.assertTrue(vehicle.log_path.is_file())
        self.assertEqual(vehicle.log_path.parent, self.log_directory)

    def test_frozen_spawn_dispatches_vehicle_subcommand_in_same_executable(self):
        with patch.object(sys, "frozen", True, create=True):
            _, call = self.start()
        self.assertEqual(call.args[0][:2], [sys.executable, "--vehicle"])

    def test_reads_complete_readiness_line_and_both_ports(self):
        vehicle, _ = self.start()
        self.assertIsNone(vehicle.poll_ready())
        vehicle.log_path.write_bytes(
            b"waiting for UDP\r\nLocal rover ready: control_port=40123 telemetry_source_port=4",
        )
        self.assertIsNone(vehicle.poll_ready())
        with vehicle.log_path.open("ab") as log:
            log.write(b"1234\r\n")
        ready = vehicle.poll_ready()
        self.assertEqual((ready.control_port, ready.telemetry_source_port), (40123, 41234))

    def test_early_exit_reports_code_and_log(self):
        vehicle, _ = self.start()
        self.child.code = 2
        with self.assertRaisesRegex(LocalVehicleStartupError, "2") as failed:
            vehicle.poll_ready()
        self.assertIn(str(vehicle.log_path), str(failed.exception))
        self.assertEqual(vehicle.returncode, 2)

    def test_timeout_closes_owned_child(self):
        with patch("rover.desktop.local_vehicle.time.monotonic", return_value=10.0):
            vehicle, _ = self.start()
        with patch("rover.desktop.local_vehicle.time.monotonic", return_value=30.0):
            with self.assertRaisesRegex(LocalVehicleStartupError, "超时"):
                vehicle.poll_ready()
        self.assertTrue(self.child.stdin.closed)
        self.assertEqual(vehicle.returncode, 0)

    def test_stop_is_idempotent_and_allows_eof_cleanup(self):
        vehicle, _ = self.start()
        vehicle.stop()
        vehicle.stop()
        self.assertTrue(self.child.stdin.closed)
        self.assertFalse(self.child.terminate_called)
        self.assertEqual(vehicle.returncode, 0)

    def test_stop_terminates_if_child_does_not_exit_after_eof(self):
        vehicle, _ = self.start()
        self.child.wait_for_eof = False
        vehicle.stop()
        self.assertTrue(self.child.terminate_called)
        self.assertEqual(vehicle.returncode, 1)

    def test_spawn_failure_identifies_log(self):
        with patch("rover.desktop.local_vehicle.subprocess.Popen", side_effect=OSError("denied")):
            with self.assertRaises(LocalVehicleStartupError) as failed:
                LocalVehicleProcess.start(41000, self.log_directory)
        (log,) = self.log_directory.iterdir()
        self.assertIn(str(log), str(failed.exception))

    def test_rejects_invalid_telemetry_port_before_spawning(self):
        with self.assertRaises(ValueError):
            LocalVehicleProcess.start(0, self.log_directory)
        self.assertFalse(self.log_directory.exists())


if __name__ == "__main__":
    unittest.main()
