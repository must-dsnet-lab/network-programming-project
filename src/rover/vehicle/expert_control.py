"""Apply expert authority decisions and existing UDP guard state together."""

from dataclasses import replace
from secrets import token_bytes
from uuid import uuid4

from rover.core.vehicle_model import VehicleState
from rover.vehicle.control_authority import AuthorityGeneration, AuthorityPhase, ControlAuthority, ControlSession
from rover.vehicle.control_guard import (
    ControlGuardState,
    enable_control_session,
    open_control_session,
    update_control_guard,
)
from rover.vehicle.control_window import issue_control_window


EXPERT_CONTROL_ACTIONS = frozenset((
    "acquire_authority", "set_authority_enabled", "issue_control_window",
    "release_authority", "transfer_authority", "emergency_stop", "reset_control",
))


def _read_generation(value: object) -> AuthorityGeneration | None:
    if not isinstance(value, dict):
        return None
    epoch, number = value.get("epoch"), value.get("number")
    if not isinstance(epoch, str) or not epoch.strip() or type(number) is not int or number <= 0:
        return None
    return AuthorityGeneration(epoch, number)


def _revoke_motion(guard: ControlGuardState) -> ControlGuardState:
    return ControlGuardState(fault_latched=guard.fault_latched)


class ExpertVehicleControl:
    """Owned and called only by the simulation thread."""

    def __init__(self) -> None:
        self.authority = ControlAuthority()
        self._last_speed_mps: float | None = None

    def _reply(self, guard: ControlGuardState, reason: str | None = None, **fields: object):
        return guard, {
            "accepted": reason is None,
            "reason": reason,
            "phase": self.authority.phase.value,
            **fields,
        }

    def dispatch(
        self,
        guard: ControlGuardState,
        session: ControlSession,
        message: dict[str, object],
        *,
        recipient: ControlSession | None,
        now_s: float,
    ) -> tuple[ControlGuardState, dict[str, object]]:
        # Apply the existing watchdog before any request can enable or renew a window.
        guard = self.sync_fault(update_control_guard(guard, now_s))
        action = message.get("type")
        if not isinstance(action, str) or action not in EXPERT_CONTROL_ACTIONS:
            return self._reply(guard, "unsupported_message_type")
        session_id = message.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return self._reply(guard, "invalid_request")
        if session_id != session.session_id:
            return self._reply(guard, "invalid_session")

        if action == "acquire_authority":
            authority, reason = self.authority.acquire(session)
            if reason is not None:
                return self._reply(guard, reason)
            grant = authority.grant
            assert grant is not None
            self.authority = authority
            return self._reply(_revoke_motion(guard), generation={
                "epoch": grant.generation.epoch, "number": grant.generation.number,
            })

        if action == "reset_control":
            if not guard.fault_latched:
                return self._reply(guard, "fault_not_latched")
            if self._last_speed_mps != 0.0 or guard.enabled or guard.last_accepted is not None:
                return self._reply(guard, "vehicle_not_stopped")
            self.authority = self.authority.fault_cleared()
            return self._reply(replace(guard, fault_latched=False))

        generation = _read_generation(message.get("generation"))
        if generation is None:
            return self._reply(guard, "invalid_request")

        if action == "set_authority_enabled":
            enabled = message.get("enabled")
            if type(enabled) is not bool:
                return self._reply(guard, "invalid_request")
            authority, reason = self.authority.set_enabled(session, generation, enabled)
            if reason is not None:
                return self._reply(guard, reason)
            if not enabled:
                self.authority = authority
                return self._reply(_revoke_motion(guard))
            key = (
                guard.control_key
                if self.authority.phase is AuthorityPhase.CONTROLLING
                and guard.enabled and guard.session_id == session_id
                else None
            )
            if key is None:
                key = token_bytes(32)
                guard = open_control_session(_revoke_motion(guard), session_id)
                guard = replace(enable_control_session(guard, now_s), control_key=key)
            self.authority = authority
            return self._reply(guard, control_key=key.hex())

        # Reuse the state machine's owner/generation check for actions that do not toggle it.
        reason = self.authority.holder_rejection(session, generation)
        if reason is not None:
            return self._reply(guard, reason)

        if action == "issue_control_window":
            if self.authority.phase is not AuthorityPhase.CONTROLLING or not guard.enabled:
                return self._reply(guard, "session_disabled")
            window = issue_control_window(uuid4().hex, now_s)
            return self._reply(
                replace(guard, control_window=window),
                window_id=window.window_id,
                lifetime_s=window.expires_at_s - window.issued_at_s,
            )

        if action == "emergency_stop":
            guard = self.sync_fault(replace(guard, fault_latched=True))
            return self._reply(guard)

        if action == "transfer_authority":
            if recipient is None:
                return self._reply(guard, "invalid_transfer_target")
            authority, reason = self.authority.transfer(session, generation, recipient)
        else:
            authority, reason = self.authority.release(session, generation)
        if reason is not None:
            return self._reply(guard, reason)
        self.authority = authority
        return self._reply(_revoke_motion(guard))

    def disconnect(self, guard: ControlGuardState, session: ControlSession) -> ControlGuardState:
        guard = self.sync_fault(guard)
        grant = self.authority.grant
        self.authority = self.authority.disconnect(session)
        return _revoke_motion(guard) if grant is not None and grant.session == session else guard

    def sync_fault(self, guard: ControlGuardState) -> ControlGuardState:
        if guard.fault_latched or self.authority.phase is AuthorityPhase.FAULT_LATCHED:
            if self.authority.phase is not AuthorityPhase.FAULT_LATCHED:
                self.authority = self.authority.latch_fault()
            return _revoke_motion(replace(guard, fault_latched=True))
        return guard

    def observe_vehicle(self, guard: ControlGuardState, vehicle: VehicleState) -> ControlGuardState:
        self._last_speed_mps = vehicle.speed_mps
        guard = self.sync_fault(guard)
        if vehicle.speed_mps == 0.0 and self.authority.phase is AuthorityPhase.STOPPING:
            generation = AuthorityGeneration(self.authority.epoch, self.authority.last_generation)
            self.authority = self.authority.motion_stopped(generation)
        return guard
