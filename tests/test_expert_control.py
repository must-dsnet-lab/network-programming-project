from dataclasses import replace
import unittest

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.protocol.control_codec import ControlCommand
from rover.vehicle.control_authority import AuthorityGeneration, ControlSession
from rover.vehicle.control_guard import ControlGuardState, update_expert_command_guard_with_reason
from rover.vehicle.control_watchdog import CONTROL_TIMEOUT_S
from rover.vehicle.expert_control import ExpertVehicleControl


class TestExpertVehicleControl(unittest.TestCase):
    def setUp(self):
        self.control = ExpertVehicleControl()
        self.guard = ControlGuardState()
        self.a = ControlSession("desktop-a", "connection-a", "session-a")
        self.b = ControlSession("desktop-b", "connection-b", "session-b")

    def dispatch(self, session, message_type, *, recipient=None, now_s=1.0, **fields):
        self.guard, result = self.control.dispatch(
            self.guard, session,
            {"type": message_type, "session_id": session.session_id, **fields},
            recipient=recipient, now_s=now_s,
        )
        return result

    def acquire(self, session):
        reply = self.dispatch(session, "acquire_authority")
        self.assertTrue(reply["accepted"])
        self.assertEqual(reply["phase"], "held")
        return reply["generation"]

    def enable(self, session, generation):
        reply = self.dispatch(session, "set_authority_enabled", generation=generation, enabled=True)
        self.assertTrue(reply["accepted"])
        self.assertEqual(reply["phase"], "controlling")
        self.assertEqual(bytes.fromhex(reply["control_key"]), self.guard.control_key)
        self.assertEqual(len(self.guard.control_key), 32)
        return reply["control_key"]

    def grant_window(self, session, generation):
        reply = self.dispatch(session, "issue_control_window", generation=generation, now_s=1.1)
        self.assertTrue(reply["accepted"])
        self.assertEqual(reply["window_id"], self.guard.control_window.window_id)
        self.assertAlmostEqual(reply["lifetime_s"], 0.25)
        return reply["window_id"]

    def simulate_command(self, session, window):
        packet = ControlCommand(
            session.session_id, 0, ControlTarget(1.0, 0.0), window,
            generation=self.control.authority.grant.generation,
        )
        self.guard, reason = update_expert_command_guard_with_reason(
            self.guard, 1.2, packet, authority=self.control.authority,
        )
        self.assertIsNone(reason)
        return packet

    def observe(self, speed_mps, *, now_s=1.3):
        self.guard = self.control.observe_vehicle(
            self.guard, VehicleState(speed_mps=speed_mps), now_s=now_s,
        )

    def assert_revoked(self):
        self.assertFalse(self.guard.enabled)
        for field in ("last_accepted", "last_seq", "control_key", "control_window", "enabled_at_s"):
            self.assertIsNone(getattr(self.guard, field), field)

    def assert_rejected(self, reply, reason):
        self.assertFalse(reply["accepted"])
        self.assertEqual(reply["reason"], reason)
        self.assertNotIn("control_key", reply)

    def test_only_current_holder_can_renew_before_the_deadline(self):
        generation = self.acquire(self.a)
        deadline = self.control.authority.grant.lease_expires_at_s
        self.assertEqual(deadline, 4.0)

        self.assert_rejected(
            self.dispatch(self.b, "renew_authority", generation=generation, now_s=3.5),
            "not_authority_holder",
        )
        self.assert_rejected(
            self.dispatch(self.a, "renew_authority", generation={**generation, "number": 99}, now_s=3.5),
            "stale_generation",
        )
        self.assertEqual(self.control.authority.grant.lease_expires_at_s, deadline)

        reply = self.dispatch(self.a, "renew_authority", generation=generation, now_s=3.75)
        self.assertTrue(reply["accepted"])
        self.assertEqual(reply["lease_remaining_s"], 3.0)
        self.assertEqual(self.control.authority.grant.lease_expires_at_s, 6.75)
        self.guard = self.control.advance_time(self.guard, 4.0)
        self.assertEqual(self.control.authority.grant.generation, AuthorityGeneration(**generation))
        self.assertFalse(self.guard.enabled)

    def test_renew_at_deadline_revokes_and_late_retry_cannot_restore_grant(self):
        generation = self.acquire(self.a)
        for now_s in (4.0, 4.01):
            with self.subTest(now_s=now_s):
                self.assert_rejected(
                    self.dispatch(self.a, "renew_authority", generation=generation, now_s=now_s),
                    "lease_expired",
                )
                self.assertIsNone(self.control.authority.grant)
                self.assertFalse(self.guard.fault_latched)
        self.observe(0.0, now_s=4.02)
        reacquired = self.dispatch(self.a, "acquire_authority", now_s=4.03)
        self.assertTrue(reacquired["accepted"])
        self.assertGreater(reacquired["generation"]["number"], generation["number"])
        self.assert_rejected(
            self.dispatch(self.a, "renew_authority", generation=generation, now_s=4.04),
            "lease_expired",
        )
        self.assertFalse(self.guard.enabled)

    def test_accepted_control_and_window_do_not_extend_lease(self):
        generation = self.acquire(self.a)
        self.assertTrue(self.dispatch(
            self.a, "set_authority_enabled", generation=generation, enabled=True, now_s=3.8,
        )["accepted"])
        window = self.dispatch(self.a, "issue_control_window", generation=generation, now_s=3.8)
        command = ControlCommand(
            self.a.session_id, 0, ControlTarget(1.0, 0.0), window["window_id"],
            generation=AuthorityGeneration(**generation),
        )
        self.guard, rejection = update_expert_command_guard_with_reason(
            self.guard, 3.9, command, authority=self.control.authority,
        )
        self.assertIsNone(rejection)
        self.assertIsNotNone(self.guard.last_accepted)

        self.guard = self.control.advance_time(self.guard, 4.0)
        self.assert_revoked()
        self.assertFalse(self.guard.fault_latched)
        self.assertEqual(self.control.authority.phase, "stopping")
        self.assertEqual(self.control.last_lease_expiry.revoked_at_s, 4.0)

    def test_watchdog_takes_precedence_when_both_deadlines_are_due(self):
        generation = self.acquire(self.a)
        self.assertTrue(self.dispatch(
            self.a, "set_authority_enabled", generation=generation, enabled=True, now_s=3.5,
        )["accepted"])
        self.guard = self.control.advance_time(self.guard, 4.0)
        self.assertTrue(self.guard.fault_latched)
        self.assert_revoked()
        self.assertEqual(self.control.authority.phase, "fault_latched")
        self.assertIsNone(self.control.last_lease_expiry)

    def test_transferred_lease_starts_when_vehicle_observes_zero(self):
        generation = self.acquire(self.a)
        self.assertTrue(self.dispatch(
            self.a, "transfer_authority", generation=generation, recipient=self.b, now_s=3.9,
        )["accepted"])
        self.observe(0.5, now_s=4.25)
        self.assertIsNone(self.control.authority.grant)
        self.observe(0.0, now_s=5.0)
        grant = self.control.authority.grant
        self.assertEqual(grant.session, self.b)
        self.assertEqual(grant.lease_expires_at_s, 8.0)
        self.assertFalse(self.guard.enabled)

    def test_only_the_holder_with_current_generation_can_enable_or_get_window(self):
        generation = self.acquire(self.a)
        self.assertEqual(generation["number"], 1)
        self.assert_revoked()
        self.assert_rejected(self.dispatch(self.b, "acquire_authority"), "authority_busy")
        self.assert_rejected(
            self.dispatch(self.b, "set_authority_enabled", generation=generation, enabled=True),
            "not_authority_holder",
        )
        self.assert_rejected(
            self.dispatch(self.a, "issue_control_window", generation=generation), "session_disabled",
        )
        self.assert_rejected(
            self.dispatch(self.a, "set_authority_enabled", generation={**generation, "number": 2}, enabled=True),
            "stale_generation",
        )
        self.enable(self.a, generation)
        self.grant_window(self.a, generation)

    def test_repeat_enable_preserves_material_and_disable_retires_it(self):
        generation = self.acquire(self.a)
        key = self.enable(self.a, generation)
        window = self.grant_window(self.a, generation)
        self.simulate_command(self.a, window)
        before = self.guard
        reply = self.dispatch(self.a, "set_authority_enabled", generation=generation, enabled=True, now_s=1.3)
        self.assertEqual(reply["control_key"], key)
        self.assertEqual(self.guard, before)
        reply = self.dispatch(self.a, "set_authority_enabled", generation=generation, enabled=False, now_s=1.4)
        self.assertEqual(reply["phase"], "held")
        self.assertNotIn("control_key", reply)
        self.assert_revoked()
        self.assertNotEqual(self.enable(self.a, generation), key)

    def test_transfer_revokes_first_then_grants_only_after_zero_speed(self):
        generation = self.acquire(self.a)
        previous_key = self.enable(self.a, generation)
        packet = self.simulate_command(self.a, self.grant_window(self.a, generation))
        self.observe(1.0)
        reply = self.dispatch(self.a, "transfer_authority", generation=generation, recipient=self.b, now_s=1.3)
        self.assertTrue(reply["accepted"])
        self.assertEqual(reply["phase"], "stopping")
        self.assert_revoked()
        self.assertIsNone(self.control.authority.grant)
        self.observe(0.01)
        self.assertIsNone(self.control.authority.grant)
        self.observe(0.0)
        grant = self.control.authority.grant
        self.assertEqual(grant.session, self.b)
        self.assertEqual(grant.generation.number, generation["number"] + 1)
        self.guard = self.control.disconnect(self.guard, self.a)
        self.assertEqual(self.control.authority.grant, grant)
        self.assert_revoked()
        new_generation = {"epoch": grant.generation.epoch, "number": grant.generation.number}
        self.assertNotEqual(self.enable(self.b, new_generation), previous_key)
        self.grant_window(self.b, new_generation)
        next_guard, reason = update_expert_command_guard_with_reason(
            self.guard, 1.2, packet, authority=self.control.authority,
        )
        self.assertIs(next_guard, self.guard)
        self.assertEqual(reason, "not_authority_holder")

    def test_expert_admission_checks_generation_window_and_sequence_without_refreshing(self):
        earlier = self.acquire(self.a)
        self.assertTrue(self.dispatch(self.a, "release_authority", generation=earlier)["accepted"])
        self.observe(0.0)
        generation = self.acquire(self.a)
        self.enable(self.a, generation)
        first = self.simulate_command(self.a, self.grant_window(self.a, generation))

        for incoming, expected in (
            (replace(first, seq=1, generation=AuthorityGeneration(**earlier)), "stale_generation"),
            (replace(first, seq=1, generation=AuthorityGeneration("earlier-epoch", generation["number"])), "stale_generation"),
            (replace(first, seq=1, window_id="old-window"), "window_mismatch"),
            (first, "sequence_duplicate"),
        ):
            with self.subTest(expected=expected, incoming=incoming):
                next_guard, reason = update_expert_command_guard_with_reason(
                    self.guard, 1.25, incoming, authority=self.control.authority,
                )
                self.assertEqual(reason, expected)
                self.assertIs(next_guard, self.guard)

        next_guard, reason = update_expert_command_guard_with_reason(
            self.guard, 1.25, replace(first, seq=1), authority=self.control.authority,
        )
        self.assertIsNone(reason)
        self.assertEqual(next_guard.last_seq, 1)
        self.assertEqual(next_guard.last_accepted.accepted_at_s, 1.25)

    def test_expert_admission_applies_watchdog_before_command(self):
        generation = self.acquire(self.a)
        self.enable(self.a, generation)
        first = self.simulate_command(self.a, self.grant_window(self.a, generation))

        next_guard, reason = update_expert_command_guard_with_reason(
            self.guard, 1.201 + CONTROL_TIMEOUT_S, replace(first, seq=1),
            authority=self.control.authority,
        )

        self.assertEqual(reason, "fault_latched")
        self.assertTrue(next_guard.fault_latched)
        self.assertIsNone(next_guard.last_accepted)

    def test_candidate_disconnect_during_stop_leaves_authority_idle(self):
        generation = self.acquire(self.a)
        self.assert_rejected(
            self.dispatch(self.a, "transfer_authority", generation=generation), "invalid_transfer_target",
        )
        self.assertTrue(self.dispatch(self.a, "transfer_authority", generation=generation, recipient=self.b)["accepted"])
        self.guard = self.control.disconnect(self.guard, self.b)
        self.assert_revoked()
        self.observe(0.0)
        self.assertEqual(self.control.authority.phase, "idle")
        self.assertIsNone(self.control.authority.grant)

    def test_release_and_owner_disconnect_each_revoke_control_immediately(self):
        for action in ("release_authority", "disconnect"):
            with self.subTest(action=action):
                generation = self.acquire(self.a)
                self.enable(self.a, generation)
                self.simulate_command(self.a, self.grant_window(self.a, generation))
                if action == "disconnect":
                    self.guard = self.control.disconnect(self.guard, self.a)
                else:
                    self.assertTrue(self.dispatch(self.a, action, generation=generation, now_s=1.3)["accepted"])
                self.assertEqual(self.control.authority.phase, "stopping")
                self.assert_revoked()
                self.assert_rejected(self.dispatch(self.b, "acquire_authority", now_s=1.3), "vehicle_stopping")
                self.observe(0.0)
                self.assertEqual(self.control.authority.phase, "idle")

    def test_emergency_stop_and_reset_require_holder_then_observed_zero_speed(self):
        generation = self.acquire(self.a)
        self.enable(self.a, generation)
        self.simulate_command(self.a, self.grant_window(self.a, generation))
        self.observe(1.0)
        self.assert_rejected(self.dispatch(self.b, "emergency_stop", generation=generation), "not_authority_holder")
        reply = self.dispatch(self.a, "emergency_stop", generation=generation)
        self.assertEqual(reply["phase"], "fault_latched")
        self.assertTrue(self.guard.fault_latched)
        self.assert_revoked()
        self.assert_rejected(self.dispatch(self.b, "reset_control"), "vehicle_not_stopped")
        self.observe(0.0)
        self.assertTrue(self.dispatch(self.b, "reset_control")["accepted"])
        self.assertFalse(self.guard.fault_latched)
        self.assertIsNone(self.control.authority.grant)
        self.assertEqual(self.control.authority.phase, "idle")
        self.assert_revoked()

    def test_watchdog_timeout_blocks_management_before_it_can_renew_window(self):
        generation = self.acquire(self.a)
        self.enable(self.a, generation)
        reply = self.dispatch(self.a, "issue_control_window", generation=generation, now_s=1.0 + CONTROL_TIMEOUT_S)
        self.assert_rejected(reply, "fault_latched")
        self.assertTrue(self.guard.fault_latched)
        self.assertIsNone(self.control.authority.grant)
        self.assert_revoked()

    def test_external_fault_during_stop_discards_pending_transfer(self):
        generation = self.acquire(self.a)
        self.assertTrue(self.dispatch(self.a, "transfer_authority", generation=generation, recipient=self.b)["accepted"])
        self.guard = self.control.sync_fault(replace(self.guard, fault_latched=True))
        self.observe(0.0)
        self.assertEqual(self.control.authority.phase, "fault_latched")
        self.assertIsNone(self.control.authority.pending_holder)
        self.assertIsNone(self.control.authority.grant)
        self.assertTrue(self.guard.fault_latched)

    def test_rejects_invalid_session_generation_and_enable_before_mutating(self):
        generation = self.acquire(self.a)
        for fields, reason in (
            ({"session_id": self.b.session_id, "generation": generation, "enabled": True}, "invalid_session"),
            ({"generation": {**generation, "number": True}, "enabled": True}, "invalid_request"),
            ({"generation": generation, "enabled": 1}, "invalid_request"),
        ):
            with self.subTest(fields=fields):
                self.assert_rejected(self.dispatch(self.a, "set_authority_enabled", **fields), reason)
        self.assertEqual(self.control.authority.phase, "held")
        self.assert_revoked()
