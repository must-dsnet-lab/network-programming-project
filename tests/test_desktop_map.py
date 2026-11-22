import unittest

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from rover.core.vehicle_model import VehicleState
from rover.desktop.desktop_map import VehicleMap
from tests.qt_fixture import dispose_widget


class TestDesktopMap(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.map = VehicleMap()
        self.map.resize(640, 480)
        self.map.show()
        self.addCleanup(dispose_widget, self.map)

    def test_zoom_anchors_the_world_point_under_the_cursor(self):
        position = self.map.screen_point(2, 3)
        self.map.zoom_at(1.25, position)
        self.assertEqual(self.map.screen_point(2, 3), position)
        self.assertAlmostEqual(self.map.pixels_per_meter, 62.5)

    def test_drag_pans_and_center_vehicle_restores_visibility(self):
        self.map.set_state(VehicleState(x_m=2, y_m=3))
        before = self.map.screen_point(2, 3)
        QTest.mousePress(self.map, Qt.MouseButton.LeftButton, pos=QPoint(60, 70))
        QTest.mouseMove(self.map, QPoint(110, 100))
        QTest.mouseRelease(self.map, Qt.MouseButton.LeftButton, pos=QPoint(110, 100))
        self.assertEqual(self.map.screen_point(2, 3), before + QPointF(50, 30))
        self.map.center_vehicle()
        self.assertEqual(self.map.screen_point(2, 3), self.map.viewport_center())

    def test_clear_trajectory_retains_the_vehicle_and_current_position(self):
        self.map.set_state(VehicleState(x_m=1, y_m=2))
        state = VehicleState(x_m=2, y_m=3)
        self.map.set_state(state)
        self.map.clear_trajectory()
        self.assertEqual(self.map.state, state)
        self.assertEqual(self.map.history.points(), ((2, 3),))
