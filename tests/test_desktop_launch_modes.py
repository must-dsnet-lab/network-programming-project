import socket
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

from rover.core.vehicle_model import VehicleState
from rover.desktop import app, connection_flow
from rover.protocol.telemetry_codec import TelemetrySnapshot
from tests.qt_fixture import dispose_widget
from tests.test_desktop_control import FakeSocket


class TestDesktopLaunchModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @unittest.skipUnless(hasattr(socket, "SO_EXCLUSIVEADDRUSE"), "Windows socket binding behavior")
    def test_observer_cannot_reuse_the_wildcard_receiver_port(self):
        owner = app.DesktopConsole(read_only=True, telemetry_address=("0.0.0.0", 0))
        self.addCleanup(dispose_widget, owner)
        address = ("127.0.0.1", owner.listen_address[1])
        with self.assertRaises(OSError):
            observer = app.DesktopConsole(read_only=True, telemetry_address=address)
            self.addCleanup(dispose_widget, observer)

    def test_observer_only_opens_receive_socket_and_updates_map(self):
        receiver = FakeSocket([])
        with patch.object(app.socket, "socket", return_value=receiver) as create_socket:
            window = app.DesktopConsole(read_only=True, telemetry_address=("127.0.0.1", 10001))
        self.addCleanup(dispose_widget, window)
        create_socket.assert_called_once()
        self.assertIsNone(window.control_sock)
        self.assertIsNone(window.secure_worker)
        self.assertEqual(window.connection_value.text(), "只读观察")
        window.start_control()
        window.send_control()
        self.assertFalse(window.control_timer.isActive())
        self.assertEqual(receiver.send_attempts, 0)
        state = VehicleState(x_m=1.25, speed_mps=0.7)
        packet = TelemetrySnapshot(state, False), ("127.0.0.1", 46240)
        with patch.object(app, "try_receive_telemetry", side_effect=[packet, None]):
            window.poll_telemetry()
        self.assertEqual(window.map.state, state)
        self.assertEqual(window.speed_value.text(), "0.70")

    def test_local_demo_uses_process_ports_and_stops_the_child(self):
        receiver, sender = FakeSocket([]), FakeSocket([])
        receiver.getsockname = Mock(return_value=("127.0.0.1", 42000))
        with patch.object(app.socket, "socket", side_effect=[receiver, sender]):
            window = app.DesktopConsole(local_demo=True, telemetry_address=("127.0.0.1", 0))
        self.addCleanup(dispose_widget, window)
        child = Mock(log_path=Path("example.log"), returncode=None)
        ports = Mock(control_port=43100, telemetry_source_port=43200)
        child.poll_ready.side_effect = [None, ports]
        with patch.object(app.LocalVehicleProcess, "start", return_value=child) as start:
            self.assertTrue(window.start_local_vehicle(Path("logs")))
        start.assert_called_once_with(42000, Path("logs"))
        self.assertFalse(window.start_button.isEnabled())
        window.start_control()
        self.assertEqual(sender.sent, [])
        window._poll_local_vehicle()
        self.assertFalse(window.start_button.isEnabled())
        window._poll_local_vehicle()
        self.assertEqual(window.control_address, ("127.0.0.1", 43100))
        packet = TelemetrySnapshot(VehicleState(), False)
        with patch.object(app, "try_receive_telemetry", side_effect=[
            (packet, ("127.0.0.1", 43201)), None
        ]):
            window.poll_telemetry()
        self.assertIsNone(window.last_received_s)
        self.assertFalse(window.start_button.isEnabled())
        with patch.object(app, "try_receive_telemetry", side_effect=[
            (packet, ("127.0.0.1", 43200)), None
        ]):
            window.poll_telemetry()
        self.assertTrue(window.start_button.isEnabled())
        window.start_control()
        self.assertEqual(sender.sent[0][1], ("127.0.0.1", 43100))
        with (
            patch.object(app, "monotonic", return_value=window.last_received_s + 2),
            patch.object(app, "try_receive_telemetry", return_value=None),
        ):
            window.poll_telemetry()
        self.assertFalse(window.control_timer.isActive())
        self.assertIn("遥测过期", window.control_status.text())
        with patch.object(app, "try_receive_telemetry", side_effect=[
            (packet, ("127.0.0.1", 43200)), None
        ]):
            window.poll_telemetry()
        self.assertTrue(window.start_button.isEnabled())
        self.assertFalse(window.control_timer.isActive())
        window.start_control()
        window.stop_control()
        self.assertIsNone(window.current_target)
        with patch.object(app, "try_receive_telemetry", side_effect=[
            (TelemetrySnapshot(VehicleState(), True), ("127.0.0.1", 43200)), None
        ]):
            window.poll_telemetry()
        self.assertFalse(window.start_button.isEnabled())
        self.assertIn("已锁存", window.control_status.text())
        window.start_control()
        self.assertEqual(len(sender.sent), 2)
        window.close()
        child.stop.assert_called_once_with()
        self.assertFalse(window.control_timer.isActive())

    def test_observe_cli_routes_receive_address_without_prompt(self):
        with (
            patch.object(app, "QApplication") as create_application,
            patch.object(app, "DesktopConnectionFlow") as create_flow,
            patch.object(app, "getpass") as credential,
        ):
            create_application.return_value.exec.return_value = 11
            self.assertEqual(app.main([
                "--observe", "--telemetry-bind-host", "0.0.0.0", "--telemetry-port", "10001",
            ]), 11)
        create_flow.return_value.start_observer.assert_called_once_with(("0.0.0.0", 10001))
        credential.assert_not_called()

    def test_launcher_accepts_a_local_role_without_opening_management(self):
        application, factory = Mock(), Mock()
        flow = connection_flow.DesktopConnectionFlow(application, factory)
        with patch.object(connection_flow, "choose_launch_role", return_value="local"):
            self.assertTrue(flow.start_launcher())
        factory.assert_called_once_with(local_demo=True, telemetry_address=("127.0.0.1", 0))
        factory.return_value.start_local_vehicle.assert_called_once()
        factory.return_value.show.assert_called_once_with()

    def test_launcher_observer_returns_with_only_selected_receive_address(self):
        application, factory = Mock(), Mock()
        flow = connection_flow.DesktopConnectionFlow(application, factory)
        with (
            patch.object(connection_flow, "choose_launch_role", return_value="observe"),
            patch.object(connection_flow, "choose_telemetry_address", return_value=("0.0.0.0", 10001)),
        ):
            self.assertTrue(flow.start_launcher())
        factory.assert_called_once_with(read_only=True, telemetry_address=("0.0.0.0", 10001))
        factory.return_value.show.assert_called_once_with()
