import unittest

from rover.vehicle.control_authority import (
    AuthorityPhase,
    ControlAuthority,
    ControlSession,
)


class TestControlAuthority(unittest.TestCase):
    alice = ControlSession("desktop-a", "connection-a", "session-a")
    bob = ControlSession("desktop-b", "connection-b", "session-b")

    def setUp(self):
        self.initial = ControlAuthority()
        self.held, reason = self.initial.acquire(self.alice)
        self.assertIsNone(reason)
        self.generation = self.held.grant.generation

    def test_only_one_session_can_acquire_control(self):
        state, reason = self.held.acquire(self.bob)
        self.assertEqual(reason, "authority_busy")
        self.assertEqual(state.grant.session, self.alice)
        self.assertEqual(state.phase, AuthorityPhase.HELD)

    def test_duplicate_acquire_keeps_current_generation(self):
        state, reason = self.held.acquire(self.alice)
        self.assertEqual(reason, "authority_busy")
        self.assertEqual(state.grant.generation, self.generation)

    def test_same_account_on_another_connection_cannot_enable(self):
        other = ControlSession("desktop-a", "connection-new", "session-new")
        self.assertEqual(
            self.held.set_enabled(other, self.generation, True),
            (self.held, "not_authority_holder"),
        )

    def test_enabled_owner_can_disable_without_releasing_authority(self):
        active, reason = self.held.set_enabled(self.alice, self.generation, True)
        self.assertIsNone(reason)
        self.assertEqual(active.phase, AuthorityPhase.CONTROLLING)
        stopped, reason = active.set_enabled(self.alice, self.generation, False)
        self.assertIsNone(reason)
        self.assertEqual(stopped.phase, AuthorityPhase.HELD)
        self.assertEqual(stopped.grant, self.held.grant)

    def test_transfer_revokes_first_and_grants_only_after_stop(self):
        active, _ = self.held.set_enabled(self.alice, self.generation, True)
        stopping, reason = active.transfer(self.alice, self.generation, self.bob)
        self.assertIsNone(reason)
        self.assertEqual(stopping.phase, AuthorityPhase.STOPPING)
        self.assertIsNone(stopping.grant)
        self.assertEqual(
            stopping.set_enabled(self.bob, self.generation, True),
            (stopping, "vehicle_stopping"),
        )

        transferred = stopping.motion_stopped(self.generation)
        self.assertEqual(transferred.phase, AuthorityPhase.HELD)
        self.assertEqual(transferred.grant.session, self.bob)
        self.assertNotEqual(transferred.grant.generation, self.generation)
        self.assertEqual(transferred.disconnect(self.alice), transferred)

    def test_cannot_transfer_to_another_session_on_own_connection(self):
        same_connection = ControlSession("desktop-a", "connection-a", "replacement")
        self.assertEqual(
            self.held.transfer(self.alice, self.generation, same_connection),
            (self.held, "invalid_transfer_target"),
        )

    def test_recipient_disconnect_cancels_pending_grant(self):
        stopping, _ = self.held.transfer(self.alice, self.generation, self.bob)
        stopping = stopping.disconnect(self.bob)
        self.assertEqual(stopping.phase, AuthorityPhase.STOPPING)
        self.assertEqual(stopping.acquire(self.alice), (stopping, "vehicle_stopping"))
        idle = stopping.motion_stopped(self.generation)
        self.assertEqual(idle.phase, AuthorityPhase.IDLE)
        self.assertIsNone(idle.grant)

    def test_release_waits_for_its_own_stop_and_rejects_old_generation(self):
        stopping, reason = self.held.release(self.alice, self.generation)
        self.assertIsNone(reason)
        self.assertEqual(stopping.acquire(self.bob), (stopping, "vehicle_stopping"))

        idle = stopping.motion_stopped(self.generation)
        reacquired, _ = idle.acquire(self.alice)
        self.assertEqual(
            reacquired.set_enabled(self.alice, self.generation, True),
            (reacquired, "stale_generation"),
        )
        stopping_again, _ = reacquired.release(self.alice, reacquired.grant.generation)
        self.assertEqual(stopping_again.motion_stopped(self.generation), stopping_again)

    def test_owner_disconnect_revokes_and_blocks_until_stopped(self):
        stopping = self.held.disconnect(self.alice)
        self.assertEqual(stopping.phase, AuthorityPhase.STOPPING)
        self.assertIsNone(stopping.grant)
        self.assertEqual(stopping.acquire(self.bob), (stopping, "vehicle_stopping"))
        idle = stopping.motion_stopped(self.generation)
        granted, reason = idle.acquire(self.bob)
        self.assertIsNone(reason)
        self.assertEqual(granted.grant.session, self.bob)

    def test_lease_expiry_only_revokes_the_current_generation(self):
        stopping = self.held.lease_expired(self.generation)
        self.assertIsNone(stopping.grant)
        idle = stopping.motion_stopped(self.generation)
        granted, _ = idle.acquire(self.bob)
        self.assertEqual(granted.lease_expired(self.generation), granted)

    def test_fault_cancels_transfer_and_requires_explicit_clear(self):
        stopping, _ = self.held.transfer(self.alice, self.generation, self.bob)
        active, _ = self.held.set_enabled(self.alice, self.generation, True)
        for state in (stopping, active):
            with self.subTest(phase=state.phase):
                fault = state.latch_fault()
                self.assertEqual(fault.phase, AuthorityPhase.FAULT_LATCHED)
                self.assertIsNone(fault.grant)
                self.assertIsNone(fault.pending_holder)
                self.assertEqual(fault.motion_stopped(self.generation), fault)
                self.assertEqual(fault.disconnect(self.alice), fault)
                self.assertEqual(fault.acquire(self.bob), (fault, "fault_latched"))

                idle = fault.fault_cleared()
                self.assertEqual(idle.phase, AuthorityPhase.IDLE)
                self.assertIsNone(idle.grant)

    def test_generation_of_a_new_vehicle_run_does_not_match_old_run(self):
        started_again, _ = ControlAuthority().acquire(self.alice)
        self.assertEqual(
            started_again.set_enabled(self.alice, self.generation, True),
            (started_again, "stale_generation"),
        )
