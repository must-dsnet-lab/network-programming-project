import socket
import unittest
from unittest.mock import Mock
from rover.core.control_target import ControlTarget
from rover.desktop.management_client import ManagementClient
from contextlib import contextmanager

from rover.desktop.secure_control import ControlSendCancelled, SecureControlSession
from rover.protocol.control_codec import ControlCommand, decode_windowed_command
from rover.protocol.management_codec import (
    ControlWindowResult,
    SessionEnableResult,
    SessionOpened,
)


class TestSecureControlSession(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.key = b"k" * 32
        self.target = ControlTarget(1.0, 0.0)
        self.address = ("127.0.0.1", 9999)

        self.client = Mock(spec=ManagementClient)
        self.client.open_session.return_value = SessionOpened(
            2, "session-a", self.key.hex()
        )
        self.client.issue_control_window.return_value = ControlWindowResult(
            3, True, "window-a", 0.25
        )
        self.client.enable_session.return_value = SessionEnableResult(4, True)

        self.udp = Mock(spec=socket.socket)
        self.udp.sendto.side_effect = lambda packet, address: len(packet)
        self.session = SecureControlSession(
            self.client, self.udp, self.address, clock=lambda: self.now
        )

    def test_open_reuses_session_without_enabling_or_sending(self):
        self.assertEqual(self.session.open(), "session-a")
        self.assertEqual(self.session.open(), "session-a")
        self.client.open_session.assert_called_once_with()
        self.client.enable_session.assert_not_called()
        self.udp.sendto.assert_not_called()

    def test_start_and_renew_send_signed_commands_with_increasing_sequence(self):
        self.client.issue_control_window.side_effect = [
            ControlWindowResult(3, True, "window-a", 0.25),
            ControlWindowResult(5, True, "window-b", 0.25),
        ]
        self.assertTrue(self.session.start(self.target))
        self.now = 0.10
        self.session.send(self.target)
        self.now = 0.15
        self.session.send(self.target)

        names = [item[0] for item in self.client.method_calls]
        self.assertEqual(
            names,
            [
                "open_session",
                "issue_control_window",
                "enable_session",
                "issue_control_window",
            ],
        )
        packets = []
        for sent in self.udp.sendto.call_args_list:
            packet, address = sent.args
            self.assertEqual(address, self.address)
            packets.append(decode_windowed_command(packet, self.key))
        self.assertEqual(
            packets,
            [
                ControlCommand("session-a", 0, self.target, "window-a"),
                ControlCommand("session-a", 1, self.target, "window-a"),
                ControlCommand("session-a", 2, self.target, "window-b"),
            ],
        )

    def test_enable_denial_leaves_sending_inactive(self):
        self.client.enable_session.return_value = SessionEnableResult(4, False)
        with self.assertRaises(PermissionError):
            self.session.start(self.target)
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)
        self.udp.sendto.assert_not_called()

    def test_window_expired_on_reply_prevents_enable(self):
        def slow_window(session_id):
            self.now = 0.25
            return ControlWindowResult(3, True, "window-a", 0.25)

        self.client.issue_control_window.side_effect = slow_window
        with self.assertRaises(TimeoutError):
            self.session.start(self.target)
        self.client.enable_session.assert_not_called()
        self.udp.sendto.assert_not_called()

    def test_enable_reply_after_window_expiry_prevents_first_packet(self):
        def slow_enable(session_id):
            self.now = 0.25
            return SessionEnableResult(4, True)

        self.client.enable_session.side_effect = slow_enable
        with self.assertRaises(TimeoutError):
            self.session.start(self.target)
        self.udp.sendto.assert_not_called()

    def test_renewal_denial_stops_without_reusing_old_window(self):
        self.session.start(self.target)
        self.now = 0.15
        self.client.issue_control_window.return_value = ControlWindowResult(5, False)
        with self.assertRaises(PermissionError):
            self.session.send(self.target)
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)
        self.assertEqual(self.udp.sendto.call_count, 1)

    def test_socket_error_blocks_later_sends(self):
        self.udp.sendto.side_effect = OSError("send failed")
        with self.assertRaises(OSError):
            self.session.start(self.target)
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)
        self.assertEqual(self.udp.sendto.call_count, 1)

    def test_stop_sending_blocks_later_sends(self):
        self.session.start(self.target)
        self.session.stop_sending()
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)
        self.assertEqual(self.udp.sendto.call_count, 1)

    @contextmanager
    def packet_gate(self):
        if self.stop_requested:
            raise ControlSendCancelled("stop requested")
        yield

    def test_stop_requested_during_enable_blocks_first_packet(self):
        self.stop_requested = False

        def enable_and_stop(session_id):
            self.stop_requested = True
            return SessionEnableResult(4, True)

        self.client.enable_session.side_effect = enable_and_stop
        with self.assertRaises(ControlSendCancelled):
            self.session.start(self.target, send_gate=self.packet_gate())

        self.udp.sendto.assert_not_called()
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)

    def test_stop_requested_during_renewal_blocks_next_packet(self):
        self.stop_requested = False
        self.session.start(self.target)
        self.now = 0.15

        def renew_and_stop(session_id):
            self.stop_requested = True
            return ControlWindowResult(5, True, "window-b", 0.25)

        self.client.issue_control_window.side_effect = renew_and_stop
        with self.assertRaises(ControlSendCancelled):
            self.session.send(self.target, send_gate=self.packet_gate())

        self.assertEqual(self.udp.sendto.call_count, 1)
        with self.assertRaises(RuntimeError):
            self.session.send(self.target)
