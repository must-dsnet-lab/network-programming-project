import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.management_client import ManagementClient
from rover.protocol.control_codec import ControlCommand
from rover.protocol.management_codec import (
    AuthenticateRequest,
    EmergencyStopRequest,
    EmergencyStopResult,
    IssueControlWindowRequest,
    OpenSessionRequest,
    decode_emergency_stop_request,
    decode_emergency_stop_result,
    decode_session_request,
    encode_authenticate_request,
    encode_emergency_stop_request,
    encode_emergency_stop_result,
    encode_open_session_request,
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
    handle_emergency_stop,
    handle_issue_control_window,
    handle_open_session,
)
from rover.vehicle.vehicle_runtime import step_vehicle


class TestEmergencyStopCodec(unittest.TestCase):
    def test_request_and_result_wire_format(self):
        request = EmergencyStopRequest(7, "session-a")
        packet = encode_emergency_stop_request(request)
        self.assertEqual(
            json.loads(packet),
            {
                "version": 1,
                "type": "emergency_stop",
                "request_id": 7,
                "session_id": "session-a",
            },
        )
        self.assertEqual(decode_emergency_stop_request(packet), request)
        self.assertEqual(decode_session_request(packet), request)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                reply = EmergencyStopResult(7, accepted)
                packet = encode_emergency_stop_result(reply)
                self.assertEqual(
                    json.loads(packet),
                    {
                        "version": 1,
                        "type": "emergency_stop_result",
                        "request_id": 7,
                        "accepted": accepted,
                    },
                )
                self.assertEqual(decode_emergency_stop_result(packet), reply)

    def test_rejects_invalid_fields(self):
        for request_id, session_id in (
            (-1, "session-a"),
            (True, "session-a"),
            (7, ""),
            (7, None),
        ):
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "emergency_stop",
                    "request_id": request_id,
                    "session_id": session_id,
                }
            ).encode("utf-8")
            for func, value in (
                (
                    encode_emergency_stop_request,
                    EmergencyStopRequest(request_id, session_id),
                ),
                (decode_emergency_stop_request, packet),
                (decode_session_request, packet),
            ):
                with self.subTest(func=func.__name__, fields=(request_id, session_id)):
                    with self.assertRaises(ValueError):
                        func(value)
        for request_id, accepted in ((True, True), (-1, True), (7, 1), (7, None)):
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "emergency_stop_result",
                    "request_id": request_id,
                    "accepted": accepted,
                }
            ).encode("utf-8")
            for func, value in (
                (
                    encode_emergency_stop_result,
                    EmergencyStopResult(request_id, accepted),
                ),
                (decode_emergency_stop_result, packet),
            ):
                with self.subTest(func=func.__name__, fields=(request_id, accepted)):
                    with self.assertRaises(ValueError):
                        func(value)

    def test_decoders_check_envelope_and_size(self):
        cases = (
            (
                decode_emergency_stop_request,
                {
                    "version": 1,
                    "type": "emergency_stop",
                    "request_id": 7,
                    "session_id": "session-a",
                },
                "session_id",
            ),
            (
                decode_emergency_stop_result,
                {
                    "version": 1,
                    "type": "emergency_stop_result",
                    "request_id": 7,
                    "accepted": True,
                },
                "accepted",
            ),
        )
        for decoder, message, required in cases:
            missing = message.copy()
            del missing[required]
            for value in (
                dict(message, version=2),
                dict(message, type="open_session"),
                missing,
                [],
            ):
                with self.subTest(decoder=decoder.__name__, value=value):
                    with self.assertRaises(ValueError):
                        decoder(json.dumps(value).encode("utf-8"))
            with self.subTest(decoder=decoder.__name__, oversized=True):
                with self.assertRaisesRegex(ValueError, "too large"):
                    decoder(b"{" * 4097)


