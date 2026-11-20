import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.management_client import ManagementClient
from rover.protocol.control_codec import (
    ControlCommand,
    decode_windowed_command,
    encode_windowed_command,
)
from rover.protocol.management_codec import (
    AuthenticateRequest,
    OpenSessionRequest,
    IssueControlWindowRequest,
    ResetControlRequest,
    ResetControlResult,
    decode_reset_control_request,
    decode_reset_control_result,
    decode_session_opened,
    decode_session_request,
    encode_authenticate_request,
    encode_open_session_request,
    encode_reset_control_request,
    encode_reset_control_result,
)
from rover.transport.tcp_records import decode_record, encode_record
from rover.vehicle.control_guard import (
    ControlGuardState,
    enable_control_session,
    update_windowed_command_guard,
)
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.vehicle.control_window import ControlWindow
from rover.vehicle.management_connection import serve_management_connection
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    handle_connection_closed,
    handle_issue_control_window,
    handle_open_session,
    handle_reset_control,
)
from rover.vehicle.vehicle_runtime import step_vehicle


class TestResetControlCodec(unittest.TestCase):
    def test_request_and_results_match_wire_format(self):
        request = ResetControlRequest(7, "old-session")
        packet = encode_reset_control_request(request)
        self.assertEqual(
            json.loads(packet),
            {
                "version": 1,
                "type": "reset_control",
                "request_id": 7,
                "session_id": "old-session",
            },
        )
        self.assertEqual(decode_reset_control_request(packet), request)
        self.assertEqual(decode_session_request(packet), request)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                reply = ResetControlResult(7, accepted)
                packet = encode_reset_control_result(reply)
                self.assertEqual(
                    json.loads(packet),
                    {
                        "version": 1,
                        "type": "reset_control_result",
                        "request_id": 7,
                        "accepted": accepted,
                    },
                )
                self.assertEqual(decode_reset_control_result(packet), reply)

    def test_rejects_invalid_fields_envelopes_and_size(self):
        for request_id, session_id in (
            (-1, "old-session"),
            (True, "old-session"),
            (7, ""),
            (7, None),
        ):
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "reset_control",
                    "request_id": request_id,
                    "session_id": session_id,
                }
            ).encode()
            for func, value in (
                (
                    encode_reset_control_request,
                    ResetControlRequest(request_id, session_id),
                ),
                (decode_reset_control_request, packet),
                (decode_session_request, packet),
            ):
                with self.subTest(func=func.__name__, fields=(request_id, session_id)):
                    with self.assertRaises(ValueError):
                        func(value)
        for request_id, accepted in ((True, True), (-1, True), (7, 1), (7, None)):
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "reset_control_result",
                    "request_id": request_id,
                    "accepted": accepted,
                }
            ).encode()
            for func, value in (
                (encode_reset_control_result, ResetControlResult(request_id, accepted)),
                (decode_reset_control_result, packet),
            ):
                with self.subTest(func=func.__name__, fields=(request_id, accepted)):
                    with self.assertRaises(ValueError):
                        func(value)
        for decoder, message, field in (
            (
                decode_reset_control_request,
                dict(
                    version=1,
                    type="reset_control",
                    request_id=7,
                    session_id="old-session",
                ),
                "session_id",
            ),
            (
                decode_reset_control_result,
                dict(
                    version=1, type="reset_control_result", request_id=7, accepted=True
                ),
                "accepted",
            ),
        ):
            missing = message.copy()
            del missing[field]
            for value in (
                dict(message, version=2),
                dict(message, type="open_session"),
                missing,
                [],
            ):
                with self.subTest(decoder=decoder.__name__, value=value):
                    with self.assertRaises(ValueError):
                        decoder(json.dumps(value).encode())
            with self.subTest(decoder=decoder.__name__, size=4097):
                with self.assertRaisesRegex(ValueError, "too large"):
                    decoder(b"{" * 4097)


