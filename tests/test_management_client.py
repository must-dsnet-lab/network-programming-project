import json
import unittest
from unittest.mock import Mock

from rover.desktop.management_client import ManagementClient
from rover.protocol.management_codec import (
    AuthenticationResult,
    ControlWindowResult,
    SessionEnableResult,
    SessionOpened,
    encode_authentication_result,
    encode_control_window_result,
    encode_session_enable_result,
    encode_session_opened,
)
from rover.transport.tcp_records import decode_record, encode_record


class TestManagementClient(unittest.TestCase):
    def _socket(self, *payloads):
        tls = Mock()
        tls.gettimeout.return_value = 3.0
        tls.recv.side_effect = [encode_record(p) for p in payloads] + [b""]
        return tls

    def _sent(self, tls):
        return [
            json.loads(decode_record(sent.args[0]))
            for sent in tls.sendall.call_args_list
        ]

    def test_all_request_types_share_increasing_ids(self):
        tls = self._socket(
            encode_authentication_result(AuthenticationResult(1, True)),
            encode_session_opened(SessionOpened(2, "session-a")),
            encode_session_enable_result(SessionEnableResult(3, True)),
            encode_control_window_result(
                ControlWindowResult(4, True, "window-a", 0.25)
            ),
        )
        client = ManagementClient(tls)
        replies = (
            client.authenticate("desktop-a", "test-secret-a"),
            client.open_session(),
            client.enable_session("session-a"),
            client.issue_control_window("session-a"),
        )
        self.assertEqual(
            [(p["type"], p["request_id"]) for p in self._sent(tls)],
            [
                ("authenticate", 1),
                ("open_session", 2),
                ("enable_session", 3),
                ("issue_control_window", 4),
            ],
        )
        self.assertEqual(
            replies,
            (
                AuthenticationResult(1, True),
                SessionOpened(2, "session-a"),
                SessionEnableResult(3, True),
                ControlWindowResult(4, True, "window-a", 0.25),
            ),
        )

    def test_each_connection_starts_at_one(self):
        for _ in range(2):
            tls = self._socket(
                encode_authentication_result(AuthenticationResult(1, True))
            )
            reply = ManagementClient(tls).authenticate("desktop-a", "secret")
            self.assertEqual(reply.request_id, 1)
            self.assertEqual([p["request_id"] for p in self._sent(tls)], [1])

    def test_business_denial_allows_next_request(self):
        tls = self._socket(
            encode_authentication_result(AuthenticationResult(1, True)),
            encode_session_opened(SessionOpened(2, "session-a")),
            encode_session_enable_result(SessionEnableResult(3, False)),
            encode_control_window_result(ControlWindowResult(4, False)),
            encode_session_enable_result(SessionEnableResult(5, True)),
        )
        client = ManagementClient(tls)
        client.authenticate("desktop-a", "secret")
        client.open_session()
        self.assertFalse(client.enable_session("session-other").enabled)
        self.assertFalse(client.issue_control_window("session-other").granted)
        self.assertTrue(client.enable_session("session-a").enabled)
        self.assertEqual([p["request_id"] for p in self._sent(tls)], [1, 2, 3, 4, 5])

    def test_protocol_or_transport_error_stops_reuse(self):
        cases = (
            (
                "wrong id",
                [
                    encode_record(
                        encode_authentication_result(AuthenticationResult(99, True))
                    )
                ],
                None,
                ValueError,
            ),
            (
                "wrong type",
                [encode_record(encode_session_opened(SessionOpened(1, "session-a")))],
                None,
                ValueError,
            ),
            ("eof", [b""], None, ConnectionError),
            ("recv timeout", [TimeoutError("test timeout")], None, TimeoutError),
            ("send error", [b""], OSError("test send error"), OSError),
        )
        for label, chunks, send_error, error_type in cases:
            with self.subTest(case=label):
                tls = Mock()
                tls.gettimeout.return_value = 3.0
                tls.recv.side_effect = chunks
                tls.sendall.side_effect = send_error
                client = ManagementClient(tls)
                with self.assertRaises(error_type):
                    client.authenticate("desktop-a", "secret")
                before = len(self._sent(tls))
                with self.assertRaisesRegex(ConnectionError, "unusable"):
                    client.open_session()
                self.assertEqual(len(self._sent(tls)), before)

    def test_buffered_extra_reply_is_not_discarded(self):
        tls = Mock()
        tls.gettimeout.return_value = 3.0
        tls.recv.side_effect = [
            encode_record(encode_authentication_result(AuthenticationResult(1, True)))
            + encode_record(encode_session_opened(SessionOpened(99, "session-a"))),
            b"",
        ]
        client = ManagementClient(tls)
        client.authenticate("desktop-a", "secret")
        with self.assertRaisesRegex(ValueError, "request_id"):
            client.open_session()

    def test_local_encoding_error_does_not_consume_id_or_connection(self):
        tls = self._socket(encode_authentication_result(AuthenticationResult(1, True)))
        client = ManagementClient(tls)
        with self.assertRaises(ValueError):
            client.authenticate("desktop-a", "")
        self.assertEqual(self._sent(tls), [])
        reply = client.authenticate("desktop-a", "secret")
        self.assertEqual(reply, AuthenticationResult(1, True))
        self.assertEqual([p["request_id"] for p in self._sent(tls)], [1])

    def test_authentication_denial_ends_client_flow(self):
        tls = self._socket(encode_authentication_result(AuthenticationResult(1, False)))
        client = ManagementClient(tls)
        self.assertFalse(client.authenticate("desktop-a", "wrong").accepted)
        with self.assertRaisesRegex(ConnectionError, "unusable"):
            client.open_session()
        self.assertEqual([p["request_id"] for p in self._sent(tls)], [1])
