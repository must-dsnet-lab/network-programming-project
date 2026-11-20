import json
import unittest
from dataclasses import replace

from rover.core.control_target import ControlTarget
from rover.protocol.management_codec import (
    MAX_MANAGEMENT_MESSAGE_BYTES,
    ControlWindowResult,
    IssueControlWindowRequest,
    decode_control_window_result,
    decode_issue_control_window_request,
    encode_control_window_result,
    encode_issue_control_window_request,
)
from rover.vehicle.control_guard import ControlGuardState
from rover.vehicle.control_watchdog import AcceptedTarget
from rover.vehicle.control_window import ControlWindow
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    handle_issue_control_window,
)


class TestControlWindowMessages(unittest.TestCase):
    def test_request_wire_format_and_round_trip(self):
        request = IssueControlWindowRequest(12, "session-a")
        packet = encode_issue_control_window_request(request)

        self.assertEqual(
            json.loads(packet),
            {
                "version": 1,
                "type": "issue_control_window",
                "request_id": 12,
                "session_id": "session-a",
            },
        )
        self.assertEqual(decode_issue_control_window_request(packet), request)

    def test_successful_result_wire_format_and_round_trip(self):
        reply = ControlWindowResult(12, True, "window-a", 0.25)
        packet = encode_control_window_result(reply)

        self.assertEqual(
            json.loads(packet),
            {
                "version": 1,
                "type": "control_window_result",
                "request_id": 12,
                "granted": True,
                "window_id": "window-a",
                "lifetime_s": 0.25,
            },
        )
        self.assertEqual(decode_control_window_result(packet), reply)

    def test_denied_result_has_no_window_data(self):
        reply = ControlWindowResult(12, False)
        packet = encode_control_window_result(reply)

        self.assertEqual(
            json.loads(packet),
            {
                "version": 1,
                "type": "control_window_result",
                "request_id": 12,
                "granted": False,
                "window_id": None,
                "lifetime_s": None,
            },
        )
        self.assertEqual(decode_control_window_result(packet), reply)

    def test_request_codecs_reject_invalid_fields(self):
        for request_id, session_id in (
            (-1, "session-a"),
            (True, "session-a"),
            (12, ""),
            (12, None),
        ):
            request = IssueControlWindowRequest(request_id, session_id)
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "issue_control_window",
                    "request_id": request_id,
                    "session_id": session_id,
                }
            ).encode("utf-8")
            for func, value in (
                (encode_issue_control_window_request, request),
                (decode_issue_control_window_request, packet),
            ):
                with self.subTest(func=func.__name__, request=request):
                    with self.assertRaises(ValueError):
                        func(value)

    def test_result_codecs_reject_invalid_combinations(self):
        changes = (
            {"request_id": True},
            {"granted": 1},
            {"window_id": None},
            {"window_id": " "},
            {"lifetime_s": None},
            {"lifetime_s": 0},
            {"lifetime_s": True},
            {"lifetime_s": float("nan")},
            {"lifetime_s": float("inf")},
            {"granted": False},
        )
        for change in changes:
            values = dict(
                request_id=12,
                granted=True,
                window_id="window-a",
                lifetime_s=0.25,
            )
            values.update(change)
            reply = ControlWindowResult(**values)
            packet = json.dumps(
                {
                    "version": 1,
                    "type": "control_window_result",
                    **values,
                }
            ).encode("utf-8")
            for func, value in (
                (encode_control_window_result, reply),
                (decode_control_window_result, packet),
            ):
                with self.subTest(func=func.__name__, change=change):
                    with self.assertRaises(ValueError):
                        func(value)

    def test_decoders_check_envelope_and_size(self):
        cases = (
            (
                decode_issue_control_window_request,
                {
                    "version": 1,
                    "type": "issue_control_window",
                    "request_id": 12,
                    "session_id": "session-a",
                },
                "session_id",
            ),
            (
                decode_control_window_result,
                {
                    "version": 1,
                    "type": "control_window_result",
                    "request_id": 12,
                    "granted": True,
                    "window_id": "window-a",
                    "lifetime_s": 0.25,
                },
                "window_id",
            ),
        )
        for decoder, base, required in cases:
            bad_version = dict(base, version=2)
            bad_type = dict(base, type="control_target")
            missing = base.copy()
            del missing[required]
            packets = (
                json.dumps(bad_version).encode("utf-8"),
                json.dumps(bad_type).encode("utf-8"),
                json.dumps(missing).encode("utf-8"),
                b"[]",
                b"{" * (MAX_MANAGEMENT_MESSAGE_BYTES + 1),
            )
            for packet in packets:
                with self.subTest(decoder=decoder.__name__, packet=packet[:60]):
                    with self.assertRaises(ValueError):
                        decoder(packet)

    def test_encoders_check_whole_packet_size(self):
        cases = (
            (
                encode_issue_control_window_request,
                IssueControlWindowRequest(12, "s" * MAX_MANAGEMENT_MESSAGE_BYTES),
            ),
            (
                encode_control_window_result,
                ControlWindowResult(12, True, "w" * MAX_MANAGEMENT_MESSAGE_BYTES, 0.25),
            ),
        )
        for encoder, value in cases:
            with self.subTest(encoder=encoder.__name__):
                with self.assertRaisesRegex(ValueError, "too large"):
                    encoder(value)


