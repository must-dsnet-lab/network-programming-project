"""Choose the local UDP endpoint for the read-only telemetry console."""

from ipaddress import IPv4Address
import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from rover.desktop.appearance import CONSOLE_STYLE, STATUS_COLORS, desktop_font


class TelemetryObserverDialog(QDialog):
    def __init__(self, address: tuple[str, int], parent: QWidget | None = None):
        super().__init__(parent)
        self.address: tuple[str, int] | None = None
        self.setObjectName("observerDialog")
        self.setWindowTitle("只读观察")
        self.setMinimumWidth(360)
        self.setFont(desktop_font(14))
        self.setStyleSheet(CONSOLE_STYLE + """
            QDialog#observerDialog { background: #f1f3f0; }
            QLineEdit {
                background: #ffffff; color: #24312c; border: 1px solid #cbd6cf;
                border-radius: 4px; padding: 4px 6px; min-height: 18px;
            }
            QLineEdit:focus { border-color: #287d67; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        title = QLabel("只读观察")
        title.setFont(desktop_font(21, bold=True))
        layout.addWidget(title)
        layout.addWidget(QLabel("填写车端发送遥测时使用的本机接收地址和端口。"))

        host, port = address
        self.host_input = QLineEdit(host)
        self.host_input.setToolTip("填写本机 IPv4 地址。使用 0.0.0.0 可监听所有本机 IPv4 接口。")
        self.port_input = QLineEdit(str(port))
        form = QFormLayout()
        form.addRow("本机 IPv4", self.host_input)
        form.addRow("UDP 端口", self.port_input)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {STATUS_COLORS['alert']};")
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setAutoDefault(False)
        self.listen_button = buttons.addButton("开始接收", QDialogButtonBox.ButtonRole.AcceptRole)
        self.listen_button.setProperty("role", "primary")
        self.listen_button.setDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.host_input.setFocus()

    def accept(self) -> None:
        try:
            host = IPv4Address(self.host_input.text().strip())
        except ValueError:
            self._show_error("请输入有效的本机 IPv4 地址。", self.host_input)
            return

        port = self.port_input.text().strip()
        if re.fullmatch(r"[0-9]{1,5}", port) is None or not 1 <= int(port) <= 65535:
            self._show_error("UDP 端口须为 1 至 65535 的整数。", self.port_input)
            return

        self.address = (str(host), int(port))
        super().accept()

    def _show_error(self, message: str, field: QLineEdit) -> None:
        self.error_label.setText(message)
        self.error_label.show()
        field.setFocus()


def choose_telemetry_address(parent: QWidget | None, address: tuple[str, int]) -> tuple[str, int] | None:
    dialog = TelemetryObserverDialog(address, parent)
    try:
        if dialog.exec() == QDialog.DialogCode.Accepted:
            return dialog.address
        return None
    finally:
        dialog.deleteLater()
