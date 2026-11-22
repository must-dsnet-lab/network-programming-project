import csv
import unittest
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import rover.vehicle.app as app
from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import (
    ControlCommand,
    encode_authenticated_command,
    encode_expert_command,
    encode_target,
    encode_windowed_command,
)
from rover.vehicle.control_window import ControlWindow
from rover.vehicle.control_authority import ControlAuthority, ControlSession
from rover.vehicle.vehicle_runtime import step_vehicle


class TestWindowedVehicleApp(unittest.TestCase):
    @staticmethod
    def _enabled_expert_authority():
        session = ControlSession("desktop-a", "connection-a", "session-a")
        authority, _ = ControlAuthority(epoch="unit-epoch").acquire(session)
        authority, _ = authority.set_enabled(session, authority.grant.generation, True)
        return authority

    def _run_one_tick(
        self, packet, *, managed=True, window=None, key=b"k" * 32, event_log=None,
        authority=None,
    ):
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
                control_key=key,
                enabled=True,
                enabled_at_s=100.0,
                control_window=window,
            )

        def observe_step(*args):
            state = step_vehicle(*args)
            states.append(state)
            return state

        if authority is not None:
            expert = app.ExpertManagementRuntime({}, operation_timeout_s=1.0)
            expert._control.authority = authority
            management = nullcontext(expert)
        else:
            management = nullcontext(process) if managed else None

        times = [100.0, 100.01, 100.02, 100.03, 100.04, 100.05, 100.06, 100.07]
        with (
            patch.object(app.ExpertManagementRuntime, "__call__", side_effect=process),
            patch.object(app.socket, "socket", side_effect=[control, telemetry]),
            patch.object(
                app,
                "monotonic",
                side_effect=times,
            ),
            patch.object(app, "sleep", side_effect=KeyboardInterrupt),
            patch.object(app, "step_vehicle", side_effect=observe_step),
            patch("builtins.print"),
        ):
            app.main(management=management, event_log=event_log)

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

    def test_expert_mode_rejects_legacy_version_with_current_control_material(self):
        authority = self._enabled_expert_authority()
        command = ControlCommand("session-a", 0, ControlTarget(1.0, 0.0), "window-a")

        with TemporaryDirectory() as temporary:
            event_log = Path(temporary) / "events.csv"
            state = self._run_one_tick(
                encode_windowed_command(command, b"k" * 32),
                window=ControlWindow("window-a", 100.0, 100.25),
                authority=authority,
                event_log=event_log,
            )
            with event_log.open(encoding="utf-8", newline="") as stream:
                reasons = [
                    row["detail"] for row in csv.DictReader(stream)
                    if row["event"] == "control_rejected"
                ]

        self.assertEqual(state.speed_mps, 0.0)
        self.assertEqual(reasons, ["unsupported_control_version"])

    def test_version_three_moves_only_in_expert_mode(self):
        authority = self._enabled_expert_authority()
        command = ControlCommand(
            "session-a", 0, ControlTarget(1.0, 0.0), "window-a",
            generation=authority.grant.generation,
        )
        packet = encode_expert_command(command, b"k" * 32)
        window = ControlWindow("window-a", 100.0, 100.25)

        expert = self._run_one_tick(packet, window=window, authority=authority)
        advanced = self._run_one_tick(packet, window=window)

        self.assertEqual(expert.speed_mps, 1.0)
        self.assertEqual(advanced.speed_mps, 0.0)

    def test_lease_expires_between_management_and_udp_admission(self):
        authority = self._enabled_expert_authority()
        grant = replace(authority.grant, lease_expires_at_s=100.03)
        authority = replace(authority, grant=grant)
        command = ControlCommand(
            "session-a", 0, ControlTarget(1.0, 0.0), "window-a",
            generation=grant.generation,
        )
        with TemporaryDirectory() as temporary:
            event_log = Path(temporary) / "events.csv"
            state = self._run_one_tick(
                encode_expert_command(command, b"k" * 32),
                window=ControlWindow("window-a", 100.0, 100.25),
                authority=authority,
                event_log=event_log,
            )
            with event_log.open(encoding="utf-8", newline="") as stream:
                events = {row["event"]: row for row in csv.DictReader(stream)}

        self.assertEqual(state.speed_mps, 0.0)
        self.assertNotIn("control_accepted", events)
        self.assertEqual(events["authority_lease_expired"]["fault_latched"], "false")
        self.assertEqual(events["authority_lease_expired"]["elapsed_s"], "0.030000")
        self.assertEqual(events["control_rejected"]["detail"], "vehicle_stopping")

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

    def test_missing_key_is_a_secure_only_rejection_event(self):
        command = ControlCommand(
            "session-a", 7, ControlTarget(1.0, 0.0), window_id="window-a"
        )
        packet = encode_windowed_command(command, b"k" * 32)

        with TemporaryDirectory() as temporary:
            for managed, incoming, expected in (
                (True, packet, [("", "missing_session_key")]),
                (False, b"{", []),
            ):
                with self.subTest(managed=managed):
                    event_log = Path(temporary) / f"events-{managed}.csv"
                    state = self._run_one_tick(
                        incoming, managed=managed, key=None, event_log=event_log
                    )
                    self.assertEqual(state.speed_mps, 0.0)
                    with event_log.open(encoding="utf-8", newline="") as stream:
                        rejected = [
                            (row["seq"], row["detail"])
                            for row in csv.DictReader(stream)
                            if row["event"] == "control_rejected"
                        ]
                    self.assertEqual(rejected, expected)

    def test_event_log_records_last_valid_control_and_observed_watchdog_stop(self):
        control = MagicMock()
        control.__enter__.return_value = control
        telemetry = MagicMock()
        telemetry.__enter__.return_value = telemetry
        key = b"k" * 32
        command = ControlCommand(
            "session-a", 7, ControlTarget(1.0, 0.0), window_id="window-a"
        )
        peer = ("127.0.0.1", 12345)
        received = (encode_windowed_command(command, key), peer)
        control.recvfrom.side_effect = [
            received,
            received,
            (b"{", peer),
            (encode_windowed_command(replace(command, seq=8), b"b" * 32), peer),
            BlockingIOError,
        ]

        def process(guard):
            if guard.session_id is not None:
                return guard
            return replace(
                guard,
                session_id="session-a",
                control_key=key,
                enabled=True,
                enabled_at_s=100.0,
                control_window=ControlWindow("window-a", 100.0, 100.3),
            )

        with TemporaryDirectory() as temporary:
            event_log = Path(temporary) / "events.csv"
            with (
                patch.object(app.socket, "socket", side_effect=[control, telemetry]),
                patch.object(app, "monotonic", side_effect=[
                    100.0,
                    100.01, 100.02, 100.03, 100.04,
                    100.10, 100.11, 100.12, 100.13,
                    100.14, 100.15, 100.16, 100.17,
                    100.18, 100.19, 100.20, 100.21,
                    100.53, 100.54, 100.55, 100.56,
                    100.57,
                ]),
                patch.object(app, "sleep", side_effect=[None] * 4 + [KeyboardInterrupt]),
                patch("builtins.print"),
            ):
                app.main(management=nullcontext(process), event_log=event_log)

            with event_log.open(encoding="utf-8", newline="") as stream:
                reader = csv.DictReader(stream)
                events = list(reader)
                self.assertEqual(reader.fieldnames, [
                    "elapsed_s", "event", "seq", "speed_mps", "fault_latched", "detail",
                ])

        self.assertEqual([row["event"] for row in events], [
            "state", "session", "control_accepted", "state",
            "control_rejected", "control_rejected", "control_rejected", "state", "session",
        ])
        self.assertEqual(events[1]["detail"], "opened")
        self.assertEqual(events[-1]["detail"], "closed")
        self.assertEqual(events[2]["seq"], "7")
        self.assertAlmostEqual(float(events[2]["elapsed_s"]), 0.02)
        self.assertEqual(float(events[3]["speed_mps"]), 1.0)
        self.assertEqual([(row["seq"], row["detail"]) for row in events[4:7]], [
            ("7", "sequence_duplicate"),
            ("", "invalid_datagram"),
            ("", "invalid_mac"),
        ])
        self.assertEqual(float(events[7]["speed_mps"]), 0.0)
        self.assertEqual(events[7]["fault_latched"], "true")
        self.assertAlmostEqual(float(events[7]["elapsed_s"]), 0.55)
