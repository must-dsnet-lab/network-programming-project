import unittest

from rover.protocol.control_codec import ControlCommand
from rover.vehicle.control_guard import (
    ControlGuardState,
    update_control_guard,
    update_command_guard,
    open_control_session,
    enable_control_session,
    close_control_session,
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
            enabled_at_s=100.0,
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


class TestControlSessionLifecycle(unittest.TestCase):
    def test_open_session_starts_disabled(self):
        before = ControlGuardState()

        opened = open_control_session(
            before,
            session_id="session-a",
        )

        # 新状态登记了会话，但没有自动使能或接受运动目标。
        self.assertEqual(opened.session_id, "session-a")
        self.assertFalse(opened.enabled)
        self.assertIsNone(opened.last_accepted)
        # 尚未接受任何序号。
        self.assertIsNone(opened.last_seq)
        self.assertFalse(opened.fault_latched)
        # 创建新状态后，原来的 before 保持不变
        self.assertIsNone(before.session_id)

    def test_open_session_rejects_replacing_existing_session(self):
        before = ControlGuardState(session_id="session-a")

        with self.assertRaises(ValueError):
            open_control_session(
                before,
                session_id="session-b",
            )

    def test_reopening_same_session_keeps_control_state(self):
        before = ControlGuardState(
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=1.0, heading_rad=0.2),
                accepted_at_s=100.0,
            ),
            session_id="session-a",
            enabled=True,
            last_seq=3,
        )

        opened = open_control_session(before, session_id="session-a")

        self.assertEqual(opened, before)

    # 登记会话与解除故障是两个独立操作
    # 故障未解除时登记会话：保留故障，也不会自动使能
    def test_open_session_preserves_latched_fault(self):
        before = ControlGuardState(fault_latched=True)

        opened = open_control_session(before, session_id="session-a")

        self.assertEqual(opened.session_id, "session-a")
        self.assertTrue(opened.fault_latched)
        self.assertFalse(opened.enabled)
        self.assertIsNone(opened.last_accepted)
        self.assertIsNone(opened.last_seq)

    def test_enable_session_starts_waiting_for_first_command(self):
        before = ControlGuardState(session_id="session-a")

        after = enable_control_session(before, now_s=100.0)

        self.assertTrue(after.enabled)
        self.assertEqual(after.enabled_at_s, 100.0)
        self.assertEqual(after.session_id, "session-a")
        self.assertIsNone(after.last_accepted)
        self.assertIsNone(after.last_seq)
        self.assertFalse(after.fault_latched)

        # 原来的状态保持不变
        self.assertFalse(before.enabled)
        self.assertIsNone(before.enabled_at_s)

    def test_enable_session_rejects_missing_session(self):
        before = ControlGuardState()

        with self.assertRaises(ValueError):
            enable_control_session(before, now_s=100.0)

    def test_enable_session_rejects_latched_fault(self):
        before = ControlGuardState(
            session_id="session-a",
            fault_latched=True,
        )

        with self.assertRaises(ValueError):
            enable_control_session(before, now_s=100.0)

    def test_repeated_enable_keeps_original_start_time(self):
        before = enable_control_session(
            ControlGuardState(session_id="session-a"),
            now_s=100.0,
        )

        after = enable_control_session(before, now_s=100.25)

        self.assertEqual(after.enabled_at_s, 100.0)
        self.assertEqual(after, before)

    def test_first_command_timeout_latches_before_processing_incoming(self):
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        for started_at_s, check_at_s in (
            (100.0, 100.5),
            (100.0, 100.75),
            (0.0, 0.5),
            (0.0, 0.75),
        ):
            for incoming in (None, command):
                with self.subTest(
                    started_at_s=started_at_s,
                    check_at_s=check_at_s,
                    has_incoming=incoming is not None,
                ):
                    guard = enable_control_session(
                        ControlGuardState(session_id="session-a"),
                        now_s=started_at_s,
                    )

                    after = update_command_guard(
                        guard,
                        now_s=check_at_s,
                        incoming=incoming,
                    )

                    self.assertTrue(after.fault_latched)
                    self.assertFalse(after.enabled)
                    self.assertIsNone(after.last_accepted)
                    self.assertIsNone(after.last_seq)
                    self.assertEqual(after.session_id, "session-a")

    def test_waiting_for_first_command_before_timeout_keeps_state(self):
        for started_at_s in (0.0, 100.0):
            with self.subTest(started_at_s=started_at_s):
                before = enable_control_session(
                    ControlGuardState(session_id="session-a"),
                    now_s=started_at_s,
                )

                after = update_command_guard(
                    before,
                    now_s=started_at_s + 0.25,
                )

                self.assertEqual(after, before)

    def test_first_command_switches_timeout_to_acceptance_time(self):
        guard = enable_control_session(
            ControlGuardState(session_id="session-a"),
            now_s=100.0,
        )
        target = ControlTarget(speed_mps=2.0, heading_rad=0.5)
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=target,
        )

        accepted = update_command_guard(
            guard,
            now_s=100.25,  # now_s 是函数参数，不是 ControlGuardState 的字段
            incoming=command,
        )

        self.assertEqual(
            accepted.last_accepted,
            AcceptedTarget(target=target, accepted_at_s=100.25),
        )
        # 还没超时，而且这次没有传入 incoming，所以检查后返回原状态
        still_valid = update_command_guard(accepted, now_s=100.5)
        self.assertEqual(still_valid, accepted)

        expired = update_command_guard(still_valid, now_s=100.75)
        self.assertTrue(expired.fault_latched)
        self.assertFalse(expired.enabled)
        self.assertIsNone(expired.last_accepted)
        self.assertEqual(expired.last_seq, 0)

    def test_enabled_session_without_time_reference_latches_fault(self):
        command = ControlCommand(
            session_id="session-a",
            seq=0,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )

        for incoming in (None, command):
            with self.subTest(has_incoming=incoming is not None):
                guard = ControlGuardState(
                    session_id="session-a",
                    enabled=True,
                    enabled_at_s=None,
                    last_accepted=None,
                )

                after = update_command_guard(
                    guard,
                    now_s=100.0,
                    incoming=incoming,
                )

                self.assertTrue(after.fault_latched)
                self.assertFalse(after.enabled)
                self.assertIsNone(after.last_accepted)
                self.assertIsNone(after.last_seq)
                self.assertEqual(after.session_id, "session-a")

    def test_close_session_revokes_control_and_clears_session(self):
        before = ControlGuardState(
            session_id="session-a",
            enabled=True,
            enabled_at_s=100.0,
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
                accepted_at_s=100.25,
            ),
            last_seq=3,
        )

        closed = close_control_session(before, session_id="session-a")

        self.assertFalse(closed.enabled)
        self.assertIsNone(closed.session_id)
        self.assertIsNone(closed.last_accepted)
        self.assertIsNone(closed.last_seq)
        self.assertIsNone(closed.enabled_at_s)
        self.assertFalse(closed.fault_latched)

        self.assertTrue(before.enabled)
        self.assertEqual(before.session_id, "session-a")

    def test_close_session_rejects_other_session(self):
        before = ControlGuardState(
            session_id="session-b",
            enabled=True,
            enabled_at_s=100.0,
            last_accepted=AcceptedTarget(
                target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
                accepted_at_s=100.25,
            ),
            last_seq=3,
        )

        with self.assertRaises(ValueError):
            close_control_session(before, session_id="session-a")

        self.assertEqual(before.session_id, "session-b")
        self.assertTrue(before.enabled)
        self.assertEqual(before.last_seq, 3)

    # 关闭会话可以清理控制记录，但不能解除故障
    def test_close_session_preserves_latched_fault(self):
        before = ControlGuardState(
            session_id="session-a",
            fault_latched=True,
            enabled_at_s=100.0,
            last_seq=3,
        )

        closed = close_control_session(before, session_id="session-a")

        self.assertTrue(closed.fault_latched)
        self.assertIsNone(closed.session_id)
        self.assertFalse(closed.enabled)
        self.assertIsNone(closed.last_accepted)
        self.assertIsNone(closed.last_seq)
        self.assertIsNone(closed.enabled_at_s)

    # 关闭一次和重复关闭，最终状态相同；不要求返回同一个对象
    def test_repeated_close_keeps_closed_state(self):
        closed_once = close_control_session(
            ControlGuardState(session_id="session-a"),
            session_id="session-a",
        )

        closed_twice = close_control_session(
            closed_once,
            session_id="session-a",
        )

        self.assertEqual(closed_twice, closed_once)
        self.assertIsNone(closed_twice.session_id)

    def test_new_session_requires_enable_and_accepts_zero_sequence(self):
        old_command = ControlCommand(
            session_id="session-a",
            seq=3,
            target=ControlTarget(speed_mps=2.0, heading_rad=0.5),
        )
        new_target = ControlTarget(speed_mps=1.5, heading_rad=1.2)
        new_command = ControlCommand(
            session_id="session-b",
            seq=0,
            target=new_target,
        )

        # A 已使能，并接受过 seq=3
        guard = open_control_session(ControlGuardState(), session_id="session-a")
        guard = enable_control_session(guard, now_s=100.0)
        guard = update_command_guard(guard, now_s=100.0, incoming=old_command)
        self.assertEqual(guard.last_seq, 3)

        # 关闭 A 后，旧包不能恢复控制
        closed = close_control_session(guard, session_id="session-a")
        after_close = update_command_guard(closed, now_s=100.125, incoming=old_command)
        self.assertEqual(after_close, closed)

        # 开启 B，默认未使能且没有旧控制记录
        reopened = open_control_session(after_close, session_id="session-b")
        self.assertEqual(reopened, ControlGuardState(session_id="session-b"))

        before_enable = update_command_guard(
            reopened, now_s=100.25, incoming=new_command
        )
        self.assertEqual(before_enable, reopened)

        # B 使能后，A 的旧包仍然无效
        enabled = enable_control_session(before_enable, now_s=100.25)
        after_old = update_command_guard(enabled, now_s=100.375, incoming=old_command)
        self.assertEqual(after_old, enabled)

        # B 可以接受自己的 seq=0
        accepted = update_command_guard(after_old, now_s=100.5, incoming=new_command)
        self.assertEqual(
            accepted.last_accepted,
            AcceptedTarget(target=new_target, accepted_at_s=100.5),
        )
        self.assertEqual(accepted.last_seq, 0)
        self.assertEqual(accepted.session_id, "session-b")
        self.assertTrue(accepted.enabled)
        self.assertFalse(accepted.fault_latched)


if __name__ == "__main__":
    unittest.main()
