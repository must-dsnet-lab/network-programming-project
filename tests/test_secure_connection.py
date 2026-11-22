import json
import ssl
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from rover.desktop.expert_management_client import ExpertManagementClient
from rover.desktop.management_client import ManagementClient
from rover.desktop.secure_connection import (
    AuthenticationRejected,
    ConnectionSetupError,
    connect_configured_management,
    connect_expert_management,
    connect_management,
)
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

    def connect_configured(self, mode="advanced"):
        settings = SimpleNamespace(
            mode=mode,
            management_address=("192.0.2.1", 19178),
            ca_file=Path("other-public-ca.pem"),
            tls_server_name="rover.example",
            client_id="desktop-b",
        )
        return connect_configured_management(settings, "test-secret-b")

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
        with self.assertRaises(AuthenticationRejected):
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

    def test_configured_connection_uses_expert_mode_and_profile_endpoints(self):
        self.tls.incoming = bytearray(encode_record(
            encode_expert_management("authentication_result", 1, accepted=True)
        ))

        with self.connect_configured("expert") as client:
            self.assertIsInstance(client, ExpertManagementClient)

        self.context_factory.assert_called_once_with(cafile="other-public-ca.pem")
        self.create_connection.assert_called_once_with(("192.0.2.1", 19178), timeout=3.0)
        self.context.wrap_socket.assert_called_once_with(self.raw, server_hostname="rover.example")
        self.assertEqual(self.messages()[0]["client_id"], "desktop-b")
        self.assertTrue(self.tls.closed)

    def test_configured_connection_distinguishes_authentication_rejection_from_os_permission_error(self):
        self.tls.incoming = bytearray(encode_record(
            encode_authentication_result(AuthenticationResult(1, False))
        ))

        with self.assertRaisesRegex(ConnectionSetupError, "认证被拒") as auth_error:
            with self.connect_configured():
                self.fail("rejected credentials must not create a client")
        self.assertEqual(auth_error.exception.kind, "authentication")
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

        self.create_connection.side_effect = PermissionError("test-secret-b")
        with self.assertRaisesRegex(ConnectionSetupError, "无法连接车端") as network_error:
            with self.connect_configured():
                self.fail("OS permission failures must not create a client")
        self.assertEqual(network_error.exception.kind, "network")
        self.assertNotIn("test-secret-b", str(network_error.exception))

    def test_configured_connection_classifies_certificate_failure_without_sending_credentials(self):
        self.context.wrap_socket.side_effect = ssl.SSLCertVerificationError(1, "test-secret-b")

        with self.assertRaisesRegex(ConnectionSetupError, "证书校验失败") as raised:
            with self.connect_configured():
                self.fail("an unverified socket must not create a client")

        self.assertEqual(raised.exception.kind, "certificate")
        self.assertNotIn("test-secret-b", str(raised.exception))
        self.assertEqual(self.tls.sent, [])
        self.assertTrue(self.raw.closed)

    def test_configured_connection_classifies_incompatible_management_reply(self):
        self.tls.incoming = bytearray(b"\x00\x00")

        with self.assertRaisesRegex(ConnectionSetupError, "管理协议") as raised:
            with self.connect_configured():
                self.fail("an incomplete reply must not create a client")

        self.assertEqual(raised.exception.kind, "protocol")
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)

    def test_configured_connection_distinguishes_invalid_ca_from_tls_handshake_failure(self):
        self.context_factory.side_effect = ssl.SSLError("invalid CA input")
        with self.assertRaisesRegex(ConnectionSetupError, "证书校验失败") as certificate:
            with self.connect_configured():
                self.fail("invalid CA must not create a client")
        self.assertEqual(certificate.exception.kind, "certificate")
        self.create_connection.assert_not_called()

        self.context_factory.side_effect = None
        self.context.wrap_socket.side_effect = ssl.SSLError("handshake problem")
        with self.assertRaisesRegex(ConnectionSetupError, "TLS 握手失败") as protocol:
            with self.connect_configured():
                self.fail("an incomplete handshake must not create a client")
        self.assertEqual(protocol.exception.kind, "protocol")
        self.assertEqual(self.tls.sent, [])
        self.assertTrue(self.raw.closed)

    def test_configured_connection_does_not_reclassify_runtime_errors(self):
        error = PermissionError("worker failure")

        with self.assertRaises(PermissionError) as raised:
            with self.connect_configured() as client:
                self.assertIsInstance(client, ManagementClient)
                raise error

        self.assertIs(raised.exception, error)
        self.assertTrue(self.tls.closed)
        self.assertTrue(self.raw.closed)
