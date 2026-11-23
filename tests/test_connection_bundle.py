import json
import unittest
from pathlib import Path
from ssl import PROTOCOL_TLS_CLIENT, SSLContext
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from rover.desktop.connection_dialog import ConnectionDialog
from rover.desktop.connection_profile import DesktopConnectionSettings
from rover.protocol.connection_bundle import read_connection_bundle, write_connection_bundle
from tests.qt_fixture import dispose_widget


PUBLIC_TEST_CA = """-----BEGIN CERTIFICATE-----
MIIBhDCCASugAwIBAgIUWO40ZA38f7CHw5R0M74cVYUydKQwCgYIKoZIzj0EAwIw
GDEWMBQGA1UEAwwNUm92ZXIgVGVzdCBDQTAeFw0yNjExMjMwOTU5MzNaFw0zNjEx
MjAwOTU5MzNaMBgxFjAUBgNVBAMMDVJvdmVyIFRlc3QgQ0EwWTATBgcqhkjOPQIB
BggqhkjOPQMBBwNCAARx2nlWF6wcGkJhNxqQbvkGd4GGVU9Iq4c210n+L8MtDYsH
3oO3gC/q0Gn40aL+VXkiAqpX2CvMuQXAi1K5yQsKo1MwUTAdBgNVHQ4EFgQUd50+
gqZ9Qie9HolTio6GlIRX6bQwHwYDVR0jBBgwFoAUd50+gqZ9Qie9HolTio6GlIRX
6bQwDwYDVR0TAQH/BAUwAwEB/zAKBggqhkjOPQQDAgNHADBEAiBZCE1zJ0JZvfIy
Kw1+YvNoMTN2VNzj7hnWre9oB838hwIgJOUBV+C8G/DX14EwwMcxSn2VZuUZiL7t
lG6DhwKZCIo=
-----END CERTIFICATE-----
"""


class TestConnectionBundle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.ca_file = self.folder / "ca.pem"
        self.ca_file.write_text(PUBLIC_TEST_CA, encoding="ascii")
        self.bundle_path = self.folder / "vehicle.rover-connection.json"

    def write_bundle(self, host="192.0.2.10", **options):
        values = {
            "vehicle_host": host,
            "management_port": 18177,
            "control_port": 7999,
            "ca_file": self.ca_file,
        } | options
        write_connection_bundle(self.bundle_path, **values)

    def test_bundle_round_trip_contains_only_portable_public_fields(self):
        self.write_bundle(server_name="rover.example.test")

        bundle = read_connection_bundle(self.bundle_path)
        self.assertEqual(bundle.vehicle_host, "192.0.2.10")
        self.assertEqual(bundle.management_port, 18177)
        self.assertEqual(bundle.control_port, 7999)
        self.assertEqual(bundle.server_name, "rover.example.test")
        context = SSLContext(PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cadata=bundle.ca_pem)
        document = json.loads(self.bundle_path.read_text(encoding="utf-8"))
        self.assertEqual(set(document), {
            "version", "vehicle_host", "management_port", "control_port", "server_name", "ca_pem",
        })
        self.assertNotIn(str(self.ca_file), str(document))

    def test_rejects_invalid_targets_ports_and_certificate_payloads(self):
        for host in ("0.0.0.0", "127.0.0.3", "239.1.1.1", "http://rover.example.test"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.write_bundle(host)
        with self.assertRaises(ValueError):
            self.write_bundle(control_port=True)
        self.ca_file.write_text(PUBLIC_TEST_CA + "-----BEGIN PRIVATE KEY-----", encoding="ascii")
        with self.assertRaises(ValueError):
            self.write_bundle()

    def test_malformed_or_oversized_bundle_is_rejected(self):
        self.write_bundle()
        document = json.loads(self.bundle_path.read_text(encoding="utf-8"))
        self.bundle_path.write_text(json.dumps({**document, "credential": "forbidden"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_connection_bundle(self.bundle_path)
        self.bundle_path.write_text(" " * 65537, encoding="utf-8")
        with self.assertRaises(ValueError):
            read_connection_bundle(self.bundle_path)

    def make_dialog(self):
        dialog = ConnectionDialog(
            DesktopConnectionSettings(ca_file=self.folder / "missing.pem", client_id="desktop-b"),
            ca_storage=self.folder / "trusted",
        )
        self.addCleanup(dispose_widget, dialog)
        return dialog

    def test_user_confirms_first_ca_and_same_ca_updates_address_without_another_prompt(self):
        self.write_bundle()
        dialog = self.make_dialog()
        dialog.credential_input.setText("transient-secret")
        with (
            patch("rover.desktop.connection_dialog.QFileDialog.getOpenFileName", return_value=(str(self.bundle_path), "")),
            patch("rover.desktop.connection_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as confirm,
        ):
            dialog.import_bundle_button.click()
            confirm.assert_called_once()
            self.assertIn("SHA-256", confirm.call_args.args[2])
            trusted_ca = Path(dialog.ca_file_input.text())
            self.assertEqual(trusted_ca.parent, self.folder / "trusted")
            self.assertTrue(trusted_ca.is_file())
            self.assertEqual(dialog.mode_input.currentData(), "product")
            self.assertEqual(dialog.vehicle_host_input.text(), "192.0.2.10")
            self.assertEqual(dialog.management_port_input.value(), 18177)
            self.assertEqual(dialog.control_port_input.value(), 7999)
            self.assertEqual(dialog.client_id_input.text(), "desktop-b")
            self.assertEqual(dialog.credential_input.text(), "")
            self.assertIsNone(dialog.take_selection())

            self.write_bundle("192.0.2.12")
            dialog.import_bundle_button.click()
            confirm.assert_called_once()
            self.assertEqual(dialog.vehicle_host_input.text(), "192.0.2.12")
            self.assertEqual(dialog.ca_file_input.text(), str(trusted_ca))

    def test_rejecting_first_ca_preserves_settings_and_does_not_persist_ca(self):
        self.write_bundle()
        dialog = self.make_dialog()
        with (
            patch("rover.desktop.connection_dialog.QFileDialog.getOpenFileName", return_value=(str(self.bundle_path), "")),
            patch("rover.desktop.connection_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
        ):
            dialog.import_bundle_button.click()
        self.assertEqual(dialog.vehicle_host_input.text(), "127.0.0.1")
        self.assertFalse((self.folder / "trusted").exists())

    def test_explicitly_confirmed_reimport_repairs_an_unreadable_cached_ca(self):
        self.write_bundle()
        dialog = self.make_dialog()
        with (
            patch("rover.desktop.connection_dialog.QFileDialog.getOpenFileName", return_value=(str(self.bundle_path), "")),
            patch("rover.desktop.connection_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes) as confirm,
        ):
            dialog.import_bundle_button.click()
            trusted_ca = Path(dialog.ca_file_input.text())
            trusted_ca.write_text("broken local copy", encoding="ascii")
            self.write_bundle("192.0.2.12")

            dialog.import_bundle_button.click()

        self.assertEqual(confirm.call_count, 2)
        self.assertEqual(dialog.vehicle_host_input.text(), "192.0.2.12")
        self.assertEqual(trusted_ca.read_text(encoding="ascii"), PUBLIC_TEST_CA)
