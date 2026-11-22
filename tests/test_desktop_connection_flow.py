import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import ANY, Mock, patch

from PySide6.QtWidgets import QApplication

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.app import DesktopConsole, main
from rover.desktop import connection_flow as flow
from rover.desktop.connection_dialog import ConnectionSelection
from rover.desktop.connection_profile import DesktopConnectionSettings
from rover.desktop.control_worker import WorkerStatus
from rover.protocol.telemetry_codec import TelemetrySnapshot
from tests.qt_fixture import dispose_widget
from tests.test_desktop_control import FakeSocket


class TestDesktopConnectionFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "desktop-connection.json"
        self.application = Mock()
        self.factory = Mock()
        self.controller = flow.DesktopConnectionFlow(self.application, self.factory, profile_path=self.path)
        self.settings = DesktopConnectionSettings(
            mode="expert", vehicle_host="192.0.2.10", telemetry_port=10001,
        )
        self.choice = ConnectionSelection(settings=self.settings, credential="transient-test", remember=True)

    def test_switch_waits_for_terminal_close_and_creates_fresh_expert_worker(self):
        old = Mock(current_target=None)
        self.controller.attach(old)
        with (
            patch.object(self.controller, "_ask_for_selection", return_value=self.choice),
            patch.object(flow, "ExpertControlWorker") as create_worker,
            patch.object(flow.QTimer, "singleShot") as defer,
        ):
            self.controller.configure()
            old.close.assert_called_once_with()
            create_worker.assert_not_called()
            self.factory.assert_not_called()

            self.controller._window_closed()
            old.deleteLater.assert_called_once_with()
            create_worker.assert_not_called()
            defer.call_args.args[1]()
            create_worker.assert_called_once()

        self.factory.assert_called_once_with(
            secure_worker=create_worker.return_value,
            telemetry_address=("127.0.0.1", 10001),
            operator_id="desktop-a", expert_mode=True, require_initial_telemetry=True,
            telemetry_peer_host=ANY,
        )
        create_worker.return_value.apply_target.assert_not_called()
        self.application.quit.assert_not_called()

    def test_cancel_leaves_current_window_and_does_not_start_a_connection(self):
        old = Mock(current_target=None)
        self.controller.attach(old)
        with patch.object(self.controller, "_ask_for_selection", return_value=None):
            self.controller.configure()
        old.close.assert_not_called()
        self.factory.assert_not_called()

    def test_settings_while_sending_nonzero_prompts_for_stop(self):
        self.controller.attach(Mock(current_target=ControlTarget(1.0, 0.0)))
        with (
            patch.object(flow.QMessageBox, "information") as message,
            patch.object(self.controller, "_ask_for_selection") as dialog,
        ):
            self.controller.configure()
        message.assert_called_once()
        dialog.assert_not_called()

    def test_save_receives_only_settings_and_user_can_reopen_without_retaining_password(self):
        with (
            patch.object(flow, "ConnectionDialog") as dialog_type,
            patch.object(flow, "save_desktop_profile") as save,
        ):
            dialog = dialog_type.return_value
            dialog.exec.return_value = flow.QDialog.DialogCode.Accepted
            dialog.take_selection.return_value = self.choice
            self.assertEqual(self.controller._ask_for_selection(), self.choice)

        save.assert_called_once_with(self.path, self.choice.settings)
        dialog.deleteLater.assert_called_once_with()

    def test_graphical_cli_opens_connection_form_without_terminal_prompt(self):
        with (
            patch("rover.desktop.app.QApplication") as application,
            patch("rover.desktop.app.DesktopConnectionFlow") as create_flow,
            patch("rover.desktop.app.getpass") as prompt,
        ):
            create_flow.return_value.start_configured.return_value = False
            self.assertEqual(main(["--connect"]), 0)
            create_flow.return_value.start_configured.assert_called_once_with()
            application.return_value.exec.assert_not_called()
            prompt.assert_not_called()

    def test_saved_relative_ca_is_recognized_when_reopening(self):
        self.controller.attach(Mock(current_target=None), self.settings)
        profile = replace(self.settings, ca_file=self.settings.ca_file.resolve())
        with (
            patch.object(flow, "ConnectionDialog") as dialog,
            patch.object(flow, "load_desktop_profile", return_value=profile),
        ):
            self.controller._ask_for_selection()
        self.assertIs(dialog.call_args.kwargs["saved"], True)

    def test_graphical_console_waits_for_telemetry_and_disconnect_keeps_it_available(self):
        worker = Mock()
        worker.status.return_value = WorkerStatus("ready")
        worker.wait_closed.return_value = True
        telemetry = FakeSocket([])
        with patch("rover.desktop.app.socket.socket", return_value=telemetry):
            window = DesktopConsole(
                secure_worker=worker, require_initial_telemetry=True,
                telemetry_peer_host=lambda: "127.0.0.1",
            )
        self.addCleanup(dispose_widget, window)
        self.assertFalse(window.start_button.isEnabled())

        old_packet = TelemetrySnapshot(VehicleState(), False), ("192.0.2.44", 9999)
        with patch("rover.desktop.app.try_receive_telemetry", side_effect=[old_packet, None]):
            window.poll_telemetry()
        self.assertFalse(window.start_button.isEnabled())
        self.assertIsNone(window.last_received_s)

        packet = TelemetrySnapshot(VehicleState(), False), ("127.0.0.1", 9999)
        with patch("rover.desktop.app.try_receive_telemetry", side_effect=[packet, None]):
            window.poll_telemetry()
        self.assertTrue(window.start_button.isEnabled())

        def close_worker():
            worker.status.return_value = WorkerStatus("closing")

        worker.close.side_effect = close_worker
        window.disconnect_button.click()
        worker.close.assert_called_once_with()
        self.assertFalse(window.start_button.isEnabled())
        self.assertFalse(telemetry.closed)
        self.assertIsNone(window.current_target)
