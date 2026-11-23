import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtNetwork import QHostAddress, QNetworkInterface
from PySide6.QtWidgets import QApplication, QMessageBox

from rover.vehicle.accounts import AccountStore
from rover.vehicle.admin_app import VehicleAdminConfig, VehicleAdminService, VehicleAdminStatus, VehicleAdminWindow
from tests.qt_fixture import dispose_widget


def _config() -> VehicleAdminConfig:
    return VehicleAdminConfig(Path("certs"), Path("accounts.sqlite3"), "127.0.0.1", 19300, 19301)


def _session(session_id: str, *, holds_authority: bool) -> dict[str, object]:
    return {
        "session_id": session_id,
        "client_id": "same-account",
        "role": "operator" if holds_authority else "observer",
        "peer_address": ("127.0.0.1", 53000),
        "telemetry_address": ("127.0.0.1", 53001) if holds_authority else None,
        "holds_authority": holds_authority,
        "last_activity_s": 98.5,
        "telemetry_result": "sent" if holds_authority else None,
        "events": (f"session {session_id}",),
    }


class TestVehicleAdminService(unittest.TestCase):
    def test_worker_reuses_expert_runtime_and_stops_with_event(self) -> None:
        service = VehicleAdminService()
        ready = Event()
        finished = Event()
        snapshots = (_session("one", holds_authority=True),)
        runtime = Mock()
        runtime.admin_snapshot.return_value = snapshots

        def vehicle(*, management, address, stop_event):
            self.assertEqual(address, ("127.0.0.1", 19301))
            with management as current:
                self.assertIs(current, runtime)
                ready.set()
                self.assertTrue(stop_event.wait(2))
            finished.set()

        with (
            patch("rover.vehicle.admin_app.AccountStore") as accounts,
            patch("rover.vehicle.admin_app.expert_management_listener", return_value=nullcontext(runtime)) as listener,
            patch("rover.vehicle.admin_app.vehicle_main", side_effect=vehicle),
        ):
            service.start(_config())
            try:
                self.assertTrue(ready.wait(2))
                self.assertEqual(service.status(), VehicleAdminStatus("running", snapshots))
            finally:
                service.stop()
            self.assertTrue(finished.wait(2))
            deadline = time.monotonic() + 2
            while service.is_active() and time.monotonic() < deadline:
                time.sleep(0.005)
        self.assertEqual(service.status(), VehicleAdminStatus("idle"))
        listener.assert_called_once_with(Path("certs"), {}, address=("127.0.0.1", 19300), accounts=accounts.return_value)

    def test_startup_failure_clears_sessions_and_reports_error(self) -> None:
        service = VehicleAdminService()
        with patch("rover.vehicle.admin_app.AccountStore", side_effect=OSError("database permission denied")):
            service.start(_config())
            deadline = time.monotonic() + 2
            while service.is_active() and time.monotonic() < deadline:
                time.sleep(0.005)
        self.assertEqual(service.status().phase, "error")
        self.assertIn("database permission denied", service.status().error)
        self.assertEqual(service.status().sessions, ())


class TestVehicleAdminWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.service = Mock()
        self.service.status.return_value = VehicleAdminStatus("idle")
        self.service.is_active.return_value = False
        self.window = VehicleAdminWindow(_config(), service=self.service)
        self.addCleanup(dispose_widget, self.window)

    def test_detect_ipv4_recommends_active_wifi_and_can_select_virtual_interface(self) -> None:
        flags = QNetworkInterface.InterfaceFlag
        types = QNetworkInterface.InterfaceType

        def adapter(name, address, interface_type, state):
            result = Mock()
            result.humanReadableName.return_value = name
            result.type.return_value = interface_type
            result.flags.return_value = state
            entry = Mock()
            entry.ip.return_value = QHostAddress(address)
            result.addressEntries.return_value = [entry]
            return result

        active = flags.IsUp | flags.IsRunning
        interfaces = [
            adapter("VMware VMnet8", "192.168.174.1", types.Ethernet, active),
            adapter("WLAN", "192.168.1.20", types.Wifi, active),
            adapter("Ethernet", "192.168.1.21", types.Ethernet, flags.IsUp),
            adapter("Loopback", "127.0.0.1", types.Loopback, active | flags.IsLoopBack),
        ]
        with (
            patch.object(QNetworkInterface, "allInterfaces", return_value=interfaces),
            patch("rover.vehicle.admin_app.QInputDialog.getItem") as choose,
        ):
            def select_virtual(*args):
                labels, suggested, editable = args[3:]
                self.assertEqual(len(labels), 2)
                self.assertIn("WLAN", labels[suggested])
                self.assertIn("VMware VMnet8", labels[1])
                self.assertFalse(editable)
                return labels[1], True

            choose.side_effect = select_virtual
            self.window.detect_ip_button.click()
        self.assertEqual(self.window.bind_host.text(), "192.168.174.1")

    def _finish_certificate_job(self) -> None:
        deadline = time.monotonic() + 2
        while self.window._certificate_job is not None and time.monotonic() < deadline:
            self.window.refresh()
            time.sleep(0.005)
        self.assertIsNone(self.window._certificate_job)

    def test_certificate_generation_sets_path_only_after_success_and_allows_public_export(self) -> None:
        folder = Path("managed/vehicle")
        output = Path("fixture.rover-connection.json")
        ready = Event()
        release = Event()

        def generate(*args):
            ready.set()
            if not release.wait(2):
                raise RuntimeError("fixture timed out")
            return folder

        self.window.bind_host.setText("192.168.1.20")
        with patch("rover.vehicle.admin_app.ensure_vehicle_certificate", side_effect=generate) as signing:
            self.window.certificate_button.click()
            try:
                self.assertTrue(ready.wait(2))
                self.assertFalse(self.window.start_button.isEnabled())
                self.assertFalse(self.window.certificate_button.isEnabled())
                self.assertEqual(self.window.cert_dir.text(), "certs")
            finally:
                release.set()
            self._finish_certificate_job()
        signing.assert_called_once_with(self.window._managed_cert_store, "192.168.1.20")
        self.assertEqual(Path(self.window.cert_dir.text()), folder)
        self.assertTrue(self.window.start_button.isEnabled())
        self.window.start_button.click()
        self.service.start.assert_called_once_with(VehicleAdminConfig(
            folder, Path("accounts.sqlite3"), "192.168.1.20", 19300, 19301,
        ))
        self.service.status.return_value = VehicleAdminStatus("running")
        self.window.refresh()
        self.assertTrue(self.window.export_bundle_button.isEnabled())
        fingerprint = "abcd" * 16
        with (
            patch("rover.vehicle.admin_app.QFileDialog.getSaveFileName", return_value=(str(output), "")),
            patch("rover.vehicle.admin_app.write_connection_bundle") as write,
            patch("rover.vehicle.admin_app.ca_certificate_fingerprint", return_value=fingerprint) as ca_fingerprint,
        ):
            self.window.export_bundle_button.click()
        write.assert_called_once_with(
            output, vehicle_host="192.168.1.20", management_port=19300, control_port=19301,
            ca_file=folder / "ca.pem",
        )
        ca_fingerprint.assert_called_once_with(folder / "ca.pem")
        self.assertIn(fingerprint, self.window.provisioning_label.text())
        self.assertTrue(self.window.provisioning_label.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        with (
            patch("rover.vehicle.admin_app.QFileDialog.getSaveFileName", return_value=(str(folder / "server.key"), "")),
            patch("rover.vehicle.admin_app.write_connection_bundle") as write,
            patch("rover.vehicle.admin_app.ca_certificate_fingerprint", return_value=fingerprint),
        ):
            self.window.export_bundle_button.click()
        write.assert_not_called()
        self.assertIn(".rover-connection.json", self.window.provisioning_label.text())
        self.service.status.return_value = VehicleAdminStatus("idle")
        self.window.refresh()
        self.window.bind_host.setText("192.168.1.22")
        self.assertFalse(self.window.export_bundle_button.isEnabled())
        self.window.start_button.click()
        self.service.start.assert_called_once()
        self.assertIn("请先生成或更新证书", self.window.service_label.text())

    def test_certificate_generation_failure_preserves_manual_certificate_directory(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        with patch("rover.vehicle.admin_app.ensure_vehicle_certificate", side_effect=ValueError("签发失败")):
            self.window.certificate_button.click()
            self._finish_certificate_job()
        self.assertEqual(self.window.cert_dir.text(), "certs")
        self.assertIn("签发失败", self.window.provisioning_label.text())
        self.assertFalse(self.window.export_bundle_button.isEnabled())

    def test_start_settings_and_address_validation(self) -> None:
        self.window.start_button.click()
        self.service.start.assert_called_once_with(_config())

        self.window.bind_host.setText("invalid-host")
        self.window.start_button.click()
        self.service.start.assert_called_once_with(_config())
        self.assertIn("IPv4", self.window.service_label.text())

    def _finish_firewall_startup(self) -> None:
        deadline = time.monotonic() + 2
        while getattr(self.window, "_firewall_job", None) is not None and time.monotonic() < deadline:
            self.window.refresh()
            time.sleep(0.005)
        self.assertIsNone(getattr(self.window, "_firewall_job", None))

    def test_start_reuses_lan_firewall_rules_without_asking_for_consent(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        with (
            patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
            patch("rover.vehicle.admin_app.inspect_lan_firewall", return_value=Mock(ready=True), create=True) as inspect,
            patch("rover.vehicle.admin_app.configure_lan_firewall", create=True) as configure,
            patch.object(QMessageBox, "question") as question,
        ):
            self.window.start_button.click()
            self._finish_firewall_startup()
        inspect.assert_called_once_with("192.168.1.20", 19300)
        configure.assert_not_called()
        question.assert_not_called()
        self.service.start.assert_called_once_with(
            VehicleAdminConfig(Path("certs"), Path("accounts.sqlite3"), "192.168.1.20", 19300, 19301)
        )

    def test_start_configures_firewall_only_after_dialog_confirmation(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        plan = Mock(profile="Public", bind_host="192.168.1.20")
        with (
            patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
            patch("rover.vehicle.admin_app.inspect_lan_firewall", return_value=Mock(ready=False, plan=plan), create=True),
            patch("rover.vehicle.admin_app.configure_lan_firewall", return_value=True, create=True) as configure,
            patch.object(QMessageBox, "question") as question,
        ):
            def confirm(*args):
                self.window.refresh()
                configure.assert_not_called()
                self.service.start.assert_not_called()
                self.assertFalse(self.window.start_button.isEnabled())
                self.assertFalse(self.window.bind_host.isEnabled())
                self.assertIn("192.168.1.20", args[2])
                return QMessageBox.StandardButton.Yes

            question.side_effect = confirm
            self.window.start_button.click()
            self._finish_firewall_startup()
        question.assert_called_once()
        configure.assert_called_once_with(plan)
        self.service.start.assert_called_once()

    def test_start_can_continue_without_firewall_changes_or_cancel(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        plan = Mock(profile="Private", bind_host="192.168.1.20")
        for reply in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Cancel):
            with (
                self.subTest(reply=reply),
                patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
                patch("rover.vehicle.admin_app.inspect_lan_firewall", return_value=Mock(ready=False, plan=plan), create=True),
                patch("rover.vehicle.admin_app.configure_lan_firewall", create=True) as configure,
                patch.object(QMessageBox, "question", return_value=reply),
            ):
                self.service.start.reset_mock()
                self.window.start_button.click()
                self._finish_firewall_startup()
                configure.assert_not_called()
                if reply == QMessageBox.StandardButton.No:
                    self.service.start.assert_called_once()
                else:
                    self.service.start.assert_not_called()

    def test_cancelled_uac_does_not_start_the_service_without_confirmation(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        plan = Mock(profile="Private", bind_host="192.168.1.20")
        with (
            patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
            patch("rover.vehicle.admin_app.inspect_lan_firewall", return_value=Mock(ready=False, plan=plan), create=True),
            patch("rover.vehicle.admin_app.configure_lan_firewall", return_value=False, create=True) as configure,
            patch.object(QMessageBox, "question", side_effect=(
                QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No,
            )) as question,
        ):
            self.window.start_button.click()
            self._finish_firewall_startup()
        configure.assert_called_once_with(plan)
        self.assertEqual(question.call_count, 2)
        self.service.start.assert_not_called()
        self.assertIn("授权已取消", self.window.service_label.text())

    def test_failed_firewall_inspection_can_continue_with_the_existing_rules(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        with (
            patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
            patch("rover.vehicle.admin_app.inspect_lan_firewall", side_effect=RuntimeError("策略限制"), create=True),
            patch("rover.vehicle.admin_app.configure_lan_firewall", create=True) as configure,
            patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as question,
        ):
            self.window.start_button.click()
            self._finish_firewall_startup()
        configure.assert_not_called()
        self.service.start.assert_called_once()
        self.assertIn("策略限制", question.call_args.args[2])
        self.assertIn("沿用现有防火墙规则", self.window.service_label.text())

    def test_close_during_firewall_inspection_never_launches_service_or_approval(self) -> None:
        self.window.bind_host.setText("192.168.1.20")
        ready = Event()
        release = Event()

        def wait_for_close(*args):
            ready.set()
            if not release.wait(2):
                raise RuntimeError("fixture timed out")
            return Mock(ready=False, plan=Mock(profile="Private", bind_host="192.168.1.20"))

        with (
            patch("rover.vehicle.admin_app.supports_lan_firewall", return_value=True, create=True),
            patch("rover.vehicle.admin_app.inspect_lan_firewall", side_effect=wait_for_close, create=True),
            patch("rover.vehicle.admin_app.configure_lan_firewall", create=True) as configure,
            patch.object(QMessageBox, "question") as question,
            patch("rover.vehicle.admin_app.QTimer.singleShot") as retry_close,
        ):
            self.window.start_button.click()
            try:
                self.assertTrue(ready.wait(2))
                event = QCloseEvent()
                self.window.closeEvent(event)
                self.assertFalse(event.isAccepted())
            finally:
                release.set()
            self._finish_firewall_startup()
        question.assert_not_called()
        configure.assert_not_called()
        self.service.start.assert_not_called()
        retry_close.assert_called_once_with(0, self.window.close)

    def test_same_account_sessions_have_distinct_rows_and_selected_events(self) -> None:
        first = _session("first", holds_authority=True)
        second = _session("second", holds_authority=False)
        self.service.status.return_value = VehicleAdminStatus("running", (first, second))
        with patch("rover.vehicle.admin_app.monotonic", return_value=100.0):
            self.window.refresh()
        table = self.window.session_table
        self.assertEqual(table.rowCount(), 2)
        self.assertEqual(table.item(0, 0).text(), table.item(1, 0).text())
        self.assertEqual(table.item(0, 3).text(), "127.0.0.1:53001")
        self.assertEqual(table.item(0, 5).text(), "1.5 秒前")
        table.selectRow(1)
        self.assertIn("session second", self.window.details.toPlainText())
        self.assertNotIn("session first", self.window.details.toPlainText())

        self.service.status.return_value = VehicleAdminStatus("running", (second,))
        self.window.refresh()
        self.assertEqual(table.rowCount(), 1)
        self.assertIn("session second", self.window.details.toPlainText())

    def test_stop_requests_worker_shutdown_and_disables_configuration_until_idle(self) -> None:
        self.service.status.return_value = VehicleAdminStatus("running")
        self.window.refresh()
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertFalse(self.window.bind_host.isEnabled())
        self.assertFalse(self.window.detect_ip_button.isEnabled())
        self.assertFalse(self.window.certificate_button.isEnabled())
        self.window.stop_button.click()
        self.service.stop.assert_called_once_with()
        self.service.status.return_value = VehicleAdminStatus("idle")
        self.window.refresh()
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertTrue(self.window.bind_host.isEnabled())

    def test_close_stops_active_worker_before_dismissing_window(self) -> None:
        self.service.is_active.return_value = True
        event = QCloseEvent()
        self.window.closeEvent(event)
        self.service.stop.assert_called_once_with()
        self.assertFalse(event.isAccepted())

        self.service.is_active.return_value = False
        with patch("rover.vehicle.admin_app.QTimer.singleShot") as retry_close:
            self.window.refresh()
        retry_close.assert_called_once_with(0, self.window.close)
        self.window.closeEvent(event)
        self.assertTrue(event.isAccepted())

    def test_admin_updates_registered_role_through_role_controls(self) -> None:
        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "accounts.sqlite3"
            store = AccountStore(path)
            self.assertIsNone(store.register("desk", "fixture password 2026"))
            self.window.account_db.setText(str(path))
            self.window.role_account.setText("desk")
            self.window.role_choice.setCurrentIndex(self.window.role_choice.findData("operator"))
            self.window.role_button.click()
            self.assertEqual(store.role("desk"), "operator")
            self.assertIn("角色已更新", self.window.role_message.text())


if __name__ == "__main__":
    unittest.main()
