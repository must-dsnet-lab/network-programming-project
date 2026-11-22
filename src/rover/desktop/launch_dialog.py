"""Select a desktop role before opening any network sockets."""

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from rover.desktop.appearance import CONSOLE_STYLE, desktop_font


def choose_launch_role() -> str | None:
    dialog = QDialog()
    dialog.setWindowTitle("虚拟小车")
    dialog.setObjectName("console")
    dialog.setMinimumWidth(380)
    dialog.setFont(desktop_font(14))
    dialog.setStyleSheet(CONSOLE_STYLE)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(10)
    title = QLabel("虚拟小车")
    title.setFont(desktop_font(23, bold=True))
    layout.addWidget(title)

    choices = {
        "local": ("本地模拟", "启动本机虚拟车，练习二维操作"),
        "connect": ("连接车端", "登录已运行的高级或专家车端"),
        "observe": ("只读观察", "查看地图、轨迹和车端遥测"),
    }
    for index, (label, description) in enumerate(choices.values(), start=2):
        button = QPushButton(label)
        button.setMinimumHeight(36)
        button.setAutoDefault(False)
        button.clicked.connect(lambda checked=False, result=index: dialog.done(result))
        layout.addWidget(button)
        layout.addWidget(QLabel(description))
    layout.addSpacing(4)

    try:
        result = dialog.exec()
        return tuple(choices)[result - 2] if result >= 2 else None
    finally:
        dialog.deleteLater()
