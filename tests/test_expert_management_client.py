import json
import unittest
from unittest.mock import Mock

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.protocol.expert_management_codec import (
    decode_expert_management,
    encode_expert_management,
)
from rover.protocol.management_codec import MAX_MANAGEMENT_MESSAGE_BYTES
from rover.transport.tcp_records import decode_record, encode_record


class TestExpertManagementCodec(unittest.TestCase):
    def test_round_trip_preserves_envelope_and_fields(self):
        payload = encode_expert_management("query_authority", 7, session_id="会话-A")
        self.assertEqual(
            decode_expert_management(payload),
            {
                "version": 2,
                "type": "query_authority",
                "request_id": 7,
                "session_id": "会话-A",
            },
        )

    def test_decoding_rejects_invalid_envelopes_and_nonstandard_json(self):
        for payload in (
            b"[]",
            b'{"version":true,"type":"authenticate","request_id":1}',
            b'{"version":1,"type":"authenticate","request_id":1}',
            b'{"version":2.0,"type":"authenticate","request_id":1}',
            b'{"version":2,"type":"authenticate","request_id":true}',
            b'{"version":2,"type":"authenticate","request_id":0}',
            b'{"version":2,"type":"authenticate","request_id":-1}',
            b'{"version":2,"type":" ","request_id":1}',
            b'{"version":2,"type":"authenticate"}',
            b'{"version":2,"type":"authenticate","request_id":1,"value":NaN}',
            b"\xff",
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                decode_expert_management(payload)

    def test_encoding_rejects_invalid_or_reserved_envelope_fields(self):
        for message_type, request_id, fields in (
            ("", 1, {}),
            ("authenticate", True, {}),
            ("authenticate", 0, {}),
            ("authenticate", 2.0, {}),
            ("authenticate", 1, {"version": 1}),
            ("authenticate", 1, {"type": "other"}),
            ("authenticate", 1, {"value": float("nan")}),
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                encode_expert_management(message_type, request_id, **fields)

    def test_payload_limit_applies_to_utf8_bytes_in_both_directions(self):
        empty = encode_expert_management("query_authority", 1, padding="")
        available = MAX_MANAGEMENT_MESSAGE_BYTES - len(empty)
        padding = "x" * available
        limit = encode_expert_management("query_authority", 1, padding=padding)
        self.assertEqual(len(limit), MAX_MANAGEMENT_MESSAGE_BYTES)
        self.assertEqual(decode_expert_management(limit)["padding"], padding)
        with self.assertRaisesRegex(ValueError, "too large"):
            encode_expert_management("query_authority", 1, padding=padding + "车")
        with self.assertRaisesRegex(ValueError, "too large"):
            decode_expert_management(limit + b" ")


class TestExpertManagementClient(unittest.TestCase):
    status = {
        "accepted": True,
        "reason": None,
        "phase": "idle",
        "owns_authority": False,
        "online_sessions": 2,
        "queue_wait_ms": 0.4,
        "tick_interval_ms": 20.0,
    }

    def _socket(self, *replies):
        tls = Mock()
        tls.gettimeout.return_value = 2.0
        chunks = [
            encode_record(json.dumps(reply).encode("utf-8"))
            for reply in replies
        ]
        tls.recv.side_effect = [b"".join(chunks), b""] if chunks else [b""]
        return tls

    def _reply(self, message_type, request_id, **fields):
        return {"version": 2, "type": message_type, "request_id": request_id, **fields}

    def _sent(self, tls):
        return [
            json.loads(decode_record(call.args[0]))
            for call in tls.sendall.call_args_list
        ]

    def test_requests_share_numbering_and_buffered_replies(self):
        query_result = self._reply("query_authority_result", 3, **self.status)
        tls = self._socket(
            self._reply("authentication_result", 1, accepted=True),
            self._reply("session_opened", 2, session_id="session-a"),
            query_result,
        )
        client = ExpertManagementClient(tls)
        self.assertTrue(client.authenticate("desktop-a", "test-credential"))
        self.assertEqual(client.open_session(), "session-a")
        self.assertEqual(client.query_authority("session-a"), query_result)
        self.assertEqual(
            self._sent(tls),
            [
                self._reply(
                    "authenticate", 1, client_id="desktop-a", credential="test-credential"
                ),
                self._reply("open_session", 2),
                self._reply("query_authority", 3, session_id="session-a"),
            ],
        )

    def test_correlated_management_error_allows_next_request(self):
        tls = self._socket(
            self._reply("management_error", 1, reason="invalid_session"),
            self._reply("query_authority_result", 2, **self.status),
        )
        client = ExpertManagementClient(tls)
        with self.assertRaisesRegex(ValueError, "invalid_session"):
            client.query_authority("old-session")
        self.assertTrue(client.query_authority("session-a")["accepted"])
        self.assertEqual([p["request_id"] for p in self._sent(tls)], [1, 2])

    def test_invalid_reply_stops_reuse(self):
        for reply in (
            self._reply("authentication_result", 3, accepted=True),
            self._reply("session_opened", 1, session_id="session-a"),
            self._reply("authentication_result", 1, accepted=1),
            self._reply("management_error", 1, reason=" "),
            self._reply("management_error", 2, reason="invalid_session"),
        ):
            with self.subTest(response=reply):
                tls = self._socket(reply)
                client = ExpertManagementClient(tls)
                with self.assertRaises(ValueError):
                    client.authenticate("desktop-a", "test-credential")
                with self.assertRaisesRegex(ConnectionError, "unusable"):
                    client.open_session()
                tls.sendall.assert_called_once()

    def test_invalid_query_body_stops_reuse(self):
        for invalid in (
            {"reason": "unexpected"},
            {"reason": None, "accepted": 1},
            {"owns_authority": 0},
            {"phase": ""},
            {"online_sessions": -1},
            {"online_sessions": True},
            {"queue_wait_ms": True},
            {"tick_interval_ms": -0.5},
        ):
            with self.subTest(fields=invalid):
                data = {**self.status, **invalid}
                tls = self._socket(self._reply("query_authority_result", 1, **data))
                client = ExpertManagementClient(tls)
                with self.assertRaises(ValueError):
                    client.query_authority("session-a")
                with self.assertRaisesRegex(ConnectionError, "unusable"):
                    client.open_session()
                tls.sendall.assert_called_once()

    def test_eof_and_timeout_stop_reuse(self):
        for reading, error in ((b"", ConnectionError), (TimeoutError(), TimeoutError)):
            with self.subTest(error=error):
                tls = self._socket()
                tls.recv.side_effect = [reading]
                client = ExpertManagementClient(tls)
                with self.assertRaises(error):
                    client.open_session()
                with self.assertRaisesRegex(ConnectionError, "unusable"):
                    client.open_session()
                tls.sendall.assert_called_once()

    def test_authentication_rejection_cannot_open_a_session(self):
        tls = self._socket(self._reply("authentication_result", 1, accepted=False))
        client = ExpertManagementClient(tls)
        self.assertFalse(client.authenticate("desktop-a", "wrong"))
        with self.assertRaisesRegex(ConnectionError, "unusable"):
            client.open_session()
        tls.sendall.assert_called_once()

    def test_local_encoding_error_preserves_the_connection_and_numbering(self):
        tls = self._socket(self._reply("authentication_result", 1, accepted=True))
        client = ExpertManagementClient(tls)
        with self.assertRaisesRegex(ValueError, "too large"):
            client.authenticate("desktop-a", "x" * MAX_MANAGEMENT_MESSAGE_BYTES)
        tls.sendall.assert_not_called()
        self.assertTrue(client.authenticate("desktop-a", "test-credential"))
        self.assertEqual([p["request_id"] for p in self._sent(tls)], [1])