class TestResetControlState(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState(
            session_id="old-session",
            control_key=b"k" * 32,
            fault_latched=True,
            enabled_at_s=99.0,
            last_seq=9,
            control_window=ControlWindow("old-window", 99.0, 99.25),
        )
        self.connection = AuthenticatedConnection("desktop-a", "old-session")
        self.request = ResetControlRequest(7, "old-session")

    def test_owner_reset_revokes_old_control_and_preserves_position(self):
        guard, connection, reply = handle_reset_control(
            self.guard, self.connection, self.request
        )
        self.assertEqual(reply, ResetControlResult(7, True))
        self.assertEqual(guard, ControlGuardState())
        self.assertEqual(connection, AuthenticatedConnection("desktop-a"))
        self.assertTrue(self.guard.fault_latched)
        self.assertEqual(self.connection.session_id, "old-session")
        state = VehicleState(7.0, -3.0, 0.5, 0.0)
        self.assertEqual(step_vehicle(state, guard.last_accepted, 100.0, 0.02), state)

    def test_rejects_wrong_owner_healthy_or_unstopped_state(self):
        cases = (
            (replace(self.guard, fault_latched=False), self.connection, self.request),
            (replace(self.guard, enabled=True), self.connection, self.request),
            (
                replace(
                    self.guard,
                    last_accepted=AcceptedTarget(ControlTarget(1.0, 0.0), 99.0),
                ),
                self.connection,
                self.request,
            ),
            (replace(self.guard, session_id=None), self.connection, self.request),
            (self.guard, AuthenticatedConnection("desktop-a"), self.request),
            (self.guard, AuthenticatedConnection("desktop-a", "stale"), self.request),
            (self.guard, self.connection, ResetControlRequest(7, "other")),
        )
        for before, owner, request in cases:
            with self.subTest(guard=before, owner=owner, request=request):
                guard, connection, reply = handle_reset_control(before, owner, request)
                self.assertIs(guard, before)
                self.assertIs(connection, owner)
                self.assertEqual(reply, ResetControlResult(7, False))

    def test_repeating_old_reset_is_denied(self):
        guard, connection, reply = handle_reset_control(
            self.guard, self.connection, self.request
        )
        self.assertTrue(reply.accepted)
        after, owner, reply = handle_reset_control(
            guard, connection, ResetControlRequest(8, "old-session")
        )
        self.assertEqual((after, owner), (guard, connection))
        self.assertEqual(reply, ResetControlResult(8, False))

    def test_new_session_key_window_and_enable_are_required(self):
        guard, connection, reply = handle_reset_control(
            self.guard, self.connection, self.request
        )
        self.assertTrue(reply.accepted)
        with (
            patch(
                "rover.vehicle.management_session.uuid4",
                side_effect=[
                    SimpleNamespace(hex="new-session"),
                    SimpleNamespace(hex="new-window"),
                ],
            ),
            patch(
                "rover.vehicle.management_session.token_bytes", return_value=b"n" * 32
            ),
        ):
            guard, connection, opened = handle_open_session(
                guard, connection, OpenSessionRequest(8)
            )
            self.assertEqual(opened.session_id, "new-session")
            self.assertEqual(guard.control_key, b"n" * 32)
            self.assertFalse(guard.enabled)
            self.assertIsNone(guard.last_seq)
            guard, window = handle_issue_control_window(
                guard,
                connection,
                IssueControlWindowRequest(9, "new-session"),
                now_s=100.0,
            )
        valid = ControlCommand(
            "new-session", 0, ControlTarget(1.0, 0.0), window_id=window.window_id
        )
        self.assertEqual(update_windowed_command_guard(guard, 100.01, valid), guard)
        guard = enable_control_session(guard, 100.02)
        old_packet = encode_windowed_command(
            ControlCommand(
                "old-session", 500, ControlTarget(9.0, 0.0), window_id="old-window"
            ),
            b"k" * 32,
        )
        with self.assertRaisesRegex(ValueError, "MAC"):
            decode_windowed_command(old_packet, guard.control_key)
        for command in (
            replace(valid, session_id="old-session", seq=500),
            replace(valid, window_id="old-window", seq=500),
        ):
            self.assertEqual(
                update_windowed_command_guard(guard, 100.03, command), guard
            )
        guard = update_windowed_command_guard(guard, 100.04, valid)
        self.assertEqual(guard.last_seq, 0)
        self.assertEqual(guard.last_accepted.target, ControlTarget(1.0, 0.0))


class TestResetControlConnection(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState(fault_latched=True)
        self.open_inputs = []
        self.closed_connection = None
        self.before_close = None
        self.sock = Mock()
        self.sock.gettimeout.return_value = 3.0

    def _open(self, connection, request):
        self.open_inputs.append(connection)
        self.guard, connection, reply = handle_open_session(
            self.guard, connection, request
        )
        return connection, reply

    def _reset(self, connection, request):
        self.guard, connection, reply = handle_reset_control(
            self.guard, connection, request
        )
        return connection, reply

    def _close(self, connection):
        self.closed_connection = connection
        self.before_close = self.guard
        self.guard = handle_connection_closed(self.guard, connection)

    def _serve(self, callback, *, reopen=False):
        packets = [
            encode_authenticate_request(AuthenticateRequest(1, "desktop-a", "secret")),
            encode_open_session_request(OpenSessionRequest(2)),
            b'{"version":1,"type":"reset_control","request_id":3,"session_id":"session-1"}',
        ]
        if reopen:
            packets.append(encode_open_session_request(OpenSessionRequest(4)))
        self.sock.recv.side_effect = [b"".join(encode_record(p) for p in packets), b""]
        with patch(
            "rover.vehicle.management_session.uuid4",
            side_effect=[
                SimpleNamespace(hex="session-1"),
                SimpleNamespace(hex="session-2"),
            ],
        ):
            serve_management_connection(
                self.sock,
                {"desktop-a": b"secret"},
                self._open,
                self._close,
                reset_control=callback,
            )

    def test_reset_then_open_uses_unbound_context_and_closes_latest_session(self):
        self._serve(self._reset, reopen=True)
        packets = [decode_record(c.args[0]) for c in self.sock.sendall.call_args_list]
        self.assertEqual(
            decode_reset_control_result(packets[2]), ResetControlResult(3, True)
        )
        self.assertEqual(
            self.open_inputs,
            [
                AuthenticatedConnection("desktop-a"),
                AuthenticatedConnection("desktop-a"),
            ],
        )
        self.assertEqual(decode_session_opened(packets[3]).session_id, "session-2")
        self.assertEqual(self.closed_connection.session_id, "session-2")
        self.assertFalse(self.before_close.fault_latched)
        self.assertFalse(self.before_close.enabled)
        self.assertEqual(self.guard, ControlGuardState())

    def test_missing_callback_denies_and_keeps_fault(self):
        self._serve(None)
        reply = decode_reset_control_result(
            decode_record(self.sock.sendall.call_args.args[0])
        )
        self.assertEqual(reply, ResetControlResult(3, False))
        self.assertEqual(self.closed_connection.session_id, "session-1")
        self.assertTrue(self.guard.fault_latched)

    def test_reply_send_failure_keeps_reset_state_and_unbound_context(self):
        self.sock.sendall.side_effect = [None, None, OSError("send failed")]
        with self.assertRaisesRegex(OSError, "send failed"):
            self._serve(self._reset)
        self.assertEqual(self.closed_connection, AuthenticatedConnection("desktop-a"))
        self.assertEqual(self.guard, ControlGuardState())


class TestResetControlClient(unittest.TestCase):
    def test_ids_continue_after_reset_and_same_client_can_open_again(self):
        tls = Mock()
        tls.gettimeout.return_value = 3.0
        tls.recv.side_effect = [
            encode_record(
                json.dumps(
                    {
                        "version": 1,
                        "type": "reset_control_result",
                        "request_id": i,
                        "accepted": accepted,
                    }
                ).encode()
            )
            for i, accepted in ((1, False), (2, True))
        ] + [
            encode_record(
                json.dumps(
                    {
                        "version": 1,
                        "type": "session_opened",
                        "request_id": 3,
                        "session_id": "new-session",
                    }
                ).encode()
            )
        ]
        client = ManagementClient(tls)
        self.assertEqual(client.reset_control("other"), ResetControlResult(1, False))
        self.assertEqual(
            client.reset_control("old-session"), ResetControlResult(2, True)
        )
        self.assertEqual(client.open_session().session_id, "new-session")
        messages = [
            json.loads(decode_record(c.args[0])) for c in tls.sendall.call_args_list
        ]
        self.assertEqual(
            [(m["type"], m["request_id"]) for m in messages],
            [("reset_control", 1), ("reset_control", 2), ("open_session", 3)],
        )
