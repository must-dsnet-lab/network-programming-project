# 保存最近收到的位置并绘图
from math import degrees

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from map_geometry import world_to_screen
from vehicle_model import VehicleState
from trajectory import TrajectoryHistory

class VehicleMap(QWidget):
    def __init__(self):
        super().__init__()
        self.state: VehicleState | None = None
        self.history = TrajectoryHistory(max_points=1000)
        self.pixels_per_meter = 50
        self.setMinimumSize(600, 450) #画布最小尺寸

    # 收到状态后保存，并请求重绘
    def set_state(self, state: VehicleState) -> None:
        self.state = state
        self.history.record(state)
        self.update() #update() 告诉 Qt 去调用被重写的 paintEvent()

    # 调用坐标转换函数
    def screen_point(self, x_m: float, y_m: float) -> QPointF:
        x_px, y_px = world_to_screen(
            x_m,
            y_m,
            origin_x_px=self.width() // 2,
            origin_y_px=self.height() // 2,
            pixels_per_meter=self.pixels_per_meter,
        )
        return QPointF(x_px, y_px)

    # Qt 需要刷新画面时调用，负责画网格和车辆
    def paintEvent(self, event):
        # 只要代码离开了 with 的缩进块，清理就必然会触发
        with QPainter(self) as painter: # 获取画笔对象
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)  # 开启抗锯齿
            painter.fillRect(self.rect(), QColor("#0f172a")) # 填充背景色

            width, height = self.width(), self.height()
            cx, cy = width // 2, height // 2
            step = self.pixels_per_meter

            # 一格对应一米
            painter.setPen(QPen(QColor("#243248"), 1))  # 设置网格线的颜色（暗蓝色）

            # 画竖线
            # cx % step 能保证网格竖线对称地穿过屏幕正中心
            for x in range(cx % step, width, step):
                painter.drawLine(x, 0, x, height)
            # 画横线
            # cy % step 能保证网格横线对称地穿过屏幕正中心
            for y in range(cy % step, height, step):
                painter.drawLine(0, y, width, y)

            painter.setPen(QPen(QColor("#64748b"), 1))  # 设置十字轴线的颜色（浅灰色）
            # painter.drawLine(x1, y1, x2, y2)：(x1, y1)是起点，(x2, y2)是终点
            painter.drawLine(cx, 0, cx, height)  # 画横穿中点的竖线（Y轴）
            painter.drawLine(0, cy, width, cy)  # 画横穿中点的横线（X轴）

            painter.setPen(QColor("#94a3b8"))  # 设置文字颜色
            painter.drawText(cx + 6, cy + 18, "0 m")
            painter.drawText(width - 28, cy - 8, "+X")
            painter.drawText(cx + 8, 18, "+Y")

            if self.state is None:
                painter.setPen(QColor("#e5e7eb"))
                painter.drawText(
                    self.rect(),
                    Qt.AlignmentFlag.AlignCenter,
                    "等待遥测",
                )
                return

            # 先把世界坐标转换为当前画布坐标，再用 drawPolyline() 连成折线。
            # 轨迹要在小车的平移、旋转之前画，否则整条路径也会跟着小车的坐标系移动。
            points = self.history.points()
            if len(points) >= 2:
                path = QPolygonF(
                    [self.screen_point(x_m, y_m) for x_m, y_m in points]
                )
                painter.setPen(QPen(QColor("#0e7490"), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPolyline(path)

            # 把画布整个移动到已转换的世界坐标
            painter.translate(self.screen_point(self.state.x_m, self.state.y_m))
            # 把画布旋转，让画布的正前方对着车头方向
            # -degrees(...) 加负号是因为物理世界逆时针为正，但Qt画布顺时针为正
            painter.rotate(-degrees(self.state.heading_rad))
            # 取消边框
            painter.setPen(Qt.PenStyle.NoPen)
            # 填充青色
            painter.setBrush(QColor("#22d3ee"))
            # 绘制小车的形状
            painter.drawPolygon( #用来绘制多边形的函数
                QPolygonF( #多边形数据容器
                    [
                        QPointF(16, 0),
                        QPointF(-10, -9),
                        QPointF(-10, 9),
                    ]
                )
            )
