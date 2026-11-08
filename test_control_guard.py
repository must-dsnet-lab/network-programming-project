import unittest

from control_guard import ControlGuardState, update_control_guard
from control_target import ControlTarget
from control_watchdog import AcceptedTarget


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


if __name__ == "__main__":
    unittest.main()