class TestIssueControlWindow(unittest.TestCase):
    def setUp(self):
        self.guard = ControlGuardState(
            session_id="session-a",
            control_key=b"k" * 32,
        )
        self.connection = AuthenticatedConnection("desktop-a", "session-a")
        self.request = IssueControlWindowRequest(12, "session-a")

    def _moving_guard(self):
        return replace(
            self.guard,
            enabled=True,
            enabled_at_s=100.0,
            last_seq=3,
            last_accepted=AcceptedTarget(ControlTarget(1.0, 0.0), 100.1),
            control_window=ControlWindow("window-old", 100.0, 100.25),
        )

    def test_owner_can_issue_before_enable(self):
        after, reply = handle_issue_control_window(
            self.guard, self.connection, self.request, now_s=100.0
        )

        self.assertTrue(reply.granted)
        window = after.control_window
        self.assertRegex(window.window_id, r"^[0-9a-f]{32}$")
        self.assertEqual(window.issued_at_s, 100.0)
        self.assertEqual(window.expires_at_s, 100.25)
        self.assertEqual(reply, ControlWindowResult(12, True, window.window_id, 0.25))
        self.assertEqual(replace(after, control_window=None), self.guard)

    def test_renewal_changes_only_window(self):
        before = self._moving_guard()
        after, reply = handle_issue_control_window(
            before, self.connection, self.request, now_s=100.2
        )

        self.assertTrue(reply.granted)
        self.assertNotEqual(after.control_window.window_id, "window-old")
        self.assertAlmostEqual(after.control_window.expires_at_s, 100.45)
        self.assertEqual(replace(after, control_window=before.control_window), before)

    def test_denies_wrong_owner_missing_key_or_fault(self):
        cases = (
            (self.guard, AuthenticatedConnection("desktop-a"), self.request),
            (
                self.guard,
                AuthenticatedConnection("desktop-a", "session-old"),
                self.request,
            ),
            (
                self.guard,
                self.connection,
                IssueControlWindowRequest(12, "session-other"),
            ),
            (replace(self.guard, session_id=None), self.connection, self.request),
            (replace(self.guard, control_key=None), self.connection, self.request),
            (replace(self.guard, control_key=b""), self.connection, self.request),
            (replace(self.guard, fault_latched=True), self.connection, self.request),
        )
        for guard, connection, request in cases:
            with self.subTest(guard=guard, connection=connection, request=request):
                after, reply = handle_issue_control_window(
                    guard, connection, request, now_s=100.0
                )
                self.assertIs(after, guard)
                self.assertEqual(reply, ControlWindowResult(12, False))

    def test_expired_window_can_renew_before_watchdog_timeout(self):
        before = self._moving_guard()
        after, reply = handle_issue_control_window(
            before, self.connection, self.request, now_s=100.3
        )

        self.assertTrue(reply.granted)
        self.assertAlmostEqual(after.control_window.expires_at_s, 100.55)
        self.assertEqual(replace(after, control_window=before.control_window), before)

    def test_renewal_requests_do_not_keep_control_alive(self):
        guard = self._moving_guard()

        for request_id, now_s in enumerate((100.2, 100.4, 100.59), start=12):
            guard, reply = handle_issue_control_window(
                guard,
                self.connection,
                IssueControlWindowRequest(request_id, "session-a"),
                now_s=now_s,
            )
            self.assertTrue(reply.granted)
            self.assertEqual(guard.last_accepted.accepted_at_s, 100.1)

        guard, reply = handle_issue_control_window(
            guard,
            self.connection,
            IssueControlWindowRequest(15, "session-a"),
            now_s=100.6,
        )
        self.assertFalse(reply.granted)
        self.assertTrue(guard.fault_latched)
        self.assertFalse(guard.enabled)
        self.assertIsNone(guard.last_accepted)
        self.assertEqual(guard.last_seq, 3)
