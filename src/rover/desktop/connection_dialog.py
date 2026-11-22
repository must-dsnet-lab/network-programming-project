"""Collect desktop connection settings while keeping the credential transient."""

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from rover.desktop.appearance import CONSOLE_STYLE, desktop_font
from rover.desktop.connection_profile import DesktopConnectionSettings


_FORM_STYLE = """
    QDialog#connectionDialog { background: #f1f3f0; }
    QGroupBox { background: #fbfcfa; border: 1px solid #d7dfd9; border-radius: 5px;
                margin-top: 10px; padding: 7px 9px 5px; color: #24312c; }
    QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px;
                       background: #f1f3f0; color: #246553; }
    QLineEdit, QComboBox, QSpinBox { background: #ffffff; color: #24312c;
                                   border: 1px solid #cbd6cf; border-radius: 4px;
                                   padding: 4px 6px; min-height: 18px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #287d67; }
    QCheckBox { color: #24312c; }
    QLabel#connectionError { color: #8a481a; background: #fff4eb; padding: 7px;
                            border-left: 2px solid #dbaf8d; }
"""


@dataclass(frozen=True)
class ConnectionSelection:
    settings: DesktopConnectionSettings
    credential: str = field(repr=False)
    remember: bool


class ConnectionDialog(QDialog):
    def __init__(
        self, settings: DesktopConnectionSettings, *, saved: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._selection: ConnectionSelection | None = None
        self.setObjectName("connectionDialog")
        self.setWindowTitle("连接到车端")
        self.setMinimumWidth(520)
        self.setFont(desktop_font(14))
        self.setStyleSheet(CONSOLE_STYLE + _FORM_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title = QLabel("连接到车端")
        title.setFont(desktop_font(22, bold=True))
        layout.addWidget(title)

        remote = self._section(layout, "车端连接")
        self.mode_input = QComboBox()
        self.mode_input.addItem("高级模式", "advanced")
        self.mode_input.addItem("专家模式（双控制台）", "expert")
        self.mode_input.setCurrentIndex(self.mode_input.findData(settings.mode))
        self.vehicle_host_input = QLineEdit(settings.vehicle_host)
        self.management_port_input = self._port(settings.management_port)
        self.control_port_input = self._port(settings.control_port)
        self.server_name_input = QLineEdit(settings.server_name)
        self.server_name_input.setPlaceholderText("留空时自动填写")
        self.server_name_input.setToolTip("用于核对车端证书上的 DNS 名称或 IPv4 地址。")
        remote.addRow("模式", self.mode_input)
        remote.addRow("车端地址", self.vehicle_host_input)
        remote.addRow("管理 TCP", self._paired_fields(
            self.management_port_input, "管理 TCP", "控制 UDP", self.control_port_input,
        ))
        remote.addRow("证书名称", self.server_name_input)

        telemetry = self._section(layout, "遥测接收")
        self.telemetry_bind_host_input = QLineEdit(settings.telemetry_bind_host)
        self.telemetry_port_input = self._port(settings.telemetry_port)
        telemetry.addRow("本机地址", self._paired_fields(
            self.telemetry_bind_host_input, "遥测本机地址", "UDP 端口", self.telemetry_port_input,
        ))

        identity = self._section(layout, "信任与登录")
        self.ca_file_input = QLineEdit(str(settings.ca_file))
        self.ca_file_input.setAccessibleName("公开 CA 证书")
        self.browse_button = QPushButton("浏览…")
        self.browse_button.clicked.connect(self._browse_ca)
        ca_row = QHBoxLayout()
        ca_row.addWidget(self.ca_file_input, 1)
        ca_row.addWidget(self.browse_button)
        identity.addRow("公开 CA 证书", ca_row)
        self.client_id_input = QLineEdit(settings.client_id)
        identity.addRow("账号", self.client_id_input)
        self.credential_input = QLineEdit()
        self.credential_input.setEchoMode(QLineEdit.EchoMode.Password)
        identity.addRow("凭据", self.credential_input)

        self.remember_checkbox = QCheckBox("保存连接配置")
        self.remember_checkbox.setToolTip("保存端口、地址、证书路径和账号。每次连接都需填写凭据。")
        self.remember_checkbox.setChecked(saved)
        layout.addWidget(self.remember_checkbox)

        self.error_label = QLabel()
        self.error_label.setObjectName("connectionError")
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.clicked.connect(self.reject)
        self.connect_button = QPushButton("连接")
        self.connect_button.setProperty("role", "primary")
        self.connect_button.setDefault(True)
        self.connect_button.clicked.connect(self.accept)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.connect_button)
        layout.addLayout(buttons)
        self.credential_input.setFocus()

    @staticmethod
    def _section(layout: QVBoxLayout, name: str) -> QFormLayout:
        group = QGroupBox(name)
        form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(6)
        form.setContentsMargins(10, 8, 10, 10)
        layout.addWidget(group)
        return form

    @staticmethod
    def _port(value: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(1, 65535)
        field.setValue(value)
        return field

    @staticmethod
    def _paired_fields(first: QWidget, first_name: str, second_name: str, second: QWidget) -> QHBoxLayout:
        first.setAccessibleName(first_name)
        second.setAccessibleName(second_name)
        label = QLabel(second_name)
        label.setBuddy(second)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(first, 1)
        row.addWidget(label)
        row.addWidget(second, 1)
        return row

    def _browse_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择公开 CA 证书", self.ca_file_input.text(),
            "证书文件 (*.pem *.crt *.cer);;所有文件 (*)",
        )
        if path:
            self.ca_file_input.setText(path)

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def accept(self) -> None:
        self._selection = None
        ca_text = self.ca_file_input.text().strip()
        try:
            ca_file = Path(ca_text).expanduser()
            ca_exists = ca_file.is_file()
        except (OSError, RuntimeError, ValueError):
            ca_exists = False
        if not ca_text or not ca_exists:
            self._show_error("请选择可读取的公开 CA 证书文件。")
            self.ca_file_input.setFocus()
            return

        try:
            settings = DesktopConnectionSettings(
                mode=self.mode_input.currentData(),
                vehicle_host=self.vehicle_host_input.text(),
                management_port=self.management_port_input.value(),
                control_port=self.control_port_input.value(),
                telemetry_bind_host=self.telemetry_bind_host_input.text(),
                telemetry_port=self.telemetry_port_input.value(),
                server_name=self.server_name_input.text(),
                ca_file=ca_file,
                client_id=self.client_id_input.text(),
            )
        except ValueError as error:
            self._show_error(str(error))
            return

        credential = self.credential_input.text()
        if not credential.strip():
            self._show_error("请输入账号凭据。")
            self.credential_input.setFocus()
            return
        self._selection = ConnectionSelection(settings, credential, self.remember_checkbox.isChecked())
        super().accept()

    def done(self, result: int) -> None:
        self.credential_input.setText("")
        if result != QDialog.DialogCode.Accepted:
            self._selection = None
        super().done(result)

    def take_selection(self) -> ConnectionSelection | None:
        selection = self._selection
        self._selection = None
        self.credential_input.setText("")
        return selection
