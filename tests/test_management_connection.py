import unittest
from unittest.mock import Mock, call

from rover.protocol.management_codec import (
    AuthenticateRequest,
    AuthenticationResult,
    OpenSessionRequest,
    SessionOpened,
    decode_authentication_result,
    decode_session_opened,
    encode_authenticate_request,
    encode_open_session_request,
    EnableSessionRequest,
    SessionEnableResult,
    decode_session_enable_result,
    encode_enable_session_request,
)
from rover.transport.tcp_records import decode_record, encode_record
from rover.vehicle.management_connection import serve_management_connection
from rover.vehicle.management_session import AuthenticatedConnection


class TestManagementConnection(unittest.TestCase):
    # 第一项测试故意让一次 recv() 同时收到认证记录和会话申请记录。
    # 如果认证后重新创建一个 iter_records()，第一份迭代器里尚未交出的会话申请就会丢掉。
    # 第二次 recv() 返回 b""，模拟对端断开。
    def test_authentication_and_session_share_the_record_stream(self):
        auth = AuthenticateRequest(6, "desktop-a", "test-secret-a")
        request = OpenSessionRequest(7)
        sock = Mock()
        sock.recv.side_effect = [
            encode_record(encode_authenticate_request(auth))
            + encode_record(encode_open_session_request(request)),
            b"",
        ]

        bound = AuthenticatedConnection("desktop-a", "session-a")
        reply = SessionOpened(7, "session-a")
        open_session = Mock(return_value=(bound, reply))
        close_connection = Mock()

        serve_management_connection(
            sock,
            {"desktop-a": b"test-secret-a"},
            open_session,
            close_connection,
        )

        open_session.assert_called_once_with(
            AuthenticatedConnection("desktop-a"),
            request,
        )
        close_connection.assert_called_once_with(bound)
        auth_record, session_record = (
            call.args[0] for call in sock.sendall.call_args_list
        )
        self.assertEqual(
            decode_authentication_result(decode_record(auth_record)),
            AuthenticationResult(6, True),
        )
        self.assertEqual(decode_session_opened(decode_record(session_record)), reply)

    def test_rejected_credentials_do_not_open_a_session(self):
        sock = Mock()
        auth = AuthenticateRequest(6, "desktop-a", "wrong")
        sock.recv.side_effect = [encode_record(encode_authenticate_request(auth)), b""]
        open_session = Mock()
        close_connection = Mock()

        serve_management_connection(
            sock,
            {"desktop-a": b"test-secret-a"},
            open_session,
            close_connection,
        )

        open_session.assert_not_called()
        close_connection.assert_not_called()
        sock.sendall.assert_called_once()
        self.assertEqual(
            decode_authentication_result(decode_record(sock.sendall.call_args.args[0])),
            AuthenticationResult(6, False),
        )

    def test_enable_requests_use_the_bound_connection(self):
        auth = AuthenticateRequest(6, "desktop-a", "test-secret-a")
        opened = OpenSessionRequest(7)
        wrong = EnableSessionRequest(8, "session-other")
        right = EnableSessionRequest(9, "session-a")

        sock = Mock()
        sock.recv.side_effect = [
            encode_record(encode_authenticate_request(auth))
            + encode_record(encode_open_session_request(opened))
            + encode_record(encode_enable_session_request(wrong))
            + encode_record(encode_enable_session_request(right)),
            b"",
        ]
        bound = AuthenticatedConnection("desktop-a", "session-a")
        open_session = Mock(return_value=(bound, SessionOpened(7, "session-a")))
        enable_session = Mock(side_effect=[False, True])
        close_connection = Mock()

        serve_management_connection(
            sock,
            {"desktop-a": b"test-secret-a"},
            open_session,
            close_connection,
            enable_session=enable_session,
        )

        self.assertEqual(
            enable_session.call_args_list,
            [call(bound, wrong), call(bound, right)],
        )
        replies = [
            decode_session_enable_result(decode_record(sent.args[0]))
            for sent in sock.sendall.call_args_list[2:]
        ]
        self.assertEqual(
            replies,
            [SessionEnableResult(8, False), SessionEnableResult(9, True)],
        )
        close_connection.assert_called_once_with(bound)
