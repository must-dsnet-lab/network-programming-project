import unittest
from contextlib import nullcontext
from dataclasses import replace
from unittest.mock import MagicMock, patch

import rover.vehicle.app as app
from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    ControlCommand,
    encode_authenticated_command,
    encode_target,
    encode_windowed_command,
)
from rover.vehicle.control_window import ControlWindow
from rover.vehicle.vehicle_runtime import step_vehicle


class TestWindowedVehicleApp(unittest.TestCase):
    def _run_one_tick(self, packet, *, managed=True, window=None):
        control = MagicMock()
        control.__enter__.return_value = control
        control.recvfrom.return_value = (packet, ("127.0.0.1", 12345))
        telemetry = MagicMock()
        telemetry.__enter__.return_value = telemetry
        states = []

        def process(guard):
            return replace(
                guard,
                session_id="session-a",
                control_key=b"k" * 32,
                enabled=True,
                enabled_at_s=100.0,
                control_window=window,
            )

        def observe_step(*args):
            state = step_vehicle(*args)
            states.append(state)
            return state

        management = nullcontext(process) if managed else None
        with (
            patch.object(app.socket, "socket", side_effect=[control, telemetry]),
            patch.object(
                app,
                "monotonic",
                side_effect=[100.0, 100.01, 100.02, 100.03, 100.04],
            ),
            patch.object(app, "sleep", side_effect=KeyboardInterrupt),
            patch.object(app, "step_vehicle", side_effect=observe_step),
            patch("builtins.print"),
        ):
            app.main(management=management)

        return states[0]

    def test_management_mode_accepts_version_two(self):
        command = ControlCommand(
            "session-a", 0, ControlTarget(1.0, 0.0), window_id="window-a"
        )
        state = self._run_one_tick(
            encode_windowed_command(command, b"k" * 32),
            window=ControlWindow("window-a", 100.0, 100.25),
        )
        self.assertEqual(state.speed_mps, 1.0)
        self.assertGreater(state.x_m, 0.0)

    def test_wrong_missing_or_expired_window_cannot_move_vehicle(self):
        command = ControlCommand(
            "session-a", 100, ControlTarget(9.0, 0.0), window_id="window-a"
        )
        packet = encode_windowed_command(command, b"k" * 32)
        for window in (
            None,
            ControlWindow("window-other", 100.0, 100.25),
            ControlWindow("window-a", 99.0, 100.02),
        ):
            with self.subTest(window=window):
                state = self._run_one_tick(packet, window=window)
                self.assertEqual(state.speed_mps, 0.0)
                self.assertEqual(state.x_m, 0.0)

    def test_management_mode_rejects_version_one(self):
        command = ControlCommand("session-a", 0, ControlTarget(9.0, 0.0))
        state = self._run_one_tick(
            encode_authenticated_command(command, b"k" * 32),
            window=ControlWindow("window-a", 100.0, 100.25),
        )
        self.assertEqual(state.speed_mps, 0.0)

    def test_default_udp_demo_still_accepts_target(self):
        state = self._run_one_tick(
            encode_target(ControlTarget(1.0, 0.0)), managed=False
        )
        self.assertEqual(state.speed_mps, 1.0)
