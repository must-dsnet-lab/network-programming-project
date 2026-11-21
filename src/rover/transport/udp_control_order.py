from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from random import Random
from typing import Generic, TypeVar


PacketT = TypeVar("PacketT")
REORDER_HOLD_S = 0.12


class FaultMode(StrEnum):
    REORDER = "reorder"
    DUPLICATE = "duplicate"


class DeliveryRole(StrEnum):
    ORIGINAL = "original"
    DUPLICATE = "duplicate"
    REORDERED = "reordered"
    HOLD_EXPIRED = "hold_expired"


@dataclass(frozen=True)
class Delivery(Generic[PacketT]):
    packet: PacketT
    role: DeliveryRole


class PacketOrderPolicy(Generic[PacketT]):
    """Schedule opaque control datagrams using the caller's monotonic timestamps."""

    def __init__(
        self,
        mode: FaultMode,
        *,
        seed: int = 42,
        probability: float = 0.15,
        hold_s: float = REORDER_HOLD_S,
    ) -> None:
        if not 0 <= probability <= 1:
            raise ValueError("probability must be between zero and one")
        if not isfinite(hold_s) or hold_s <= 0:
            raise ValueError("hold_s must be finite and positive")
        self._mode = FaultMode(mode)
        self._probability = probability
        self._hold_s = hold_s
        self._random = Random(seed)
        self._held: tuple[PacketT, float] | None = None

    @property
    def deadline_s(self) -> float | None:
        """Return the current release deadline, if a packet is held."""
        return self._held[1] if self._held is not None else None

    def flush_due(self, now_s: float) -> tuple[Delivery[PacketT], ...]:
        """Release a due packet even when the relay has no new arrivals."""
        if self._held is None:
            return ()
        packet, deadline_s = self._held
        if now_s < deadline_s:
            return ()
        self._held = None
        return (Delivery(packet, DeliveryRole.HOLD_EXPIRED),)

    def receive(self, packet: PacketT, now_s: float) -> tuple[Delivery[PacketT], ...]:
        ready = self.flush_due(now_s)
        original = Delivery(packet, DeliveryRole.ORIGINAL)
        if self._held is not None:
            held_packet, _ = self._held
            self._held = None
            return (original, Delivery(held_packet, DeliveryRole.REORDERED))

        if self._random.random() >= self._probability:
            return (*ready, original)
        if self._mode == FaultMode.DUPLICATE:
            return (original, Delivery(packet, DeliveryRole.DUPLICATE))

        self._held = (packet, now_s + self._hold_s)
        return ready
