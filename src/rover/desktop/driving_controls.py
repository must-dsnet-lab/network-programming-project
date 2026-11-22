"""Momentary driving inputs for the desktop console."""

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QSlider, QStyle, QStyleOptionSlider, QWidget

from rover.core.driving import DriveInput
from rover.desktop.appearance import desktop_font


_STEERING_DEAD_ZONE = 0.08
_DRIVING_KEYS = (Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_W, Qt.Key.Key_S)
_SLIDER_STYLE = """
    QSlider { min-height: 28px; }
    QSlider::groove:horizontal {
        background: #d9e5dd; height: 6px; border-radius: 3px;
    }
    QSlider::handle:horizontal {
        background: #246553; border: 1px solid #17483b; border-radius: 9px;
        width: 18px; margin: -7px 0;
    }
    QSlider#driveBrake::handle:horizontal { background: #9d3d37; border-color: #76312c; }
    QSlider::handle:horizontal:disabled { background: #aebdb4; border-color: #8fa197; }
    QSlider:focus { border: 1px solid #287d67; border-radius: 4px; }
"""


class _SpringSlider(QSlider):
    pointer_changed = Signal()

    def __init__(self, minimum: int, name: str) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(minimum, 100)
        self.setValue(0)
        self.setAccessibleName(name)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.pointer_value = 0

    def _move_pointer(self, x: int) -> None:
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        style = self.style()
        control = QStyle.ComplexControl.CC_Slider
        groove = style.subControlRect(control, option, QStyle.SubControl.SC_SliderGroove, self)
        handle = style.subControlRect(control, option, QStyle.SubControl.SC_SliderHandle, self)
        span = max(1, groove.width() - handle.width())
        offset = x - (groove.left() + handle.width() // 2)
        position = max(0, min(span, offset))
        self._set_pointer_value(QStyle.sliderValueFromPosition(self.minimum(), self.maximum(), position, span))

    def _set_pointer_value(self, value: int) -> None:
        if value != self.pointer_value:
            self.pointer_value = value
            self.pointer_changed.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._move_pointer(round(event.position().x()))
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self.isSliderDown():
            self._move_pointer(round(event.position().x()))
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            self._set_pointer_value(0)
            event.accept()

    def wheelEvent(self, event) -> None:
        event.ignore()

    def keyPressEvent(self, event) -> None:
        event.ignore()


class DrivingControls(QWidget):
    """Convert pointer and WASD input into an independent direction and two pedals."""

    input_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("二维驾驶输入")
        self.setStyleSheet(_SLIDER_STYLE)
        self._held_keys: set[Qt.Key] = set()
        self._input = DriveInput(0, 0, 0)
        self.steering_slider = _SpringSlider(-100, "方向摇杆")
        self.throttle_slider = _SpringSlider(0, "油门")
        self.brake_slider = _SpringSlider(0, "刹车")
        self.brake_slider.setObjectName("driveBrake")
        self._sliders = (self.steering_slider, self.throttle_slider, self.brake_slider)

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(2)
        layout.setColumnStretch(1, 1)
        self._readouts: list[QLabel] = []
        for row, slider in enumerate(self._sliders):
            label = QLabel(slider.accessibleName())
            label.setBuddy(slider)
            layout.addWidget(label, row, 0)
            layout.addWidget(slider, row, 1)
            reading = QLabel()
            reading.setFont(desktop_font(13, numeric=True))
            reading.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            reading.setMinimumWidth(reading.fontMetrics().horizontalAdvance("右 100%") + 3)
            self._readouts.append(reading)
            layout.addWidget(reading, row, 2)
            slider.installEventFilter(self)
            slider.sliderPressed.connect(self._focus_for_pointer)
            slider.pointer_changed.connect(self._publish_input)

        guidance = QLabel("拖动控件，或按 A/D 转向、W 加油、S 刹车。松开自动归位。")
        guidance.setWordWrap(True)
        guidance.setProperty("role", "muted")
        layout.addWidget(guidance, len(self._sliders), 0, 1, 3)
        self.installEventFilter(self)
        self._update_readouts()

    def input_value(self) -> DriveInput:
        return self._input

    def _focus_for_pointer(self) -> None:
        focused = QApplication.focusWidget()
        if focused is not self and (focused is None or not self.isAncestorOf(focused)):
            self.setFocus(Qt.FocusReason.MouseFocusReason)

    def _publish_input(self) -> None:
        steering = -self.steering_slider.pointer_value / 100
        if abs(steering) <= _STEERING_DEAD_ZONE:
            steering = 0.0
        left = Qt.Key.Key_A in self._held_keys
        right = Qt.Key.Key_D in self._held_keys
        if left or right:
            steering = float(left - right)
        throttle = 1.0 if Qt.Key.Key_W in self._held_keys else self.throttle_slider.pointer_value / 100
        brake = 1.0 if Qt.Key.Key_S in self._held_keys else self.brake_slider.pointer_value / 100
        drive_input = DriveInput(throttle, brake, steering)
        if drive_input != self._input:
            self._input = drive_input
            self._update_readouts()
            self.input_changed.emit()

    def _update_readouts(self) -> None:
        steering = self._input.steering
        direction = ("左" if steering > 0 else "右") if steering else ""
        self.steering_slider.setValue(round(-steering * 100))
        self.throttle_slider.setValue(round(self._input.throttle * 100))
        self.brake_slider.setValue(round(self._input.brake * 100))
        self._readouts[0].setText(f"{direction} {abs(steering):.0%}" if direction else "回中")
        self._readouts[1].setText(f"{self._input.throttle:.0%}")
        self._readouts[2].setText(f"{self._input.brake:.0%}")

    def reset_inputs(self) -> None:
        """Return every control to neutral without submitting another input."""
        self._held_keys.clear()
        for slider in self._sliders:
            with QSignalBlocker(slider):
                slider.setSliderDown(False)
                slider.pointer_value = 0
        self._input = DriveInput(0, 0, 0)
        self._update_readouts()

    def eventFilter(self, watched, event) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.FocusOut and self._held_keys:
            self._held_keys.clear()
            self._publish_input()
        if event_type not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            return super().eventFilter(watched, event)
        key = event.key()
        if not self.isEnabled() or key not in _DRIVING_KEYS:
            return super().eventFilter(watched, event)
        if event_type == QEvent.Type.KeyPress and event.modifiers() & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier
        ):
            return super().eventFilter(watched, event)
        if not event.isAutoRepeat():
            if event_type == QEvent.Type.KeyPress:
                self._held_keys.add(key)
            else:
                self._held_keys.discard(key)
            self._publish_input()
        return True
