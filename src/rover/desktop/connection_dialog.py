"""Collect desktop connection settings while keeping the credential transient."""

from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from threading import Thread

from PySide6.QtCore import Qt, QTimer
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

from rover.desktop import secure_connection
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
    QLabel#connectionStatus { color: #246553; background: #e7f3ed; padding: 7px;
                             border-left: 2px solid #63a38d; }
"""


_REGISTRATION_REJECTIONS = {
    "invalid_account": "账号须以字母或数字开头，最多 64 个字符，可用英文字母、数字、下划线、点或连字符。",
    "invalid_credential": "注册凭据至少需要 12 个字符，并且长度不能超过 1024 字节。",
    "account_exists": "该账号已存在。请使用原凭据连接，或填写新账号。",
    "registration_unavailable": "车端暂未启用账号注册。",
}
_REGISTRATION_SETUP_ERRORS = {
    "certificate": "证书校验失败。请核对 CA 文件和车端证书名称。",
    "network": "无法连接车端。请检查车端地址和管理端口。",
    "protocol": "车端注册协议响应无效。请检查车端的启动模式。",
}


def _register_in_background(
    settings: DesktopConnectionSettings,
    credential: str,
    outcome: Future[str | None],
) -> None:
    if not outcome.set_running_or_notify_cancel():
        return
    try:
        reason = secure_connection.register_configured_account(settings, credential)
        message = _REGISTRATION_REJECTIONS.get(reason, "车端拒绝了账号注册。") if reason else None
    except secure_connection.ConnectionSetupError as error:
        message = _REGISTRATION_SETUP_ERRORS.get(error.kind, "无法完成账号注册。")
    except OSError:
        message = _REGISTRATION_SETUP_ERRORS["network"]
    except ValueError:
        message = _REGISTRATION_SETUP_ERRORS["protocol"]
    except Exception:
        # Always restore the form, and keep request details off the GUI and console.
        message = "无法完成账号注册。请重试。"
    outcome.set_result(message)


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
        self._registration: Future[str | None] | None = None
        self._registration_timer = QTimer(self)
        self._registration_timer.setInterval(40)
        self._registration_timer.timeout.connect(self._finish_registration)
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
        self.mode_input.addItem("用户版（自动遥测）", "product")
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
        self._telemetry_group = telemetry.parentWidget()
        self._telemetry_group.setObjectName("telemetrySettings")
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
        self._form_groups = (remote.parentWidget(), self._telemetry_group, identity.parentWidget())

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

        self.status_label = QLabel()
        self.status_label.setObjectName("connectionStatus")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.register_button = QPushButton("注册账号")
        self.register_button.setAutoDefault(False)
        self.register_button.clicked.connect(self._register_account)
        buttons.addWidget(self.register_button)
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
        self.mode_input.currentIndexChanged.connect(self._update_mode_fields)
        self._update_mode_fields()
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
        self.status_label.hide()
        self.error_label.setText(message)
        self.error_label.show()

    def _show_status(self, message: str) -> None:
        self.error_label.hide()
        self.status_label.setText(message)
        self.status_label.show()

    def _update_mode_fields(self) -> None:
        product = self.mode_input.currentData() == "product"
        self._telemetry_group.setVisible(not product)
        self.register_button.setVisible(product)
        self.status_label.hide()
        self.error_label.hide()

    def _read_selection(self) -> ConnectionSelection | None:
        ca_text = self.ca_file_input.text().strip()
        try:
            ca_file = Path(ca_text).expanduser()
            ca_exists = ca_file.is_file()
        except (OSError, RuntimeError, ValueError):
            ca_exists = False
        if not ca_text or not ca_exists:
            self._show_error("请选择可读取的公开 CA 证书文件。")
            self.ca_file_input.setFocus()
            return None

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
            return None

        credential = self.credential_input.text()
        if not credential.strip():
            self._show_error("请输入账号凭据。")
            self.credential_input.setFocus()
            return None
        return ConnectionSelection(settings, credential, self.remember_checkbox.isChecked())

    def _set_registration_pending(self, pending: bool) -> None:
        for group in self._form_groups:
            group.setEnabled(not pending)
        self.remember_checkbox.setEnabled(not pending)
        self.connect_button.setEnabled(not pending)
        self.register_button.setEnabled(not pending)
        self.cancel_button.setText("关闭" if pending else "取消")
        self.cancel_button.setToolTip("关闭窗口后，已送达车端的注册请求仍可能完成。" if pending else "")

    def _register_account(self) -> None:
        if self.mode_input.currentData() != "product" or self._registration is not None:
            return
        selection = self._read_selection()
        if selection is None:
            return
        self.credential_input.clear()
        outcome: Future[str | None] = Future()
        self._registration = outcome
        self._set_registration_pending(True)
        self._show_status("正在注册账号…")
        try:
            Thread(
                target=_register_in_background,
                args=(selection.settings, selection.credential, outcome),
                name="desktop-account-registration",
                daemon=True,
            ).start()
        except RuntimeError:
            self._registration = None
            self._set_registration_pending(False)
            self._show_error("无法启动账号注册。请重试。")
            return
        self._registration_timer.start()

    def _finish_registration(self) -> None:
        outcome = self._registration
        if outcome is None or not outcome.done():
            return
        self._registration_timer.stop()
        self._registration = None
        self._set_registration_pending(False)
        message = outcome.result()
        if message is None:
            self._show_status("注册成功。新账号默认只读。请重新输入凭据并点击连接。")
        else:
            self._show_error(message)
        self.credential_input.setFocus()

    def accept(self) -> None:
        if self._registration is not None:
            return
        self._selection = self._read_selection()
        if self._selection is not None:
            super().accept()

    def done(self, result: int) -> None:
        if self._registration is not None:
            self._registration_timer.stop()
            self._registration.cancel()
            self._registration = None
            self._set_registration_pending(False)
            self.status_label.hide()
        self.credential_input.setText("")
        if result != QDialog.DialogCode.Accepted:
            self._selection = None
        super().done(result)

    def take_selection(self) -> ConnectionSelection | None:
        selection = self._selection
        self._selection = None
        self.credential_input.setText("")
        return selection
