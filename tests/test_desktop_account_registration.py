import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, current_thread
from time import monotonic
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QGroupBox

from rover.desktop.connection_dialog import ConnectionDialog
from rover.desktop.connection_profile import DesktopConnectionSettings
from tests.qt_fixture import dispose_widget


_REGISTER = "rover.desktop.secure_connection.register_configured_account"


class TestDesktopAccountRegistration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ca_file = Path(folder.name) / "vehicle-ca.pem"
        self.ca_file.write_text("public CA test fixture", encoding="utf-8")
        settings = DesktopConnectionSettings(ca_file=self.ca_file)
        self.dialog = ConnectionDialog(settings)
        self.addCleanup(dispose_widget, self.dialog)

    def select_product(self):
        self.dialog.mode_input.setCurrentIndex(self.dialog.mode_input.findData("product"))

    def wait_for(self, predicate):
        deadline = monotonic() + 3.0
        while not predicate():
            if monotonic() >= deadline:
                self.fail("registration feedback did not update")
            QTest.qWait(20)

    def test_registration_and_automatic_telemetry_are_available_only_in_product_mode(self):
        self.assertTrue(self.dialog.register_button.isHidden())
        self.dialog.credential_input.setText("unused secret")
        with patch(_REGISTER, create=True) as register:
            self.dialog.register_button.click()
        register.assert_not_called()
        telemetry = self.dialog.findChild(QGroupBox, "telemetrySettings")
        self.dialog.mode_input.setCurrentIndex(self.dialog.mode_input.findData("expert"))
        self.assertTrue(self.dialog.register_button.isHidden())
        self.assertFalse(telemetry.isHidden())
        self.select_product()
        self.assertFalse(self.dialog.register_button.isHidden())
        self.assertTrue(telemetry.isHidden())

    def test_registration_uses_validated_fields_and_requires_a_separate_login(self):
        self.select_product()
        self.dialog.vehicle_host_input.setText("car.example.test")
        self.dialog.management_port_input.setValue(19178)
        self.dialog.client_id_input.setText("new-operator")
        self.dialog.credential_input.setText(" synthetic password ")
        self.dialog.remember_checkbox.setChecked(True)
        caller = current_thread()
        workers = []

        def register(settings, credential):
            workers.append(current_thread())
            return None

        with patch(_REGISTER, side_effect=register, create=True) as command:
            self.dialog.register_button.click()
            self.assertEqual(self.dialog.credential_input.text(), "")
            self.wait_for(lambda: "注册成功" in self.dialog.status_label.text())

        self.assertTrue(workers and workers[0] is not caller)
        settings, credential = command.call_args.args
        self.assertEqual(settings.management_address, ("car.example.test", 19178))
        self.assertEqual(settings.mode, "product")
        self.assertEqual(settings.client_id, "new-operator")
        self.assertEqual(settings.ca_file, self.ca_file)
        self.assertEqual(credential, " synthetic password ")
        self.assertIn("只读", self.dialog.status_label.text())
        self.assertNotIn("synthetic password", self.dialog.status_label.text())
        self.assertIsNone(self.dialog.take_selection())
        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.dialog.connect_button.isEnabled())

        self.dialog.credential_input.setText(" synthetic password ")
        self.dialog.connect_button.click()
        selection = self.dialog.take_selection()
        self.assertEqual(selection.credential, " synthetic password ")
        self.assertTrue(selection.remember)

    def test_registration_reuses_ca_validation(self):
        self.select_product()
        self.dialog.credential_input.setText("untouched")
        self.dialog.ca_file_input.setText(str(self.ca_file.with_name("missing.pem")))
        with patch(_REGISTER, create=True) as register:
            self.dialog.register_button.click()
        register.assert_not_called()
        self.assertIn("CA", self.dialog.error_label.text())

    def test_registration_errors_explain_the_failure_without_a_secret(self):
        self.select_product()
        for outcome, hint in (
            ("account_exists", "账号已存在"),
            (OSError("sensitive exception"), "无法连接车端"),
            (RuntimeError("sensitive exception"), "无法完成账号注册"),
        ):
            with self.subTest(outcome=outcome):
                self.dialog.credential_input.setText("synthetic password")
                if isinstance(outcome, Exception):
                    override = patch(_REGISTER, side_effect=outcome, create=True)
                else:
                    override = patch(_REGISTER, return_value=outcome, create=True)
                with override:
                    self.dialog.register_button.click()
                    self.wait_for(lambda: hint in self.dialog.error_label.text())
                self.assertNotIn("sensitive exception", self.dialog.error_label.text())
                self.assertNotIn("synthetic password", self.dialog.error_label.text())
                self.assertEqual(self.dialog.credential_input.text(), "")

    def test_cancel_remains_available_while_background_request_finishes(self):
        self.select_product()
        self.dialog.credential_input.setText("synthetic password")
        self.dialog.show()
        started, release, finished = Event(), Event(), Event()
        self.addCleanup(release.set)

        def register(settings, credential):
            started.set()
            release.wait(timeout=3.0)
            finished.set()
            return None

        with patch(_REGISTER, side_effect=register, create=True):
            self.dialog.register_button.click()
            self.assertTrue(started.wait(timeout=2.0))
            self.assertFalse(self.dialog.connect_button.isEnabled())
            self.assertTrue(self.dialog.cancel_button.isEnabled())
            self.assertEqual(self.dialog.cancel_button.text(), "关闭")
            self.assertIn("可能完成", self.dialog.cancel_button.toolTip())
            self.dialog.cancel_button.click()
            self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)
            self.assertFalse(self.dialog.isVisible())
            self.assertEqual(self.dialog.credential_input.text(), "")
            release.set()
            self.assertTrue(finished.wait(timeout=2.0))
            QTest.qWait(70)
        self.assertIsNone(self.dialog.take_selection())
        self.assertNotIn("注册成功", self.dialog.status_label.text())
