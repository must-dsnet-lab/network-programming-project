"""Vehicle-side authority rules for serialized, authenticated events."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from uuid import uuid4


class AuthorityPhase(StrEnum):
    IDLE = "idle"
    HELD = "held"
    CONTROLLING = "controlling"
    STOPPING = "stopping"
    FAULT_LATCHED = "fault_latched"


@dataclass(frozen=True)
class ControlSession:
    """Identity bound by the vehicle after authenticating a connection."""

    client_id: str
    connection_id: str
    session_id: str


@dataclass(frozen=True)
class AuthorityGeneration:
    epoch: str
    number: int


@dataclass(frozen=True)
class AuthorityGrant:
    session: ControlSession
    generation: AuthorityGeneration


@dataclass(frozen=True)
class ControlAuthority:
    """Immutable state advanced by the vehicle's control loop."""

    phase: AuthorityPhase = AuthorityPhase.IDLE
    grant: AuthorityGrant | None = None
    pending_holder: ControlSession | None = None
    epoch: str = field(default_factory=lambda: uuid4().hex)
    last_generation: int = 0

    def acquire(self, session: ControlSession) -> tuple[ControlAuthority, str | None]:
        if self.phase is AuthorityPhase.FAULT_LATCHED:
            return self, "fault_latched"
        if self.phase is AuthorityPhase.STOPPING:
            return self, "vehicle_stopping"
        if self.grant is not None:
            return self, "authority_busy"
        return self._grant_to(session), None

    def set_enabled(
        self, session: ControlSession, generation: AuthorityGeneration, enabled: bool
    ) -> tuple[ControlAuthority, str | None]:
        rejection = self.holder_rejection(session, generation)
        if rejection is not None:
            return self, rejection
        phase = AuthorityPhase.CONTROLLING if enabled else AuthorityPhase.HELD
        return replace(self, phase=phase), None

    def release(
        self, session: ControlSession, generation: AuthorityGeneration
    ) -> tuple[ControlAuthority, str | None]:
        rejection = self.holder_rejection(session, generation)
        if rejection is not None:
            return self, rejection
        return self._begin_stopping(), None

    def transfer(
        self,
        session: ControlSession,
        generation: AuthorityGeneration,
        recipient: ControlSession,
    ) -> tuple[ControlAuthority, str | None]:
        """Begin transfer to a recipient resolved from a live session by the caller."""
        rejection = self.holder_rejection(session, generation)
        if rejection is not None:
            return self, rejection
        if session.connection_id == recipient.connection_id:
            return self, "invalid_transfer_target"
        return self._begin_stopping(recipient), None

    def disconnect(self, session: ControlSession) -> ControlAuthority:
        if self.grant is not None and self.grant.session == session:
            return self._begin_stopping()
        if self.pending_holder == session:
            return replace(self, pending_holder=None)
        return self

    def lease_expired(self, generation: AuthorityGeneration) -> ControlAuthority:
        """Apply an expiry event evaluated by the vehicle's lease clock."""
        if self.grant is not None and self.grant.generation == generation:
            return self._begin_stopping()
        return self

    def motion_stopped(self, generation: AuthorityGeneration) -> ControlAuthority:
        """Confirm simulation reported zero speed for this stop transition."""
        if (
            self.phase is not AuthorityPhase.STOPPING
            or generation != AuthorityGeneration(self.epoch, self.last_generation)
        ):
            return self
        if self.pending_holder is not None:
            return self._grant_to(self.pending_holder)
        return replace(self, phase=AuthorityPhase.IDLE)

    def latch_fault(self) -> ControlAuthority:
        return replace(
            self, phase=AuthorityPhase.FAULT_LATCHED, grant=None, pending_holder=None
        )

    def fault_cleared(self) -> ControlAuthority:
        """Apply an explicit reset already accepted by the vehicle's recovery path."""
        if self.phase is AuthorityPhase.FAULT_LATCHED:
            return replace(self, phase=AuthorityPhase.IDLE)
        return self

    def holder_rejection(
        self, session: ControlSession, generation: AuthorityGeneration
    ) -> str | None:
        if self.phase is AuthorityPhase.FAULT_LATCHED:
            return "fault_latched"
        if self.phase is AuthorityPhase.STOPPING:
            return "vehicle_stopping"
        if self.grant is None or self.grant.session != session:
            return "not_authority_holder"
        if self.grant.generation != generation:
            return "stale_generation"
        return None

    def _grant_to(self, session: ControlSession) -> ControlAuthority:
        number = self.last_generation + 1
        return replace(
            self,
            phase=AuthorityPhase.HELD,
            grant=AuthorityGrant(session, AuthorityGeneration(self.epoch, number)),
            pending_holder=None,
            last_generation=number,
        )

    def _begin_stopping(self, recipient: ControlSession | None = None) -> ControlAuthority:
        return replace(
            self, phase=AuthorityPhase.STOPPING, grant=None, pending_holder=recipient
        )
