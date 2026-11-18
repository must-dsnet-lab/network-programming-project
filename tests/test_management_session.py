import unittest
from rover.protocol.management_codec import OpenSessionRequest
from rover.vehicle.control_guard import ControlGuardState, enable_control_session
from rover.vehicle.management_session import (
    AuthenticatedConnection,
    handle_open_session,
    handle_connection_closed,
)
from rover.vehicle.management_session import verify_client_credential


class TestVerifyClientCredential(unittest.TestCase):
    def test_accepts_matching_client_and_credential(self):
        trusted = {"desktop-a": b"test-secret-a"}
        self.assertTrue(
            verify_client_credential(trusted, "desktop-a", b"test-secret-a")
        )

    def test_rejects_unmatched_credentials(self):
        trusted = {
            "desktop-a": b"test-secret-a",
            "desktop-b": b"test-secret-b",
            "unconfigured": b"",
        }
        cases = (
            ("wrong credential", "desktop-a", b"wrong"),
            ("another client's credential", "desktop-a", b"test-secret-b"),
            ("unknown client", "desktop-x", b"test-secret-a"),
            ("empty credential", "desktop-a", b""),
            ("empty configured credential", "unconfigured", b""),
        )
        for label, client_id, credential in cases:
            with self.subTest(case=label):
                self.assertFalse(
                    verify_client_credential(trusted, client_id, credential)
                )


class TestOpenSession(unittest.TestCase):
    def test_registers_session_before_reply(self):
        guard, connection, reply = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(request_id=7),
        )

        # 断言 reply.session_id 必须是恰好 32 位的纯小写十六进制字符串
        self.assertRegex(reply.session_id, r"^[0-9a-f]{32}$")
        self.assertEqual(guard.session_id, reply.session_id)
        self.assertEqual(connection.session_id, reply.session_id)
        self.assertEqual(reply.request_id, 7)
        self.assertFalse(guard.enabled)

    # 第二条测试特意在两次申请之间先使能，验证重复申请连原来的 guard 对象都原样返回
    def test_same_connection_reuses_session_and_preserves_state(self):
        guard, connection, first_reply = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(request_id=7),
        )
        guard = enable_control_session(guard, now_s=100.0)

        next_guard, next_connection, reply = handle_open_session(
            guard,
            connection,
            OpenSessionRequest(request_id=8),
        )

        self.assertIs(next_guard, guard)
        self.assertIs(next_connection, connection)
        self.assertEqual(reply.session_id, first_reply.session_id)
        self.assertEqual(reply.request_id, 8)

    # 第三条测试重新创建 AuthenticatedConnection("desktop-a")，模拟同一账号另开一条连接。
    def test_new_connection_with_same_client_cannot_take_over(self):
        guard, _, _ = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(request_id=7),
        )

        with self.assertRaisesRegex(ValueError, "another control session"):
            handle_open_session(
                guard,
                AuthenticatedConnection("desktop-a"),
                OpenSessionRequest(request_id=8),
            )


class TestConnectionClosed(unittest.TestCase):
    def test_owner_disconnect_revokes_control(self):
        guard, connection, _ = handle_open_session(
            ControlGuardState(),
            AuthenticatedConnection("desktop-a"),
            OpenSessionRequest(request_id=7),
        )
        guard = enable_control_session(guard, now_s=100.0)

        closed = handle_connection_closed(guard, connection)

        self.assertIsNone(closed.session_id)
        self.assertFalse(closed.enabled)
        self.assertIsNone(closed.enabled_at_s)
        self.assertIs(handle_connection_closed(closed, connection), closed)

    def test_unbound_or_stale_connection_cannot_close_current_session(self):
        guard = ControlGuardState(
            session_id="session-new",
            enabled=True,
            enabled_at_s=100.0,
        )
        for old_session_id in (None, "session-old"):
            with self.subTest(old_session_id=old_session_id):
                connection = AuthenticatedConnection(
                    "desktop-a",
                    session_id=old_session_id,
                )
                self.assertIs(handle_connection_closed(guard, connection), guard)
