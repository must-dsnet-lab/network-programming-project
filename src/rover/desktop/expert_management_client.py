from math import isfinite
from ssl import SSLSocket
from string import hexdigits
from typing import NoReturn

from rover.protocol.expert_management_codec import (
    decode_expert_management,
    encode_expert_management,
)
from rover.transport.tcp_transport import iter_records, send_record


class ExpertManagementClient:
    """Exchange one expert management request at a time on a verified TLS stream."""

    def __init__(self, tls: SSLSocket):
        self._tls = tls
        self._records = iter_records(tls, record_timeout_s=1.0)
        self._next_request_id = 1
        self._usable = True

    def authenticate(self, client_id: str, credential: str) -> bool:
        reply = self._exchange(
            "authenticate",
            "authentication_result",
            client_id=client_id,
            credential=credential,
        )
        accepted = reply.get("accepted")
        if type(accepted) is not bool:
            self._invalid_reply("authentication accepted must be a boolean")
        if not accepted:
            self._usable = False
        return accepted

    def open_session(self) -> str:
        reply = self._exchange("open_session", "session_opened")
        session_id = reply.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            self._invalid_reply("session_id must be a non-blank string")
        return session_id

    def query_authority(self, session_id: str) -> dict[str, object]:
        reply = self._exchange(
            "query_authority", "query_authority_result", session_id=session_id
        )
        if (
            reply.get("accepted") is not True
            or "reason" not in reply
            or reply["reason"] is not None
        ):
            self._invalid_reply("invalid authority query result")
        phase = reply.get("phase")
        if not isinstance(phase, str) or not phase.strip():
            self._invalid_reply("authority phase must be a non-blank string")
        owns_authority = reply.get("owns_authority")
        if type(owns_authority) is not bool:
            self._invalid_reply("owns_authority must be a boolean")
        online_sessions = reply.get("online_sessions")
        if type(online_sessions) is not int or online_sessions < 0:
            self._invalid_reply("online_sessions must be a non-negative integer")
        for field in ("queue_wait_ms", "tick_interval_ms"):
            if not _nonnegative_number(reply.get(field)):
                self._invalid_reply(f"{field} must be a finite, non-negative number")
        if "generation" in reply:
            generation = reply["generation"]
            if owns_authority and not _valid_generation(generation):
                self._invalid_reply("invalid authority generation")
            if not owns_authority and generation is not None:
                self._invalid_reply("authority generation requires ownership")
        if "target_session_ids" in reply:
            targets = reply["target_session_ids"]
            if not isinstance(targets, list) or not all(_nonblank_text(item) for item in targets):
                self._invalid_reply("target_session_ids must be a list of session IDs")
            if targets and not owns_authority:
                self._invalid_reply("transfer targets require ownership")
        return reply

    def acquire_authority(self, session_id: str) -> dict[str, object]:
        reply = self._action("acquire_authority", session_id)
        if reply["accepted"] and not _valid_generation(reply.get("generation")):
            self._invalid_reply("invalid authority generation")
        return reply

    def set_authority_enabled(
        self, session_id: str, generation: dict[str, object], enabled: bool
    ) -> dict[str, object]:
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        reply = self._action(
            "set_authority_enabled", session_id, generation=generation, enabled=enabled
        )
        if reply["accepted"] and enabled:
            key = reply.get("control_key")
            if not isinstance(key, str) or len(key) != 64 or any(c not in hexdigits for c in key):
                self._invalid_reply("control_key must contain 32 bytes in hexadecimal")
        return reply

    def issue_control_window(
        self, session_id: str, generation: dict[str, object]
    ) -> dict[str, object]:
        reply = self._action("issue_control_window", session_id, generation=generation)
        if reply["accepted"]:
            if not _nonblank_text(reply.get("window_id")):
                self._invalid_reply("window_id must be a non-blank string")
            lifetime = reply.get("lifetime_s")
            if not _nonnegative_number(lifetime) or lifetime == 0:
                self._invalid_reply("lifetime_s must be a finite, positive number")
        return reply

    def transfer_authority(
        self, session_id: str, generation: dict[str, object], target_session_id: str
    ) -> dict[str, object]:
        if not _nonblank_text(target_session_id):
            raise ValueError("target_session_id must be a non-blank string")
        return self._action(
            "transfer_authority", session_id,
            generation=generation, target_session_id=target_session_id,
        )

    def release_authority(
        self, session_id: str, generation: dict[str, object]
    ) -> dict[str, object]:
        return self._action("release_authority", session_id, generation=generation)

    def emergency_stop(
        self, session_id: str, generation: dict[str, object]
    ) -> dict[str, object]:
        return self._action("emergency_stop", session_id, generation=generation)

    def reset_control(self, session_id: str) -> dict[str, object]:
        return self._action("reset_control", session_id)

    def _action(
        self, message_type: str, session_id: str, **fields: object
    ) -> dict[str, object]:
        if not _nonblank_text(session_id):
            raise ValueError("session_id must be a non-blank string")
        if "generation" in fields and not _valid_generation(fields["generation"]):
            raise ValueError("invalid authority generation")
        reply = self._exchange(
            message_type, f"{message_type}_result", session_id=session_id, **fields
        )
        accepted = reply.get("accepted")
        if type(accepted) is not bool:
            self._invalid_reply("authority result accepted must be a boolean")
        if not _nonblank_text(reply.get("phase")):
            self._invalid_reply("authority phase must be a non-blank string")
        if accepted and ("reason" not in reply or reply["reason"] is not None):
            self._invalid_reply("accepted authority result must have a null reason")
        if not accepted and not _nonblank_text(reply.get("reason")):
            self._invalid_reply("rejected authority result must have a non-blank reason")
        return reply

    def _exchange(self, request_type: str, expected_type: str, **fields) -> dict[str, object]:
        if not self._usable:
            raise ConnectionError("expert management connection is unusable")
        request_id = self._next_request_id
        packet = encode_expert_management(request_type, request_id, **fields)
        self._next_request_id += 1
        rejection = None
        try:
            send_record(self._tls, packet)
            payload = next(self._records, None)
            if payload is None:
                raise ConnectionError("expert management connection closed before reply")
            reply = decode_expert_management(payload)
            if reply["request_id"] != request_id:
                raise ValueError("reply request_id does not match request")
            if reply["type"] == "management_error":
                reason = reply.get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ValueError("management_error reason must be a non-blank string")
                rejection = reason
            elif reply["type"] != expected_type:
                raise ValueError("unexpected expert management reply type")
        except (OSError, ValueError):
            self._usable = False
            raise
        if rejection is not None:
            raise ValueError(rejection)
        return reply

    def _invalid_reply(self, message: str) -> NoReturn:
        self._usable = False
        raise ValueError(message)


def _nonnegative_number(value: object) -> bool:
    if type(value) is int:
        return value >= 0
    return type(value) is float and isfinite(value) and value >= 0


def _nonblank_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_generation(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    number = value.get("number")
    return _nonblank_text(value.get("epoch")) and type(number) is int and number > 0
