import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from rover.desktop.connection_dialog import ConnectionDialog
from rover.desktop.connection_profile import DesktopConnectionSettings
from tests.qt_fixture import dispose_widget


class TestDesktopConnectionDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ca_file = Path(folder.name) / "public-ca.pem"
        self.ca_file.write_text("fixture for validating the CA file selection", encoding="utf-8")
        self.dialog = ConnectionDialog(DesktopConnectionSettings(ca_file=self.ca_file))
        self.addCleanup(dispose_widget, self.dialog)

    def test_returns_settings_and_exact_masked_credential_once(self):
        self.assertEqual(self.dialog.credential_input.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse(self.dialog.remember_checkbox.isChecked())
        expert = self.dialog.mode_input.findData("expert")
        self.dialog.mode_input.setCurrentIndex(expert)
        self.dialog.vehicle_host_input.setText("car.example.test")
        self.dialog.management_port_input.setValue(18177)
        self.dialog.control_port_input.setValue(8900)
        self.dialog.telemetry_bind_host_input.setText("0.0.0.0")
        self.dialog.telemetry_port_input.setValue(18000)
        self.dialog.server_name_input.setText("rover.example.test")
        self.dialog.client_id_input.setText("desktop-b")
        self.dialog.credential_input.setText(" synthetic password ")
        self.dialog.remember_checkbox.setChecked(True)

        self.dialog.connect_button.click()

        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        selection = self.dialog.take_selection()
        self.assertIsNotNone(selection)
        self.assertEqual(selection.settings, DesktopConnectionSettings(
            mode="expert", vehicle_host="car.example.test", management_port=18177,
            control_port=8900, telemetry_bind_host="0.0.0.0", telemetry_port=18000,
            server_name="rover.example.test", ca_file=self.ca_file, client_id="desktop-b",
        ))
        self.assertTrue(selection.remember)
        self.assertEqual(selection.credential, " synthetic password ")
        self.assertNotIn("synthetic password", repr(selection))
        self.assertEqual(self.dialog.credential_input.text(), "")
        self.assertIsNone(self.dialog.take_selection())

    def test_reopens_saved_settings_without_a_secret(self):
        dialog = ConnectionDialog(DesktopConnectionSettings(
            mode="expert", vehicle_host="127.0.0.1", telemetry_port=10001,
            ca_file=self.ca_file, client_id="desktop-b",
        ), saved=True)
        self.addCleanup(dispose_widget, dialog)

        self.assertEqual(dialog.mode_input.currentData(), "expert")
        self.assertEqual(dialog.telemetry_port_input.value(), 10001)
        self.assertEqual(dialog.client_id_input.text(), "desktop-b")
        self.assertEqual(dialog.ca_file_input.text(), str(self.ca_file))
        self.assertTrue(dialog.remember_checkbox.isChecked())
        self.assertEqual(dialog.credential_input.text(), "")
        self.assertIsNone(dialog.take_selection())

    def test_missing_ca_file_stays_in_form(self):
        self.dialog.ca_file_input.setText(str(self.ca_file.with_name("missing.pem")))
        self.dialog.credential_input.setText("unused secret")
        self.dialog.connect_button.click()

        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("CA", self.dialog.error_label.text())
        self.assertNotIn("unused secret", self.dialog.error_label.text())
        self.assertIsNone(self.dialog.take_selection())

    def test_missing_credential_stays_in_form(self):
        self.dialog.connect_button.click()

        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("凭据", self.dialog.error_label.text())
        self.assertIsNone(self.dialog.take_selection())

    def test_invalid_host_stays_in_form(self):
        self.dialog.vehicle_host_input.setText("http://car.example.test")
        self.dialog.credential_input.setText("unused secret")
        self.dialog.connect_button.click()

        self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(self.dialog.error_label.text())
        self.assertIsNone(self.dialog.take_selection())

    def test_escape_wipes_the_credential(self):
        self.dialog.credential_input.setText("unused secret")
        QTest.keyClick(self.dialog.credential_input, Qt.Key.Key_Escape)

        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(self.dialog.credential_input.text(), "")
        self.assertIsNone(self.dialog.take_selection())

    def test_browse_updates_only_when_a_file_is_chosen(self):
        chosen = self.ca_file.with_name("other-ca.pem")
        with patch("rover.desktop.connection_dialog.QFileDialog.getOpenFileName", side_effect=[
            (str(chosen), ""), ("", ""),
        ]):
            self.dialog.browse_button.click()
            self.assertEqual(self.dialog.ca_file_input.text(), str(chosen))
            self.dialog.browse_button.click()
            self.assertEqual(self.dialog.ca_file_input.text(), str(chosen))
