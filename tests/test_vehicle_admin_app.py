import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

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

    def test_start_settings_and_address_validation(self) -> None:
        self.window.start_button.click()
        self.service.start.assert_called_once_with(_config())

        self.window.bind_host.setText("invalid-host")
        self.window.start_button.click()
        self.service.start.assert_called_once_with(_config())
        self.assertIn("IPv4", self.window.service_label.text())

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
