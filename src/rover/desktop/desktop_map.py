from math import degrees, floor

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from rover.core.vehicle_model import VehicleState
from rover.desktop.appearance import desktop_font
from rover.desktop.map_geometry import world_to_screen
from rover.desktop.trajectory import TrajectoryHistory


class VehicleMap(QWidget):
    def __init__(self):
        super().__init__()
        self.state: VehicleState | None = None
        self.history = TrajectoryHistory(max_points=1000)
        self.pixels_per_meter = 50.0
        self.pan_offset = QPointF()
        self.drag_position = None
        self.telemetry_fresh = True
        self.setFont(desktop_font(12))
        self.setMinimumSize(420, 340)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_state(self, state: VehicleState) -> None:
        if self.state != state:
            self.state = state
            self.history.record(state)
            self.update()

    def viewport_center(self) -> QPointF:
        return QPointF(self.width() // 2, self.height() // 2)

    def screen_point(self, x_m: float, y_m: float) -> QPointF:
        origin = self.viewport_center() + self.pan_offset
        return QPointF(*world_to_screen(
            x_m, y_m, origin_x_px=origin.x(), origin_y_px=origin.y(),
            pixels_per_meter=self.pixels_per_meter,
        ))

    def zoom_at(self, factor: float, cursor: QPointF) -> None:
        scale = max(10.0, min(240.0, self.pixels_per_meter * factor))
        origin = self.screen_point(0, 0)
        self.pan_offset += (origin - cursor) * (scale / self.pixels_per_meter - 1)
        self.pixels_per_meter = scale
        self.update()

    def center_vehicle(self) -> None:
        point = self.screen_point(self.state.x_m, self.state.y_m) if self.state else self.screen_point(0, 0)
        self.pan_offset += self.viewport_center() - point
        self.update()

    def set_telemetry_fresh(self, fresh: bool) -> None:
        if fresh != self.telemetry_fresh:
            self.telemetry_fresh = fresh
            self.update()

    def clear_trajectory(self) -> None:
        self.history.clear()
        if self.state is not None:
            self.history.record(self.state)
        self.update()

    def wheelEvent(self, event):
        if event.angleDelta().y():
            self.zoom_at(1.2 ** (event.angleDelta().y() / 120), event.position())
            event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_position is not None:
            self.pan_offset += event.position() - self.drag_position
            self.drag_position = event.position()
            self.update()
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()

    def paintEvent(self, event):
        with QPainter(self) as painter:
            painter.fillRect(self.rect(), QColor("#fbfcfa"))
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            self.draw_grid(painter)
            if self.state is None:
                painter.setPen(QColor("#5c6b64"))
                painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "等待遥测")
                return

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            color = QColor("#267b68" if self.telemetry_fresh else "#98652f")
            points = self.history.points()
            if len(points) >= 2:
                path = QPolygonF([self.screen_point(x_m, y_m) for x_m, y_m in points])
                painter.setPen(QPen(color, 2.25))
                painter.drawPolyline(path)

            painter.translate(self.screen_point(self.state.x_m, self.state.y_m))
            # World angles are counterclockwise, while the Qt screen's Y axis points down.
            painter.rotate(-degrees(self.state.heading_rad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#38443d"))
            for x in (-11, 5):
                for y in (-10, 7):
                    painter.drawRoundedRect(QRectF(x, y, 6, 3), 1, 1)
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(-15, -8, 30, 16), 4, 4)
            painter.setBrush(QColor("#ffffff"))
            painter.drawPolygon(QPolygonF([QPointF(10, 0), QPointF(1, -4), QPointF(1, 4)]))

    def draw_grid(self, painter):
        width, height = self.width(), self.height()
        origin = self.screen_point(0, 0)
        meters = next(unit for unit in (0.25, 0.5, 1, 2, 5) if unit * self.pixels_per_meter >= 35)
        step = self.pixels_per_meter * meters
        minor = QPen(QColor("#e9ede8"), 1)
        major = QPen(QColor("#dbe4db"), 1)
        first_column = floor(-origin.x() / step)
        first_row = floor(-origin.y() / step)
        for column in range(first_column, first_column + 2 + int(width / step)):
            x = origin.x() + column * step
            painter.setPen(major if column % 5 == 0 else minor)
            painter.drawLine(QPointF(x, 0), QPointF(x, height))
        for row in range(first_row, first_row + 2 + int(height / step)):
            y = origin.y() + row * step
            painter.setPen(major if row % 5 == 0 else minor)
            painter.drawLine(QPointF(0, y), QPointF(width, y))

        painter.setPen(QPen(QColor("#a4b4a9"), 1))
        painter.drawLine(QPointF(origin.x(), 0), QPointF(origin.x(), height))
        painter.drawLine(QPointF(0, origin.y()), QPointF(width, origin.y()))
        painter.setPen(QColor("#5c6b64"))
        painter.drawText(origin + QPointF(10, 19), "0 m")
        painter.drawText(QPointF(width - 27, origin.y() - 7), "+X")
        painter.drawText(QPointF(origin.x() + 8, 18), "+Y")

        left, baseline = 16, height - 20
        painter.drawLine(QPointF(left, baseline), QPointF(left + step, baseline))
        for x in (left, left + step):
            painter.drawLine(QPointF(x, baseline - 4), QPointF(x, baseline + 4))
        painter.drawText(QPointF(left + step + 8, baseline + 4), f"{meters:g} m")
