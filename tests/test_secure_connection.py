import json
import ssl
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.desktop.management_client import ManagementClient
from rover.desktop.secure_connection import connect_expert_management, connect_management
from rover.protocol.expert_management_codec import encode_expert_management
from rover.protocol.management_codec import (
    AuthenticationResult,
    SessionOpened,
    encode_authentication_result,
    encode_session_opened,
)
from rover.transport.tcp_records import decode_record, encode_record


class MemorySocket:
    """只替代网络；管理编号、编解码和TCP分帧仍运行真实代码。"""

    def __init__(self, *payloads):
        self.incoming = bytearray(b"".join(encode_record(p) for p in payloads))
        self.sent = []
        self.closed = False
        self.timeout_s = 3.0

    def recv(self, size):
        chunk = bytes(self.incoming[:size])
        del self.incoming[:size]
        return chunk

    def gettimeout(self):
        return self.timeout_s

    def settimeout(self, timeout_s):
        self.timeout_s = timeout_s

    def sendall(self, packet):
        self.sent.append(packet)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True


class TestSecureConnection(unittest.TestCase):
    def setUp(self):
        self.raw = MemorySocket()
        self.tls = MemorySocket(
            encode_authentication_result(AuthenticationResult(1, True)),
            encode_session_opened(SessionOpened(2, "session-a")),
        )
        self.context = Mock(spec=ssl.SSLContext)
        self.context.wrap_socket.return_value = self.tls

        context_patch = patch(
            "rover.desktop.secure_connection.ssl.create_default_context",
            return_value=self.context,
        )
        socket_patch = patch(
            "rover.desktop.secure_connection.socket.create_connection",
            return_value=self.raw,
        )
        self.context_factory = context_patch.start()
        self.addCleanup(context_patch.stop)
        self.create_connection = socket_patch.start()
        self.addCleanup(socket_patch.stop)

    def connect(self):
        return connect_management(
            ("127.0.0.1", 19177),
            ca_file=Path("public-ca.pem"),
            server_hostname="localhost",
            client_id="desktop-a",
            credential="test-secret-a",
        )

    def messages(self):
        return [
            json.loads(decode_record(packet))
            for packet in self.tls.sent
        ]

    def test_verified_connection_authenticates_before_use(self):
        with self.connect() as client:
            self.assertIsInstance(client, ManagementClient)
            opened = client.open_session()
            self.assertEqual(opened, SessionOpened(2, "session-a"))
            self.assertFalse(self.tls.closed)

        self.context_factory.assert_called_once_with(cafile="public-ca.pem")
        self.create_connection.assert_called_once_with(
            ("127.0.0.1", 19177), timeout=3.0
        )
        self.context.wrap_socket.assert_called_once_with(
            self.raw, server_hostname="localhost"
        )
        self.assertEqual(
            [(p["type"], p["request_id"]) for p in self.messages()],
            [("authenticate", 1), ("open_session", 2)],
        )
        self.assertEqual(self.messages()[0]["client_id"], "desktop-a")
        self.assertEqual(self.messages()[0]["credential"], "test-secret-a")
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_expert_connection_uses_verified_tls_and_authenticates_with_version_two(self):
        self.tls = MemorySocket(
            encode_expert_management("authentication_result", 1, accepted=True),
            encode_expert_management("session_opened", 2, session_id="expert-session-a"),
        )
        self.context.wrap_socket.return_value = self.tls
        with connect_expert_management(
            ("127.0.0.1", 19177),
            ca_file=Path("public-ca.pem"),
            server_hostname="localhost",
            client_id="desktop-a",
            credential="test-secret-a",
        ) as client:
            self.assertIsInstance(client, ExpertManagementClient)
            self.assertEqual(client.open_session(), "expert-session-a")

        self.context_factory.assert_called_once_with(cafile="public-ca.pem")
        self.context.wrap_socket.assert_called_once_with(self.raw, server_hostname="localhost")
        self.assertEqual([message["version"] for message in self.messages()], [2, 2])
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_rejected_credentials_close_connection_without_opening_session(self):
        self.tls.incoming = bytearray(encode_record(
            encode_authentication_result(AuthenticationResult(1, False))
        ))
        with self.assertRaises(PermissionError):
            with self.connect():
                self.fail("rejected client must not be handed to the caller")

        self.assertEqual(
            [p["type"] for p in self.messages()], ["authenticate"]
        )
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_certificate_failure_sends_no_credentials(self):
        self.context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
            1, "certificate rejected"
        )
        with self.assertRaises(ssl.SSLCertVerificationError):
            with self.connect():
                self.fail("unverified connection must not be handed to the caller")

        self.assertEqual(self.tls.sent, [])
        self.assertEqual(self.raw.sent, [])
        self.assertTrue(self.raw.closed)
        self.create_connection.assert_called_once()

    def test_caller_exception_still_closes_connection(self):
        with self.assertRaisesRegex(RuntimeError, "caller failed"):
            with self.connect():
                raise RuntimeError("caller failed")

        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_incomplete_authentication_reply_closes_connection(self):
        self.tls.incoming = bytearray(b"\x00\x00")
        with self.assertRaisesRegex(ValueError, "incomplete record"):
            with self.connect():
                self.fail("incomplete reply must not authenticate the client")

        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_missing_ca_prevents_tcp_connection(self):
        self.context_factory.side_effect = FileNotFoundError("CA file missing")
        with self.assertRaises(FileNotFoundError):
            with self.connect():
                self.fail("missing CA must prevent connection")

        self.create_connection.assert_not_called()
