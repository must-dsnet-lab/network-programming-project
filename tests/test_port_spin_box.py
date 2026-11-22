import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

from rover.desktop.connection_dialog import ConnectionDialog
from rover.desktop.connection_profile import DesktopConnectionSettings
from rover.vehicle.admin_app import VehicleAdminConfig, VehicleAdminWindow
from tests.qt_fixture import dispose_widget


class TestPortSpinBox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_ports_have_distinct_click_targets_and_keep_spin_keys(self):
        desktop = ConnectionDialog(DesktopConnectionSettings())
        admin = VehicleAdminWindow(VehicleAdminConfig(Path("certs"), Path("accounts.sqlite3")))
        for window in (desktop, admin):
            self.addCleanup(dispose_widget, window)
            window.show()
        self.app.processEvents()

        fields = (
            desktop.management_port_input,
            desktop.control_port_input,
            desktop.telemetry_port_input,
            admin.management_port,
            admin.control_port,
        )
        for field in fields:
            with self.subTest(field=field.accessibleName() or field.value()):
                option = QStyleOptionSpinBox()
                field.initStyleOption(option)
                up, down = (
                    field.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, field)
                    for control in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown)
                )
                self.assertGreaterEqual(up.width(), 24)
                self.assertGreaterEqual(down.width(), 24)
                self.assertLess(up.bottom(), down.top())
                self.assertFalse(field.lineEdit().geometry().contains(up.center()))
                self.assertFalse(field.lineEdit().geometry().contains(down.center()))

                port = field.value()
                QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=up.center())
                self.assertEqual(field.value(), port + 1)
                QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=down.center())
                self.assertEqual(field.value(), port)
                QTest.keyClick(field, Qt.Key.Key_Up)
                QTest.keyClick(field, Qt.Key.Key_Up)
                QTest.keyClick(field, Qt.Key.Key_Down)
                self.assertEqual(field.value(), port + 1)

                field.setValue(field.maximum())
                QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=up.center())
                self.assertEqual(field.value(), field.maximum())
