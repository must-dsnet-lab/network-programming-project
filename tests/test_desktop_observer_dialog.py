import unittest

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from rover.desktop.observer_dialog import TelemetryObserverDialog, choose_telemetry_address
from tests.qt_fixture import dispose_widget


class TestDesktopObserverDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dialog = TelemetryObserverDialog(("127.0.0.1", 10001))
        self.addCleanup(dispose_widget, self.dialog)

    def test_prefills_the_callers_address_and_accepts_typed_wildcard(self):
        self.assertEqual(self.dialog.host_input.text(), "127.0.0.1")
        self.assertEqual(self.dialog.port_input.text(), "10001")
        self.dialog.host_input.setText(" 0.0.0.0 ")
        self.dialog.port_input.setText("65535")
        self.dialog.listen_button.click()

        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.dialog.address, ("0.0.0.0", 65535))

    def test_rejects_network_prefixes_and_hostnames(self):
        for invalid_host in ("127.0.0.1/8", "localhost", "::1", ""):
            with self.subTest(host=invalid_host):
                self.dialog.host_input.setText(invalid_host)
                self.dialog.listen_button.click()
                self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
                self.assertIsNone(self.dialog.address)
                self.assertIn("IPv4", self.dialog.error_label.text())

    def test_rejects_invalid_ports_before_accepting_a_correction(self):
        for invalid_port in ("0", "65536", "-1", "abc", ""):
            with self.subTest(port=invalid_port):
                self.dialog.port_input.setText(invalid_port)
                self.dialog.listen_button.click()
                self.assertNotEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
                self.assertIsNone(self.dialog.address)
                self.assertIn("端口", self.dialog.error_label.text())

        self.dialog.port_input.setText("1")
        self.dialog.listen_button.click()
        self.assertEqual(self.dialog.address, ("127.0.0.1", 1))

    def test_modal_returns_selected_address_or_none_on_cancel(self):
        def accept():
            dialog = self.app.activeModalWidget()
            dialog.port_input.setText("15555")
            dialog.listen_button.click()

        QTimer.singleShot(0, accept)
        self.assertEqual(choose_telemetry_address(None, ("0.0.0.0", 10001)), ("0.0.0.0", 15555))

        QTimer.singleShot(0, lambda: self.app.activeModalWidget().reject())
        self.assertIsNone(choose_telemetry_address(None, ("127.0.0.1", 10001)))
