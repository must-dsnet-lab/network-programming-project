import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rover.protocol.management_codec import (
    AuthenticateRequest,
    ControlWindowResult,
    IssueControlWindowRequest,
    OpenSessionRequest,
    decode_control_window_result,
    decode_session_opened,
    decode_session_request,
    encode_authenticate_request,
    encode_issue_control_window_request,
    encode_open_session_request,
)
from rover.transport.tcp_records import decode_record, encode_record
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.management_connection import serve_management_connection
from rover.vehicle.management_session import (
    handle_connection_closed,
    handle_issue_control_window,
    handle_open_session,
)


class TestWindowRequestDispatch(unittest.TestCase):
    def test_routes_window_request(self):
        packet = encode_issue_control_window_request(
            IssueControlWindowRequest(12, "session-a")
        )
        self.assertEqual(
            decode_session_request(packet),
            IssueControlWindowRequest(12, "session-a"),
        )

    def test_rejects_invalid_or_missing_fields(self):
        for change in (
            {"request_id": -1},
            {"request_id": True},
            {"session_id": ""},
            {"session_id": None},
        ):
            message = dict(
                version=1,
                type="issue_control_window",
                request_id=12,
                session_id="session-a",
            )
            message.update(change)
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    decode_session_request(json.dumps(message).encode("utf-8"))

        with self.assertRaises(ValueError):
            decode_session_request(
                b'{"version":1,"type":"issue_control_window","request_id":12}'
            )


class TestWindowConnection(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState()
        self.before_close = None
        self.sock = Mock()
        self.sock.gettimeout.return_value = 5.0

    def _open(self, connection, request):
        self.guard, connection, reply = handle_open_session(
            self.guard, connection, request
        )
        return connection, reply

    def _issue(self, connection, request):
        self.guard, reply = handle_issue_control_window(
            self.guard, connection, request, now_s=100.0
        )
        return reply

    def _close(self, connection):
        self.before_close = self.guard
        self.guard = handle_connection_closed(self.guard, connection)

    def _serve(self, window_requests, issue):
        auth = AuthenticateRequest(6, "desktop-a", "test-secret-a")
        self.sock.recv.side_effect = [
            encode_record(encode_authenticate_request(auth))
            + encode_record(encode_open_session_request(OpenSessionRequest(7)))
            + b"".join(encode_record(packet) for packet in window_requests),
            b"",
        ]
        with patch("rover.vehicle.management_session.uuid4") as make_id:
            make_id.side_effect = [
                SimpleNamespace(hex="a" * 32),
                SimpleNamespace(hex="b" * 32),
            ]
            serve_management_connection(
                self.sock,
                {"desktop-a": b"test-secret-a"},
                self._open,
                self._close,
                issue_control_window=issue,
            )

    def test_grants_window_on_same_stream_and_cleans_up_at_eof(self):
        self._serve(
            [
                encode_issue_control_window_request(
                    IssueControlWindowRequest(12, "a" * 32)
                )
            ],
            self._issue,
        )
        packets = [
            decode_record(sent.args[0]) for sent in self.sock.sendall.call_args_list
        ]
        self.assertEqual(len(packets), 3)
        opened = decode_session_opened(packets[1])
        reply = decode_control_window_result(packets[2])
        self.assertEqual(reply.request_id, 12)
        self.assertTrue(reply.granted)
        self.assertEqual(reply.lifetime_s, 0.25)
        self.assertEqual(self.before_close.session_id, opened.session_id)
        self.assertEqual(self.before_close.control_window.window_id, reply.window_id)
        self.assertFalse(self.before_close.enabled)
        self.assertIsNone(self.guard.session_id)
        self.assertIsNone(self.guard.control_key)
        self.assertIsNone(self.guard.control_window)

    def test_wrong_session_is_denied_without_creating_window(self):
        self._serve(
            [
                encode_issue_control_window_request(
                    IssueControlWindowRequest(12, "session-other")
                )
            ],
            self._issue,
        )
        reply = decode_control_window_result(
            decode_record(self.sock.sendall.call_args.args[0])
        )
        self.assertEqual(reply, ControlWindowResult(12, False))
        self.assertIsNone(self.before_close.control_window)
        self.assertIsNone(self.guard.session_id)

    def test_missing_callback_denies_window(self):
        self._serve(
            [
                encode_issue_control_window_request(
                    IssueControlWindowRequest(12, "session-a")
                )
            ],
            None,
        )
        self.assertEqual(len(self.sock.sendall.call_args_list), 3)
        reply = decode_control_window_result(
            decode_record(self.sock.sendall.call_args.args[0])
        )
        self.assertEqual(reply, ControlWindowResult(12, False))
        self.assertIsNone(self.guard.session_id)

    def test_callback_error_still_closes_bound_session(self):
        def broken(connection, request):
            raise RuntimeError("window handler failed")

        with self.assertRaisesRegex(RuntimeError, "window handler failed"):
            self._serve(
                [
                    encode_issue_control_window_request(
                        IssueControlWindowRequest(12, "session-a")
                    )
                ],
                broken,
            )
        self.assertIsNotNone(self.before_close.session_id)
        self.assertIsNone(self.guard.session_id)
        self.assertIsNone(self.guard.control_key)

    def test_send_failure_clears_already_issued_window(self):
        self.sock.sendall.side_effect = [None, None, OSError("send failed")]
        with self.assertRaisesRegex(OSError, "send failed"):
            self._serve(
                [
                    encode_issue_control_window_request(
                        IssueControlWindowRequest(12, "a" * 32)
                    )
                ],
                self._issue,
            )
        self.assertIsNotNone(self.before_close.control_window)
        self.assertIsNone(self.guard.session_id)
        self.assertIsNone(self.guard.control_key)
        self.assertIsNone(self.guard.control_window)
