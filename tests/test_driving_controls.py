import unittest

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rover.desktop.driving_controls import DrivingControls
from tests.qt_fixture import dispose_widget


class TestDrivingControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controls = DrivingControls()
        self.addCleanup(dispose_widget, self.controls)
        self.controls.resize(360, 156)
        self.controls.show()
        self.controls.setFocus()
        self.app.processEvents()
        self.changes = []
        self.controls.input_changed.connect(lambda: self.changes.append(self.controls.input_value()))

    def test_steering_drag_applies_dead_zone_and_springs_to_center(self):
        slider = self.controls.steering_slider
        center = QPoint(slider.width() // 2 - 2, slider.height() // 2)
        left = QPoint(1, center.y())
        right = QPoint(slider.width() - 2, center.y())
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=center)
        self.assertEqual(self.controls.input_value().steering, 0)
        self.assertFalse(self.changes)

        QTest.mouseMove(slider, left)
        self.assertEqual(self.controls.input_value().steering, 1)
        QTest.mouseMove(slider, right)
        self.assertEqual(self.controls.input_value().steering, -1)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=right)
        self.assertEqual(self.controls.input_value().steering, 0)
        self.assertEqual([intent.steering for intent in self.changes], [1, -1, 0])

    def test_keyboard_and_pointer_allow_independent_inputs(self):
        slider = self.controls.steering_slider
        left = QPoint(1, slider.height() // 2)
        QTest.keyPress(self.controls, Qt.Key.Key_W)
        QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=left)
        QTest.keyPress(self.controls, Qt.Key.Key_S)
        intent = self.controls.input_value()
        self.assertEqual((intent.throttle, intent.brake, intent.steering), (1, 1, 1))
        self.assertEqual(self.controls.throttle_slider.value(), 100)
        self.assertEqual(self.controls.brake_slider.value(), 100)
        QTest.keyRelease(self.controls, Qt.Key.Key_W)
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=left)
        intent = self.controls.input_value()
        self.assertEqual((intent.throttle, intent.brake, intent.steering), (0, 1, 0))
        QTest.keyRelease(self.controls, Qt.Key.Key_S)
        self.assertEqual(self.controls.input_value().brake, 0)
        self.assertEqual(self.controls.brake_slider.value(), 0)

    def test_release_and_reset_remove_proportional_pedals(self):
        for slider, pedal in (
            (self.controls.throttle_slider, "throttle"),
            (self.controls.brake_slider, "brake"),
        ):
            with self.subTest(pedal=pedal):
                center = QPoint(slider.width() // 2, slider.height() // 2)
                QTest.mousePress(slider, Qt.MouseButton.LeftButton, pos=center)
                self.assertAlmostEqual(getattr(self.controls.input_value(), pedal), 0.5, delta=0.04)
                QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, pos=center)
                self.assertEqual(getattr(self.controls.input_value(), pedal), 0)

        QTest.keyPress(self.controls, Qt.Key.Key_D)
        brake = self.controls.brake_slider
        QTest.mousePress(brake, Qt.MouseButton.LeftButton, pos=QPoint(brake.width() - 2, brake.height() // 2))
        count = len(self.changes)
        self.controls.reset_inputs()
        QTest.mouseMove(brake, QPoint(brake.width() // 2, brake.height() // 2))
        QTest.mouseRelease(brake, Qt.MouseButton.LeftButton)
        QTest.keyRelease(self.controls, Qt.Key.Key_D)
        self.assertEqual(len(self.changes), count)
        intent = self.controls.input_value()
        self.assertEqual((intent.throttle, intent.brake, intent.steering), (0, 0, 0))

    def test_keyboard_focus_loss_releases_held_inputs(self):
        QTest.keyPress(self.controls, Qt.Key.Key_A)
        QTest.keyPress(self.controls, Qt.Key.Key_W)
        QApplication.sendEvent(self.controls, QFocusEvent(QEvent.Type.FocusOut))
        intent = self.controls.input_value()
        self.assertEqual((intent.throttle, intent.brake, intent.steering), (0, 0, 0))
