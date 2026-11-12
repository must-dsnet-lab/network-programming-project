import unittest

from rover.protocol.control_codec import ControlCommand
from rover.vehicle.control_guard import (
    ControlGuardState,
    update_control_guard,
    update_command_guard,
)
from rover.core.control_target import ControlTarget
from rover.vehicle.control_watchdog import AcceptedTarget


class TestControlGuard(unittest.TestCase):
    def test_waits_then_accepts_first_target(self):
        guard = ControlGuardState()

        waiting = update_control_guard(guard, now_s=100.0)

        self.assertIsNone(waiting.last_accepted)
        self.assertFalse(waiting.fault_latched)

        target = ControlTarget(speed_mps=2.0, heading_rad=0.0)
        after = update_control_guard(waiting, now_s=100.1, incoming=target)

        self.assertEqual(
            after.last_accepted,
            AcceptedTarget(target=target, accepted_at_s=100.1),
        )
        self.assertFalse(after.fault_latched)

    def test_fresh_target_refreshes_acceptance_time(self):
        guard = ControlGuardState(
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=2.0, heading_rad=0.0),
                accepted_at_s=100.0,
            )
        )
        target = ControlTarget(speed_mps=1.5, heading_rad=1.2)

        refreshed = update_control_guard(guard, now_s=100.25, incoming=target)

        self.assertEqual(
            refreshed.last_accepted,
            AcceptedTarget(target=target, accepted_at_s=100.25),
        )
        self.assertFalse(refreshed.fault_latched)

        # 没有新目标时，不能刷新接受时间
        idle = update_control_guard(refreshed, now_s=100.5)

        self.assertEqual(idle.last_accepted, refreshed.last_accepted)
        self.assertFalse(idle.fault_latched)

    def test_timeout_latches_and_rejects_later_targets(self):
        guard = ControlGuardState(
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=2.0, heading_rad=0.0),
                accepted_at_s=100.0,
            )
        )
        target = ControlTarget(speed_mps=1.5, heading_rad=0.8)

        # 恰好到期时，即使同时收到新目标，也应先锁存故障
        expired = update_control_guard(guard, now_s=100.5, incoming=target)

        self.assertTrue(expired.fault_latched)
        self.assertIsNone(expired.last_accepted)

        later = update_control_guard(expired, now_s=100.6, incoming=target)

        self.assertTrue(later.fault_latched)
        self.assertIsNone(later.last_accepted)


class TestControlCommandAdmission(unittest.TestCase):
    def test_accepts_first_command_for_enabled_session(self):
        guard = ControlGuardState(
            session_id="session-a",
            enabled=True,
        )
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        after = update_command_guard(
            guard,
            now_s=100.0,
            incoming=command,
        )

        self.assertEqual(
            after.last_accepted,
            AcceptedTarget(
                target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
                accepted_at_s=100.0,
            ),
        )
        self.assertEqual(after.last_seq, 0)
        self.assertEqual(after.session_id, "session-a")
        self.assertTrue(after.enabled)
        self.assertFalse(after.fault_latched)

    def test_rejects_command_when_not_enabled(self):
        # 已登记会话，但enabled默认仍为False
        guard = ControlGuardState(session_id="session-a")
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        after = update_command_guard(
            guard,
            now_s=100.0,
            incoming=command,
        )

        self.assertIsNone(after.last_accepted)
        self.assertIsNone(after.last_seq)
        self.assertEqual(after, guard)

    def test_rejects_other_session_without_refreshing(self):
        previous = AcceptedTarget(
            target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
            accepted_at_s=100.0,
        )
        guard = ControlGuardState(
            last_accepted=previous,
            session_id="session-a",
            enabled=True,
            last_seq=3,
        )
        command = ControlCommand(
            session_id="session-b",
            seq=4,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        after = update_command_guard(
            guard,
            now_s=100.25,
            incoming=command,
        )

        self.assertEqual(after.last_accepted, previous)
        self.assertEqual(after.last_seq, 3)
        self.assertEqual(after, guard)

    def test_accepts_newer_sequence_with_a_gap(self):
        guard = ControlGuardState(
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
                accepted_at_s=100.0,
            ),
            session_id="session-a",
            enabled=True,
            last_seq=3,
        )
        target = ControlTarget(speed_mps=2.0, heading_rad=0.5)
        command = ControlCommand(
            session_id="session-a",
            seq=5,
            target=target,
        )

        after = update_command_guard(
            guard,
            now_s=100.25,
            incoming=command,
        )

        self.assertEqual(
            after.last_accepted,
            AcceptedTarget(target=target, accepted_at_s=100.25),
        )
        self.assertEqual(after.last_seq, 5)

    def test_rejects_duplicate_or_older_sequence(self):
        previous = AcceptedTarget(
            target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
            accepted_at_s=100.0,
        )
        for last_seq, incoming_seq in ((3, 3), (3, 2), (0, 0)):
            with self.subTest(last_seq=last_seq, incoming_seq=incoming_seq):
                guard = ControlGuardState(
                    last_accepted=previous,
                    session_id="session-a",
                    enabled=True,
                    last_seq=last_seq,
                )
                command = ControlCommand(
                    session_id="session-a",
                    seq=incoming_seq,
                    target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
                )

                after = update_command_guard(
                    guard,
                    now_s=100.25,
                    incoming=command,
                )

                self.assertEqual(after.last_accepted, previous)
                self.assertEqual(after.last_seq, last_seq)
                self.assertEqual(after, guard)

    def test_timeout_latches_before_processing_incoming(self):
        command = ControlCommand(
            session_id="session-a",
            seq=4,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )
        for incoming in (None, command):
            with self.subTest(has_incoming=incoming is not None):
                guard = ControlGuardState(
                    last_accepted=AcceptedTarget(
                        target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
                        accepted_at_s=100.0,
                    ),
                    session_id="session-a",
                    enabled=True,
                    last_seq=3,
                )

                after = update_command_guard(
                    guard,
                    now_s=100.5,
                    incoming=incoming,
                )

                self.assertTrue(after.fault_latched)
                self.assertFalse(after.enabled)
                self.assertIsNone(after.last_accepted)
                self.assertEqual(after.session_id, "session-a")
                self.assertEqual(after.last_seq, 3)

    def test_idle_before_timeout_keeps_previous_control(self):
        previous = AcceptedTarget(
            target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
            accepted_at_s=100.0,
        )
        guard = ControlGuardState(
            last_accepted=previous,
            session_id="session-a",
            enabled=True,
            last_seq=3,
        )

        after = update_command_guard(guard, now_s=100.25)

        self.assertEqual(after.last_accepted, previous)
        self.assertEqual(after.last_seq, 3)
        self.assertEqual(after, guard)

    def test_latched_fault_blocks_even_enabled_session(self):
        guard = ControlGuardState(
            fault_latched=True,
            session_id="session-a",
            enabled=True,  # 故意打开，验证故障仍然优先
            last_seq=3,
        )
        command = ControlCommand(
            session_id="session-a",
            seq=4,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        after = update_command_guard(
            guard,
            now_s=100.6,
            incoming=command,
        )

        self.assertTrue(after.fault_latched)
        self.assertIsNone(after.last_accepted)
        self.assertEqual(after.last_seq, 3)


if __name__ == "__main__":
    unittest.main()
