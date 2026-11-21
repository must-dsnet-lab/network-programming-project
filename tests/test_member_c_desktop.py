import csv
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.acceptance import TELEMETRY_DELAY_S, TelemetryDelivery
from rover.desktop.app import DesktopConsole
from rover.desktop.desktop_map import VehicleMap
from rover.desktop.control_worker import SecureControlWorker, WorkerStatus
from rover.desktop.secure_control import SecureControlSession
from rover.protocol.control_codec import decode_windowed_command
from rover.protocol.management_codec import ControlWindowResult, SessionEnableResult, SessionOpened
from rover.protocol.telemetry_codec import TelemetrySnapshot
from tests.test_desktop_control import FakeSocket


class MemberCDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def make_window(self, **options):
        self.sent = []
        sockets = patch("rover.desktop.app.socket.socket", side_effect=lambda *args: FakeSocket(self.sent))
        sockets.start()
        self.addCleanup(sockets.stop)
        window = DesktopConsole(**options)
        self.addCleanup(window.close)
        return window

    def test_observer_never_sends_controls(self):
        window = self.make_window(observe=True)
        self.assertIsNone(window.control_sock)
        window.start_control()
        self.assertEqual(self.sent, [])
        self.assertFalse(window.control_timer.isActive())

    def test_map_zoom_keeps_position_under_cursor_and_can_center_car(self):
        view = VehicleMap()
        self.addCleanup(view.close)
        point = view.screen_point(2, 3)
        view.zoom_at(1.25, point)
        self.assertEqual(view.screen_point(2, 3), point)
        view.set_state(VehicleState(x_m=2, y_m=3))
        view.center_vehicle()
        self.assertEqual(view.screen_point(2, 3), QPointF(view.width() // 2, view.height() // 2))

    def test_pause_freezes_display_but_keeps_raw_telemetry_for_csv(self):
        window = self.make_window()
        window.begin_recording()
        first = TelemetrySnapshot(state=VehicleState(x_m=1), fault_latched=False)
        second = TelemetrySnapshot(state=VehicleState(x_m=2), fault_latched=True)
        peer = ("127.0.0.1", 41000)
        with patch("rover.desktop.app.try_receive_telemetry", side_effect=[(first, peer), None, (second, peer), None]):
            window.poll_telemetry()
            window.set_telemetry_fault("pause")
            window.poll_telemetry()
        self.assertEqual(window.map.state.x_m, 1)
        self.assertEqual(window.pose_values[0].text(), "1.000")
        self.assertEqual(window.protection_value.text(), "保护未锁存")
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "trial.csv"
            window.write_recording(destination)
            with destination.open(encoding="utf-8-sig", newline="") as source:
                rows = list(csv.DictReader(source))
        samples = [row for row in rows if row["event"] == "telemetry"]
        self.assertEqual([float(row["x_m"]) for row in samples], [1, 2])
        self.assertEqual(samples[-1]["fault_latched"], "1")

    def test_delayed_display_keeps_original_arrival_time_and_drops_old_queue_on_recovery(self):
        delivery = TelemetryDelivery()
        snapshot = TelemetrySnapshot(state=VehicleState(x_m=1), fault_latched=False)
        peer = ("127.0.0.1", 41000)
        delivery.set_mode("delay")
        delivery.receive(10.0, snapshot, peer)
        self.assertEqual(list(delivery.deliver(10.0 + TELEMETRY_DELAY_S - 0.01)), [])
        self.assertEqual(list(delivery.deliver(10.0 + TELEMETRY_DELAY_S)), [(10.0, snapshot, peer)])
        delivery.receive(12.0, snapshot, peer)
        delivery.set_mode("live")
        self.assertEqual(list(delivery.deliver(13.0)), [])

    def test_reconnect_starts_a_fresh_worker_without_applying_a_target(self):
        window = self.make_window(management_connect=lambda **_: nullcontext())
        window.credential_input.setText("test-only")
        with patch("rover.desktop.app.SecureControlWorker", spec=SecureControlWorker) as factory:
            old, new = Mock(spec=SecureControlWorker), Mock(spec=SecureControlWorker)
            for worker in (old, new):
                worker.status.return_value = WorkerStatus("ready")
                worker.wait_closed.return_value = True
            factory.side_effect = (old, new)
            window.connect_button.click()
            window.disconnect_button.click()
            old.close.assert_called_once_with()
            window.connect_button.click()
            self.assertIs(window.secure_worker, new)
            self.assertIsNone(window.current_target)
            self.assertEqual(self.sent, [])
            new.apply_target.assert_not_called()

    def test_control_loss_drops_every_third_datagram_and_consumes_its_sequence(self):
        window = self.make_window()
        client = Mock()
        key = b"k" * 32
        client.open_session.return_value = SessionOpened(1, "session", key.hex())
        client.issue_control_window.return_value = ControlWindowResult(2, True, "window", 0.25)
        client.enable_session.return_value = SessionEnableResult(3, True)
        session = SecureControlSession(client, window.control_sock, window.control_address,
                                       clock=lambda: 10, send_command=window.control_datagrams.send_windowed)
        target = ControlTarget(1, 0)
        window.begin_recording()
        window.set_control_loss(True)
        session.start(target)
        for _ in range(3):
            session.send(target)
        window.drain_control_events()
        commands = [decode_windowed_command(packet, key) for packet, _ in self.sent]
        self.assertEqual([item.seq for item in commands], [0, 1, 3])
        self.assertEqual(sum(row["event"] == "control_dropped" for row in window.recording.rows), 1)
