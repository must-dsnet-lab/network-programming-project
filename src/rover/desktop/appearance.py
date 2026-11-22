from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPolygon
from PySide6.QtWidgets import QAbstractSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox


def desktop_font(pixels: int, *, bold=False, numeric=False) -> QFont:
    family = "Cascadia Mono" if numeric else "Microsoft YaHei UI"
    font = QFont(family) if QFontDatabase.hasFamily(family) else QFont()
    if numeric:
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


STATUS_COLORS = {"live": "#176756", "alert": "#8a481a", "quiet": "#5c6b64"}


_PORT_STYLE = """
    QSpinBox { background: #ffffff; color: #24312c; border: 1px solid #cbd6cf;
               border-radius: 4px; min-height: 30px; padding: 0 29px 0 6px; }
    QSpinBox:focus { border-color: #287d67; }
    QSpinBox:disabled { background: #f2f4f1; color: #5c6b64; }
    QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: padding; width: 25px; }
    QSpinBox::up-button { subcontrol-position: top right; }
    QSpinBox::down-button { subcontrol-position: bottom right; }
"""


class PortSpinBox(QSpinBox):
    """Keep native spin controls and draw legible arrows at their styled hit targets."""

    def __init__(self, value: int) -> None:
        super().__init__()
        self.setStyleSheet(_PORT_STYLE)
        self.setRange(1, 65535)
        self.setValue(value)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        for control, enabled_flag, direction in (
            (QStyle.SubControl.SC_SpinBoxUp, QAbstractSpinBox.StepEnabledFlag.StepUpEnabled, -1),
            (QStyle.SubControl.SC_SpinBoxDown, QAbstractSpinBox.StepEnabledFlag.StepDownEnabled, 1),
        ):
            rect = self.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, control, self)
            rect = rect.intersected(self.rect().adjusted(1, 1, -1, -1))
            enabled = self.isEnabled() and bool(option.stepEnabled & enabled_flag)
            if enabled and option.activeSubControls & control:
                background = "#d3e6dc" if option.state & QStyle.StateFlag.State_Sunken else "#e3efe9"
            else:
                background = "#edf4f0" if enabled else "#f2f4f1"
            painter.fillRect(rect, QColor(background))
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            painter.setPen(QColor("#b8c8c0"))
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
            if direction == 1:
                painter.drawLine(rect.topLeft(), rect.topRight())

            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#225446" if enabled else "#7d9086"))
            center = rect.center()
            base = center - QPoint(0, direction * 3)
            painter.drawPolygon(QPolygon([
                center + QPoint(0, direction * 3),
                base + QPoint(5, 0),
                base - QPoint(5, 0),
            ]))
        painter.end()


CONSOLE_STYLE = """
    QWidget#console { background: #f1f3f0; color: #24312c; }
    QWidget#sidebar { background: #fbfcfa; border: 1px solid #d7dfd9; }
    QFrame#mapFrame { background: #fbfcfa; border: 1px solid #cdd8d0; }
    QFrame#divider { background: #e1e6e0; min-height: 1px; max-height: 1px; }
    QLabel { color: #24312c; }
    QLabel#controlStatus {
        color: #34443b; background: #eff4ef; padding: 8px;
        border-left: 2px solid #adc1b3; min-height: 24px;
    }
    QLabel[role="muted"] { color: #5c6b64; }
    QPushButton {
        background: #ffffff; color: #26352e; border: 1px solid #cbd6cf;
        border-radius: 4px; padding: 5px 9px; min-height: 19px;
        selection-color: #ffffff; selection-background-color: #286958;
    }
    QPushButton:hover { background: #eaf1ec; border-color: #99b5a6; }
    QPushButton:pressed { background: #dce8e0; }
    QPushButton:focus {
        border-color: #287d67;
    }
    QPushButton:disabled {
        background: #f2f4f1; color: #68766d; border-color: #d9e0da;
    }
    QPushButton[role="primary"]:enabled {
        background: #246553; color: #ffffff; border-color: #246553;
    }
    QPushButton[role="primary"]:enabled:hover { background: #1b5344; }
    QPushButton[role="primary"]:enabled:focus { border-color: #17392f; }
    QPushButton[role="danger"]:enabled {
        background: #fff4f0; color: #932f2d; border-color: #ddb7ad;
    }
    QPushButton[role="danger"]:enabled:hover { background: #f8e1da; }
    QPlainTextEdit {
        background: #f5f7f3; color: #34443b; padding: 5px;
        border: 1px solid #d8e0d9; border-radius: 3px;
        selection-color: #ffffff; selection-background-color: #286958;
    }
"""