class TestEmergencyStopState(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState(
            session_id="session-a",
            control_key=b"k" * 32,
            enabled=True,
            enabled_at_s=100.0,
            last_seq=2,
            last_accepted=AcceptedTarget(ControlTarget(1.0, 0.5), 100.0),
            control_window=ControlWindow("window-a", 100.0, 100.25),
        )
        self.connection = AuthenticatedConnection("desktop-a", "session-a")
        self.request = EmergencyStopRequest(7, "session-a")

    def test_owner_stop_applies_zero_speed_on_current_step(self):
        stopped, reply = handle_emergency_stop(
            self.guard, self.connection, self.request
        )
        self.assertEqual(reply, EmergencyStopResult(7, True))
        self.assertEqual(
            stopped,
            replace(
                self.guard,
                last_accepted=None,
                enabled=False,
                enabled_at_s=None,
                control_window=None,
                fault_latched=True,
            ),
        )
        self.assertIsNotNone(self.guard.last_accepted)
        state = VehicleState(2.0, 3.0, 0.5, 1.0)
        after = step_vehicle(state, stopped.last_accepted, 100.02, 0.02)
        self.assertEqual(after, replace(state, speed_mps=0.0))

    def test_stop_is_allowed_before_enable_and_is_idempotent(self):
        before = replace(
            self.guard, enabled=False, enabled_at_s=None, last_accepted=None
        )
        stopped, reply = handle_emergency_stop(before, self.connection, self.request)
        self.assertTrue(reply.accepted)
        repeated, reply = handle_emergency_stop(
            stopped, self.connection, EmergencyStopRequest(8, "session-a")
        )
        self.assertEqual(repeated, stopped)
        self.assertEqual(reply, EmergencyStopResult(8, True))

        no_key = replace(before, control_key=None)
        stopped, reply = handle_emergency_stop(no_key, self.connection, self.request)
        self.assertTrue(reply.accepted)
        self.assertTrue(stopped.fault_latched)

    def test_wrong_or_unbound_owner_cannot_stop_current_session(self):
        cases = (
            (self.guard, AuthenticatedConnection("desktop-a"), self.request),
            (self.guard, AuthenticatedConnection("desktop-a", "old"), self.request),
            (self.guard, self.connection, EmergencyStopRequest(7, "other")),
            (replace(self.guard, session_id=None), self.connection, self.request),
        )
        for guard, connection, request in cases:
            with self.subTest(connection=connection, request=request):
                after, reply = handle_emergency_stop(guard, connection, request)
                self.assertIs(after, guard)
                self.assertEqual(reply, EmergencyStopResult(7, False))

    def test_late_command_enable_and_window_cannot_clear_stop(self):
        stopped, _ = handle_emergency_stop(self.guard, self.connection, self.request)
        command = ControlCommand(
            "session-a", 99, ControlTarget(9.0, 0.0), window_id="window-a"
        )
        self.assertEqual(
            update_windowed_command_guard(stopped, 100.02, command), stopped
        )
        with self.assertRaisesRegex(ValueError, "latched"):
            enable_control_session(stopped, 100.02)
        after, reply = handle_issue_control_window(
            stopped,
            self.connection,
            IssueControlWindowRequest(8, "session-a"),
            now_s=100.02,
        )
        self.assertEqual(after, stopped)
        self.assertFalse(reply.granted)

    def test_disconnect_and_reconnect_preserve_stop(self):
        stopped, _ = handle_emergency_stop(self.guard, self.connection, self.request)
        closed = handle_connection_closed(stopped, self.connection)
        self.assertTrue(closed.fault_latched)
        reopened, connection, _ = handle_open_session(
            closed, AuthenticatedConnection("desktop-a"), OpenSessionRequest(8)
        )
        self.assertTrue(reopened.fault_latched)
        self.assertFalse(reopened.enabled)
        self.assertNotEqual(connection.session_id, "session-a")


class TestEmergencyStopConnection(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState()
        self.before_close = None
        self.sock = Mock()
        self.sock.gettimeout.return_value = 3.0

    def _open(self, connection, request):
        self.guard, connection, reply = handle_open_session(
            self.guard, connection, request
        )
        self.guard = replace(
            self.guard,
            enabled=True,
            enabled_at_s=100.0,
            last_seq=2,
            last_accepted=AcceptedTarget(ControlTarget(1.0, 0.0), 100.0),
            control_window=ControlWindow("window-a", 100.0, 100.25),
        )
        return connection, reply

    def _stop(self, connection, request):
        self.guard, reply = handle_emergency_stop(self.guard, connection, request)
        return reply

    def _close(self, connection):
        self.before_close = self.guard
        self.guard = handle_connection_closed(self.guard, connection)

    def _serve(self, requests, callback):
        packets = [
            encode_authenticate_request(AuthenticateRequest(1, "desktop-a", "secret")),
            encode_open_session_request(OpenSessionRequest(2)),
        ]
        packets.extend(
            json.dumps(
                {
                    "version": 1,
                    "type": "emergency_stop",
                    "request_id": request_id,
                    "session_id": session_id,
                }
            ).encode("utf-8")
            for request_id, session_id in requests
        )
        self.sock.recv.side_effect = [b"".join(encode_record(p) for p in packets), b""]
        with patch("rover.vehicle.management_session.uuid4") as make_id:
            make_id.return_value = SimpleNamespace(hex="session-a")
            serve_management_connection(
                self.sock,
                {"desktop-a": b"secret"},
                self._open,
                self._close,
                emergency_stop=callback,
            )

    def test_wrong_owner_then_stop_and_repeat_share_stream(self):
        self._serve([(3, "other"), (4, "session-a"), (5, "session-a")], self._stop)
        replies = [
            decode_emergency_stop_result(decode_record(sent.args[0]))
            for sent in self.sock.sendall.call_args_list[2:]
        ]
        self.assertEqual(
            replies,
            [
                EmergencyStopResult(3, False),
                EmergencyStopResult(4, True),
                EmergencyStopResult(5, True),
            ],
        )
        self.assertTrue(self.before_close.fault_latched)
        self.assertTrue(self.guard.fault_latched)
        self.assertIsNone(self.guard.session_id)
        self.assertIsNone(self.guard.control_key)

    def test_missing_callback_denies_without_stopping(self):
        self._serve([(3, "session-a")], None)
        self.assertEqual(len(self.sock.sendall.call_args_list), 3)
        reply = decode_emergency_stop_result(
            decode_record(self.sock.sendall.call_args.args[0])
        )
        self.assertEqual(reply, EmergencyStopResult(3, False))
        self.assertFalse(self.before_close.fault_latched)

    def test_send_failure_after_stop_preserves_latch_during_cleanup(self):
        self.sock.sendall.side_effect = [None, None, OSError("send failed")]
        with self.assertRaisesRegex(OSError, "send failed"):
            self._serve([(3, "session-a")], self._stop)
        self.assertTrue(self.guard.fault_latched)
        self.assertIsNone(self.guard.session_id)
        self.assertIsNone(self.guard.control_key)
        self.assertIsNone(self.guard.control_window)


class TestEmergencyStopClient(unittest.TestCase):
    def test_client_allocates_ids_and_can_continue_after_denial(self):
        tls = Mock()
        tls.gettimeout.return_value = 3.0
        tls.recv.side_effect = [
            encode_record(
                json.dumps(
                    {
                        "version": 1,
                        "type": "emergency_stop_result",
                        "request_id": 1,
                        "accepted": False,
                    }
                ).encode("utf-8")
            ),
            encode_record(
                json.dumps(
                    {
                        "version": 1,
                        "type": "emergency_stop_result",
                        "request_id": 2,
                        "accepted": True,
                    }
                ).encode("utf-8")
            ),
        ]
        client = ManagementClient(tls)
        self.assertEqual(client.emergency_stop("other"), EmergencyStopResult(1, False))
        self.assertEqual(
            client.emergency_stop("session-a"), EmergencyStopResult(2, True)
        )
        messages = [
            json.loads(decode_record(sent.args[0]))
            for sent in tls.sendall.call_args_list
        ]
        self.assertEqual(
            messages,
            [
                {
                    "version": 1,
                    "type": "emergency_stop",
                    "request_id": 1,
                    "session_id": "other",
                },
                {
                    "version": 1,
                    "type": "emergency_stop",
                    "request_id": 2,
                    "session_id": "session-a",
                },
            ],
        )
