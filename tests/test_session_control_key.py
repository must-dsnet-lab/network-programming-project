import json
import unittest

from rover.protocol.management_codec import (
    OpenSessionRequest,
    SessionOpened,
    decode_session_opened,
    encode_session_opened,
)
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    handle_connection_closed,
    handle_open_session,
)


class TestSessionControlKey(unittest.TestCase):
    def test_reply_preserves_key(self):
        key_hex = "12" * 32
        reply = SessionOpened(7, "session-a", control_key=key_hex)

        packet = encode_session_opened(reply)

        self.assertEqual(json.loads(packet).get("control_key"), key_hex)
        self.assertEqual(decode_session_opened(packet), reply)

    def test_encoder_rejects_invalid_key(self):
        for key in ("", 7, "0" * 63, "0" * 65, "g" * 64, "A" * 64):
            with self.subTest(key=key):
                reply = SessionOpened(7, "session-a", control_key=key)

                with self.assertRaisesRegex(ValueError, "control_key"):
                    encode_session_opened(reply)

    def test_decoder_rejects_invalid_key(self):
        for key in (None, "", 7, "0" * 63, "0" * 65, "g" * 64, "A" * 64):
            with self.subTest(key=key):
                message = {
                    "version": 1,
                    "type": "session_opened",
                    "request_id": 7,
                    "session_id": "session-a",
                    "control_key": key,
                }
                packet = json.dumps(message).encode("utf-8")

                with self.assertRaisesRegex(ValueError, "control_key"):
                    decode_session_opened(packet)

    def test_new_session_binds_key_before_reply(self):
        guard, connection, reply = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(7),
        )

        self.assertIsInstance(guard.control_key, bytes)
        self.assertEqual(len(guard.control_key), 32)
        self.assertEqual(bytes.fromhex(reply.control_key), guard.control_key)
        self.assertEqual(connection.session_id, reply.session_id)

    def test_repeated_request_keeps_same_key(self):
        guard, connection, first = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(7),
        )
        self.assertIsNotNone(guard.control_key)

        after, same_connection, second = handle_open_session(
            guard, connection, OpenSessionRequest(8)
        )

        self.assertIs(after, guard)
        self.assertIs(same_connection, connection)
        self.assertEqual(second.control_key, first.control_key)
        self.assertEqual(second.request_id, 8)

    def test_close_clears_key_and_preserves_fault(self):
        guard = ControlGuardState(
            session_id="session-a",
            control_key=b"k" * 32,
            fault_latched=True,
        )
        connection = AuthenticatedConnection("desktop-a", "session-a")

        closed = handle_connection_closed(guard, connection)

        self.assertIsNone(closed.control_key)
        self.assertIsNone(closed.session_id)
        self.assertTrue(closed.fault_latched)

    def test_existing_session_without_key_is_rejected(self):
        guard = ControlGuardState(session_id="session-a")
        connection = AuthenticatedConnection("desktop-a", "session-a")

        with self.assertRaisesRegex(ValueError, "HMAC key"):
            handle_open_session(guard, connection, OpenSessionRequest(8))

    def test_new_session_gets_new_key(self):
        guard, connection, _ = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(7),
        )
        self.assertIsNotNone(guard.control_key)
        old_key = guard.control_key
        closed = handle_connection_closed(guard, connection)

        reopened, _, _ = handle_open_session(
            closed,
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(8),
        )

        self.assertNotEqual(reopened.control_key, old_key)
