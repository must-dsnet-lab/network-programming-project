from math import isfinite
from ssl import SSLSocket
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
        if type(reply.get("owns_authority")) is not bool:
            self._invalid_reply("owns_authority must be a boolean")
        online_sessions = reply.get("online_sessions")
        if type(online_sessions) is not int or online_sessions < 0:
            self._invalid_reply("online_sessions must be a non-negative integer")
        for field in ("queue_wait_ms", "tick_interval_ms"):
            if not _nonnegative_number(reply.get(field)):
                self._invalid_reply(f"{field} must be a finite, non-negative number")
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
