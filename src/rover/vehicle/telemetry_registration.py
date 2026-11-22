from dataclasses import dataclass
from ipaddress import IPv4Address
from math import isfinite
from secrets import token_hex

from rover.protocol.telemetry_registration import (
    REGISTRATION_TOKEN_BYTES,
    decode_telemetry_registration,
)


_LIMITED_BROADCAST = IPv4Address("255.255.255.255")


@dataclass(frozen=True)
class _PendingRegistration:
    session_id: str
    expires_at_s: float
    expected_ip: IPv4Address | None


class TelemetryRegistry:
    """Track one short-lived registration and one destination per authenticated session.

    Callers issue and revoke registrations on the vehicle simulation thread.
    """

    def __init__(self, lifetime_s: float = 5.0):
        if not isfinite(lifetime_s) or lifetime_s <= 0:
            raise ValueError("telemetry registration lifetime must be positive and finite")

        self._lifetime_s = lifetime_s
        self._pending: dict[str, _PendingRegistration] = {}
        self._tokens_by_session: dict[str, str] = {}
        self._destinations: dict[str, tuple[str, int]] = {}

    def issue(self, session_id: str, *, now_s: float, expected_ip: str | None = None) -> str:
        expires_at_s = now_s + self._lifetime_s
        if not isfinite(expires_at_s):
            raise ValueError("telemetry registration expiry must be finite")
        peer_ip = IPv4Address(expected_ip) if expected_ip is not None else None

        token = token_hex(REGISTRATION_TOKEN_BYTES)
        while token in self._pending:
            token = token_hex(REGISTRATION_TOKEN_BYTES)
        self._clear_pending(session_id)
        self._pending[token] = _PendingRegistration(session_id, expires_at_s, peer_ip)
        self._tokens_by_session[session_id] = token
        return token

    def accept(self, packet: bytes, source: tuple[str, int], *, now_s: float) -> str | None:
        """Register the datagram source and consume its token on success."""
        host, port = source
        if not isfinite(now_s) or not isinstance(host, str) or type(port) is not int or not 1 <= port <= 65535:
            return None
        try:
            token = decode_telemetry_registration(packet)
            source_ip = IPv4Address(host)
        except ValueError:
            return None
        if source_ip.is_unspecified or source_ip.is_multicast or source_ip == _LIMITED_BROADCAST:
            return None

        pending = self._pending.get(token)
        if pending is None:
            return None
        if now_s >= pending.expires_at_s:
            self._clear_pending(pending.session_id)
            return None
        if pending.expected_ip is not None and source_ip != pending.expected_ip:
            return None

        self._clear_pending(pending.session_id)
        self._destinations[pending.session_id] = (str(source_ip), port)
        return pending.session_id

    def destination(self, session_id: str) -> tuple[str, int] | None:
        return self._destinations.get(session_id)

    def destinations(self) -> tuple[tuple[str, int], ...]:
        return tuple(dict.fromkeys(self._destinations.values()))

    def disconnect(self, session_id: str) -> None:
        self._clear_pending(session_id)
        self._destinations.pop(session_id, None)

    def _clear_pending(self, session_id: str) -> None:
        token = self._tokens_by_session.pop(session_id, None)
        if token is not None:
            self._pending.pop(token, None)
