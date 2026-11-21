import threading
import unittest
from contextlib import contextmanager
from queue import Empty, Queue
from unittest.mock import MagicMock, Mock, patch

from rover.core.control_target import ControlTarget
from rover.desktop.control_worker import SecureControlWorker
from rover.desktop.management_client import ManagementClient
from rover.protocol.control_codec import decode_windowed_command
from rover.protocol.management_codec import (
    ControlWindowResult,
    SessionEnableResult,
    SessionOpened,
)


class TestSecureControlWorker(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.address = ("127.0.0.1", 9999)
        self.packets = Queue()
        self.connected_on = []
        self.released = threading.Event()

        self.client = Mock(spec=ManagementClient)
        self.client.open_session.return_value = SessionOpened(
            2, "session-a", self.key.hex()
        )
        self.client.enable_session.return_value = SessionEnableResult(4, True)
        self.client.issue_control_window.return_value = ControlWindowResult(
            3, True, "window-a", 0.25
        )

        self.udp = MagicMock()
        self.udp.__enter__.return_value = self.udp
        self.udp.sendto.side_effect = self.capture_packet
        self.socket_patch = patch("socket.socket", return_value=self.udp)
        self.socket_patch.start()
        self.addCleanup(self.socket_patch.stop)

        self.worker = SecureControlWorker(self.connect, self.address)
        self.addCleanup(self.close_worker)

    @contextmanager
    def connect(self):
        self.connected_on.append(threading.get_ident())
        try:
            yield self.client
        finally:
            self.released.set()

    def capture_packet(self, packet, address):
        self.packets.put((packet, address))
        return len(packet)

    def receive_command(self):
        try:
            packet, address = self.packets.get(timeout=1.0)
        except Empty:
            self.fail("worker did not send a command")
        self.assertEqual(address, self.address)
        return decode_windowed_command(packet, self.key)

    def close_worker(self):
        self.worker.close()
        self.assertTrue(self.worker.wait_closed(2.0))

    def test_uses_one_background_thread_and_sends_periodically(self):
        gui_thread = threading.get_ident()
        self.worker.start()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))

        first = self.receive_command()
        second = self.receive_command()
        self.assertEqual((first.seq, second.seq), (0, 1))
        self.assertEqual((first.target.speed_mps, second.target.speed_mps), (1.0, 1.0))
        self.assertEqual(len(self.connected_on), 1)
        self.assertNotEqual(self.connected_on[0], gui_thread)

        self.close_worker()
        self.assertTrue(self.released.is_set())
        self.assertEqual(self.worker.status().state, "closed")
        self.udp.__exit__.assert_called_once()

    def test_stop_then_apply_blocks_the_old_first_packet(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)

        def enable(session_id):
            if not entered.is_set():
                entered.set()
                if not release.wait(2.0):
                    raise TimeoutError("test did not release enable")
            return SessionEnableResult(4, True)

        self.client.enable_session.side_effect = enable
        self.worker.start()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.assertTrue(entered.wait(1.0))

        self.worker.stop_sending()
        self.assertTrue(self.worker.apply_target(ControlTarget(0.5, 0.0)))
        release.set()

        first = self.receive_command()
        self.assertEqual((first.seq, first.target.speed_mps), (0, 0.5))

    def test_idle_keeps_management_connection_without_sending_control(self):
        heartbeat = threading.Event()

        def open_session():
            request_id = self.client.open_session.call_count + 1
            if request_id > 2:
                heartbeat.set()
            return SessionOpened(request_id, "session-a", self.key.hex())

        self.client.open_session.side_effect = open_session
        with patch("rover.desktop.control_worker.KEEPALIVE_S", 0.02):
            self.worker.start()
            self.assertTrue(heartbeat.wait(1.0))
            self.close_worker()

        self.client.enable_session.assert_not_called()
        self.udp.sendto.assert_not_called()

    def test_send_error_closes_connection_and_reports_failure(self):
        self.udp.sendto.side_effect = OSError("send failed")
        self.worker.start()
        self.assertTrue(self.worker.apply_target(ControlTarget(1.0, 0.0)))
        self.assertTrue(self.worker.wait_closed(1.0))

        self.assertTrue(self.released.is_set())
        self.assertEqual(self.worker.status().state, "failed")
        self.assertIn("send failed", self.worker.status().detail)
        self.assertFalse(self.worker.apply_target(ControlTarget(0.5, 0.0)))
