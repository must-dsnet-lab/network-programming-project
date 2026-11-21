import unittest
from dataclasses import replace

from rover.core.control_target import ControlTarget
from rover.protocol.control_codec import ControlCommand
from rover.protocol.management_codec import OpenSessionRequest
from rover.vehicle.control_guard import (
    ControlGuardState,
    close_control_session,
    update_windowed_command_guard,
    update_windowed_command_guard_with_reason,
)
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.vehicle.control_window import ControlWindow
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    handle_open_session,
)


class TestWindowedControlGuard(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState(
            session_id="session-a",
            enabled=True,
            enabled_at_s=100.0,
            last_seq=3,
            last_accepted=AcceptedTarget(ControlTarget(1.0, 0.0), 100.1),
            control_key=b"k" * 32,
            control_window=ControlWindow("window-a", 100.0, 100.25),
        )
        self.command = ControlCommand(
            "session-a",
            4,
            ControlTarget(2.0, 0.5),
            window_id="window-a",
        )

    def test_valid_window_accepts_command(self):
        after = update_windowed_command_guard(
            self.guard, now_s=100.2, incoming=self.command
        )

        self.assertEqual(after.last_seq, 4)
        self.assertEqual(
            after.last_accepted,
            AcceptedTarget(self.command.target, 100.2),
        )
        self.assertIs(after.control_window, self.guard.control_window)
        self.assertEqual(after.enabled_at_s, 100.0)

    def test_missing_or_wrong_window_id_does_not_refresh(self):
        for window_id in (None, "window-old"):
            with self.subTest(window_id=window_id):
                command = replace(self.command, seq=999, window_id=window_id)
                self.assertIs(
                    update_windowed_command_guard(
                        self.guard, now_s=100.2, incoming=command
                    ),
                    self.guard,
                )

    def test_no_active_window_rejects_command(self):
        guard = replace(self.guard, control_window=None)

        self.assertIs(
            update_windowed_command_guard(guard, now_s=100.2, incoming=self.command),
            guard,
        )

    def test_expired_window_does_not_refresh_or_latch_early(self):
        command = replace(self.command, seq=999)

        for now_s in (100.25, 100.3, 100.59):
            with self.subTest(now_s=now_s):
                self.assertIs(
                    update_windowed_command_guard(
                        self.guard, now_s=now_s, incoming=command
                    ),
                    self.guard,
                )

    def test_valid_window_keeps_other_admission_rules(self):
        disabled = replace(self.guard, enabled=False, last_accepted=None, last_seq=None)
        faulted = replace(
            self.guard, fault_latched=True, enabled=False, last_accepted=None
        )
        cases = (
            (
                "other session",
                self.guard,
                replace(self.command, session_id="session-other"),
            ),
            ("duplicate", self.guard, replace(self.command, seq=3)),
            ("older", self.guard, replace(self.command, seq=2)),
            ("disabled", disabled, self.command),
            ("fault", faulted, self.command),
        )

        for label, guard, command in cases:
            with self.subTest(case=label):
                self.assertIs(
                    update_windowed_command_guard(guard, now_s=100.2, incoming=command),
                    guard,
                )

    def test_rejection_reports_the_first_failed_check_without_refreshing_control(self):
        cases = (
            (
                "fault_latched",
                replace(self.guard, fault_latched=True, control_window=None),
                self.command,
                100.2,
            ),
            (
                "window_missing",
                replace(self.guard, enabled=False, control_window=None),
                self.command,
                100.2,
            ),
            (
                "window_mismatch",
                self.guard,
                replace(self.command, window_id="window-other"),
                100.25,
            ),
            ("window_time_invalid", self.guard, self.command, float("nan")),
            (
                "window_not_started",
                replace(self.guard, control_window=ControlWindow("window-a", 100.2, 100.45)),
                self.command,
                100.19,
            ),
            ("window_expired", self.guard, self.command, 100.25),
            (
                "session_disabled",
                replace(self.guard, enabled=False),
                replace(self.command, session_id="session-other"),
                100.2,
            ),
            (
                "session_mismatch",
                self.guard,
                replace(self.command, session_id="session-other", seq=3),
                100.2,
            ),
            ("sequence_duplicate", self.guard, replace(self.command, seq=3), 100.2),
            ("sequence_out_of_order", self.guard, replace(self.command, seq=2), 100.2),
        )

        for expected, guard, command, now_s in cases:
            with self.subTest(reason=expected):
                after, reason = update_windowed_command_guard_with_reason(
                    guard, now_s=now_s, incoming=command
                )
                self.assertIs(after, guard)
                self.assertEqual(reason, expected)

    def test_accepted_command_and_idle_watchdog_do_not_report_rejections(self):
        accepted, accepted_reason = update_windowed_command_guard_with_reason(
            self.guard, now_s=100.2, incoming=self.command
        )
        self.assertEqual(accepted.last_seq, self.command.seq)
        self.assertEqual(accepted.last_accepted, AcceptedTarget(self.command.target, 100.2))
        self.assertIsNone(accepted_reason)

        idle, idle_reason = update_windowed_command_guard_with_reason(
            self.guard, now_s=100.6
        )
        self.assertTrue(idle.fault_latched)
        self.assertIsNone(idle.last_accepted)
        self.assertIsNone(idle_reason)

    def test_idle_still_checks_watchdog(self):
        after = update_windowed_command_guard(self.guard, now_s=100.6)

        self.assertTrue(after.fault_latched)
        self.assertFalse(after.enabled)
        self.assertIsNone(after.last_accepted)
        self.assertEqual(after.last_seq, 3)

    def test_timeout_latches_before_incoming_even_with_fresh_window(self):
        fresh = ControlWindow("window-new", 100.4, 100.65)

        for window in (self.guard.control_window, fresh):
            with self.subTest(window_id=window.window_id):
                guard = replace(self.guard, control_window=window)
                command = replace(self.command, seq=999, window_id=window.window_id)
                after = update_windowed_command_guard(
                    guard, now_s=100.6, incoming=command
                )

                self.assertTrue(after.fault_latched)
                self.assertFalse(after.enabled)
                self.assertIsNone(after.last_accepted)
                self.assertEqual(after.last_seq, 3)

    def test_close_clears_window_and_key_preserving_fault(self):
        guard = replace(
            self.guard, fault_latched=True, enabled=False, last_accepted=None
        )
        closed = close_control_session(guard, "session-a")

        self.assertIsNone(closed.control_window)
        self.assertIsNone(closed.control_key)
        self.assertIsNone(closed.session_id)
        self.assertTrue(closed.fault_latched)

    def test_new_session_does_not_inherit_window(self):
        orphan = ControlGuardState(control_window=self.guard.control_window)
        opened, _, _ = handle_open_session(
            orphan, AuthenticatedConnection("desktop-a"), OpenSessionRequest(7)
        )

        self.assertIsNone(opened.control_window)

    def test_same_connection_retry_keeps_window(self):
        connection = AuthenticatedConnection("desktop-a", "session-a")
        after, same_connection, _ = handle_open_session(
            self.guard, connection, OpenSessionRequest(8)
        )

        self.assertIs(after, self.guard)
        self.assertIs(same_connection, connection)
        self.assertIs(after.control_window, self.guard.control_window)
