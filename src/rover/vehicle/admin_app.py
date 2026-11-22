"""The vehicle-side administration console for the SQLite expert runtime."""

import argparse
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic

from PySide6.QtCore import QSignalBlocker, QStandardPaths, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rover.desktop.appearance import CONSOLE_STYLE, desktop_font
from rover.vehicle.accounts import AccountStore
from rover.vehicle.app import main as vehicle_main
from rover.vehicle.expert_management_runtime import ExpertManagementRuntime, expert_management_listener


@dataclass(frozen=True)
class VehicleAdminConfig:
    cert_dir: Path
    account_db: Path
    bind_host: str = "127.0.0.1"
    management_port: int = 19177
    control_port: int = 9999


@dataclass(frozen=True)
class VehicleAdminStatus:
    phase: str
    sessions: tuple[dict[str, object], ...] = ()
    error: str | None = None


class VehicleAdminService:
    """Run the existing vehicle loop off the Qt thread and publish snapshots."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._thread: Thread | None = None
        self._stop_event = Event()
        self._phase = "idle"
        self._error: str | None = None
        self._runtime: ExpertManagementRuntime | None = None

    def is_active(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, config: VehicleAdminConfig) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("车端正在运行或停止中。")
            self._phase = "starting"
            self._error = None
            self._stop_event.clear()
            self._thread = Thread(target=self._run, args=(config,), name="vehicle-admin-service")
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._phase in ("starting", "running"):
                self._phase = "stopping"
                self._stop_event.set()

    def status(self) -> VehicleAdminStatus:
        with self._lock:
            phase, runtime, error = self._phase, self._runtime, self._error
        sessions = runtime.admin_snapshot() if runtime is not None else ()
        return VehicleAdminStatus(phase, sessions, error)

    def _run(self, config: VehicleAdminConfig) -> None:
        error = None
        try:
            accounts = AccountStore(config.account_db)
            if self._stop_event.is_set():
                return

            @contextmanager
            def management():
                with expert_management_listener(
                    config.cert_dir, {}, address=(config.bind_host, config.management_port), accounts=accounts,
                ) as runtime:
                    with self._lock:
                        self._runtime = runtime
                        if self._phase == "starting":
                            self._phase = "running"
                    yield runtime

            vehicle_main(
                management=management(),
                address=(config.bind_host, config.control_port),
                stop_event=self._stop_event,
            )
        except Exception as failure:
            error = str(failure)
        finally:
            with self._lock:
                self._runtime = None
                self._error = error
                self._phase = "error" if error is not None else "idle"


_PHASE_TEXT = {"idle": "车端已停止", "starting": "正在启动", "running": "车端运行中", "stopping": "正在停止"}
_ROLE_TEXT = {"observer": "观察员", "operator": "操作者"}
_TELEMETRY_TEXT = {"sent": "已发送", "pending": "等待发送", "error": "发送失败"}
_COLUMNS = ("账号", "角色", "管理连接", "遥测接收端点", "控制权", "最后活动", "遥测发送", "连接状态")


def _display_address(address: object) -> str:
    if isinstance(address, tuple) and len(address) == 2:
        return f"{address[0]}:{address[1]}"
    return "—"


class VehicleAdminWindow(QWidget):
    def __init__(self, config: VehicleAdminConfig, *, service: VehicleAdminService | None = None) -> None:
        super().__init__()
        self.service = service if service is not None else VehicleAdminService()
        self._close_requested = False
        self._validation_error: str | None = None
        self._rows: tuple[dict[str, object], ...] = ()

        self.setObjectName("console")
        self.setWindowTitle("Rover 车端管理")
        self.resize(1060, 670)
        self.setFont(desktop_font(14))
        self.setStyleSheet(CONSOLE_STYLE)
        layout = QVBoxLayout(self)
        heading = QLabel("Rover 车端管理")
        heading.setFont(desktop_font(24, bold=True))
        layout.addWidget(heading)

        settings = QGroupBox("服务设置")
        settings_form = QFormLayout(settings)
        self.cert_dir = QLineEdit(str(config.cert_dir))
        self.account_db = QLineEdit(str(config.account_db))
        cert_picker = QPushButton("浏览…")
        db_picker = QPushButton("浏览…")
        cert_picker.clicked.connect(self._choose_cert_dir)
        db_picker.clicked.connect(self._choose_account_db)
        settings_form.addRow("证书目录", self._path_row(self.cert_dir, cert_picker))
        settings_form.addRow("账号数据库", self._path_row(self.account_db, db_picker))

        self.bind_host = QLineEdit(config.bind_host)
        self.management_port = self._port_field(config.management_port)
        self.control_port = self._port_field(config.control_port)
        endpoints = QHBoxLayout()
        endpoints.addWidget(self.bind_host, 1)
        endpoints.addWidget(QLabel("管理 TCP"))
        endpoints.addWidget(self.management_port)
        endpoints.addWidget(QLabel("控制 UDP"))
        endpoints.addWidget(self.control_port)
        settings_form.addRow("监听 IPv4 地址", endpoints)
        self._settings_widgets = (
            self.cert_dir, self.account_db, cert_picker, db_picker, self.bind_host,
            self.management_port, self.control_port,
        )

        self.start_button = QPushButton("启动车端")
        self.start_button.setProperty("role", "primary")
        self.start_button.clicked.connect(self._start_service)
        self.stop_button = QPushButton("停止车端")
        self.stop_button.clicked.connect(self.service.stop)
        self.service_label = QLabel()
        self.service_label.setTextFormat(Qt.TextFormat.PlainText)
        self.service_label.setWordWrap(True)
        controls = QHBoxLayout()
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        controls.addWidget(self.service_label, 1)
        settings_form.addRow(controls)
        layout.addWidget(settings)

        self.session_table = QTableWidget(0, len(_COLUMNS))
        self.session_table.setHorizontalHeaderLabels(_COLUMNS)
        self.session_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.session_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.session_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.session_table.setAlternatingRowColors(True)
        self.session_table.verticalHeader().hide()
        header = self.session_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.session_table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(QLabel("桌面会话"))
        layout.addWidget(self.session_table, 2)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("选择会话查看近期事件。")
        layout.addWidget(QLabel("会话事件"))
        layout.addWidget(self.details, 1)

        role_controls = QHBoxLayout()
        self.role_account = QLineEdit()
        self.role_account.setPlaceholderText("已注册账号")
        self.role_choice = QComboBox()
        for value, label in _ROLE_TEXT.items():
            self.role_choice.addItem(label, value)
        self.role_button = QPushButton("更新角色")
        self.role_button.clicked.connect(self._set_role)
        self.role_message = QLabel()
        self.role_message.setTextFormat(Qt.TextFormat.PlainText)
        role_controls.addWidget(QLabel("账号权限"))
        role_controls.addWidget(self.role_account)
        role_controls.addWidget(self.role_choice)
        role_controls.addWidget(self.role_button)
        role_controls.addWidget(self.role_message, 1)
        layout.addLayout(role_controls)

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @staticmethod
    def _path_row(field: QLineEdit, picker: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(picker)
        return row

    @staticmethod
    def _port_field(value: int) -> QSpinBox:
        field = QSpinBox()
        field.setRange(1, 65535)
        field.setValue(value)
        return field

    def _choose_cert_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择证书目录", self.cert_dir.text())
        if path:
            self.cert_dir.setText(path)

    def _choose_account_db(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "选择账号数据库", self.account_db.text(), "SQLite (*.sqlite3 *.db);;所有文件 (*)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.account_db.setText(path)

    def _start_service(self) -> None:
        host = self.bind_host.text().strip()
        try:
            host = str(IPv4Address(host))
        except ValueError:
            self._validation_error = "请填写有效的监听 IPv4 地址。"
            self.refresh()
            return

        cert_dir, account_db = self.cert_dir.text().strip(), self.account_db.text().strip()
        self._validation_error = None
        if not cert_dir or not account_db:
            self._validation_error = "请选择证书目录和账号数据库。"
        else:
            try:
                self.service.start(VehicleAdminConfig(
                    Path(cert_dir), Path(account_db), host, self.management_port.value(), self.control_port.value(),
                ))
            except RuntimeError as error:
                self._validation_error = str(error)
        self.refresh()

    def _set_role(self) -> None:
        client_id, db = self.role_account.text().strip(), self.account_db.text().strip()
        if not client_id or not db or not Path(db).is_file():
            self.role_message.setText("请填写已注册账号并选择账号数据库。")
            return
        try:
            updated = AccountStore(Path(db)).set_role(client_id, self.role_choice.currentData())
        except (OSError, sqlite3.Error) as error:
            self.role_message.setText(f"账号数据库不可用：{error}")
            return
        self.role_message.setText("角色已更新。桌面可重新登录。" if updated else "找不到这个账号。")

    def refresh(self) -> None:
        state = self.service.status()
        inactive = state.phase in ("idle", "error")
        for widget in self._settings_widgets:
            widget.setEnabled(inactive)
        self.start_button.setEnabled(inactive)
        self.stop_button.setEnabled(state.phase in ("starting", "running"))
        if self._validation_error:
            self.service_label.setText(self._validation_error)
        elif state.error is not None:
            self.service_label.setText(f"车端服务失败：{state.error}")
        else:
            self.service_label.setText(_PHASE_TEXT[state.phase])

        self._update_sessions(state.sessions)
        if self._close_requested and not self.service.is_active():
            QTimer.singleShot(0, self.close)

    def _update_sessions(self, sessions: tuple[dict[str, object], ...]) -> None:
        selected_row = self.session_table.currentRow()
        selected_id = self._rows[selected_row]["session_id"] if 0 <= selected_row < len(self._rows) else None
        self._rows = sessions
        now_s = monotonic()
        select_row = 0 if sessions else -1
        with QSignalBlocker(self.session_table):
            self.session_table.setRowCount(len(sessions))
            for index, session in enumerate(sessions):
                if session["session_id"] == selected_id:
                    select_row = index
                timestamp = session.get("last_activity_s")
                age = f"{max(0.0, now_s - timestamp):.1f} 秒前" if isinstance(timestamp, (int, float)) else "—"
                result = session.get("telemetry_result")
                role = str(session.get("role", ""))
                values = (
                    str(session["client_id"]),
                    _ROLE_TEXT.get(role, role or "—"),
                    _display_address(session.get("peer_address")),
                    _display_address(session.get("telemetry_address")),
                    "持有" if session.get("holds_authority") else "—",
                    age,
                    _TELEMETRY_TEXT.get(str(result), str(result)) if result is not None else "—",
                    "在线" if session.get("online", True) else "已断开",
                )
                for column, value in enumerate(values):
                    item = self.session_table.item(index, column)
                    if item is None:
                        self.session_table.setItem(index, column, QTableWidgetItem(value))
                    elif item.text() != value:
                        item.setText(value)
            if select_row >= 0 and self.session_table.currentRow() != select_row:
                self.session_table.selectRow(select_row)
        self._show_details()

    def _show_details(self) -> None:
        row = self.session_table.currentRow()
        if 0 <= row < len(self._rows):
            events = self._rows[row].get("events", ())
            content = "\n".join(str(event) for event in events)
        else:
            content = ""
        if self.details.toPlainText() != content:
            self.details.setPlainText(content)

    def closeEvent(self, event) -> None:
        if self.service.is_active():
            self._close_requested = True
            self.service.stop()
            event.ignore()
        else:
            self._timer.stop()
            event.accept()


def _port_argument(raw: str) -> int:
    port = int(raw)
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("端口应在 1–65535 之间。")
    return port


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rover 车端管理")
    parser.add_argument("--cert-dir", type=Path, default=Path("local/certs"))
    parser.add_argument("--account-db", type=Path)
    parser.add_argument("--bind-host", type=IPv4Address, default=IPv4Address("127.0.0.1"))
    parser.add_argument("--management-port", type=_port_argument, default=19177)
    parser.add_argument("--control-port", type=_port_argument, default=9999)
    args = parser.parse_args(argv)

    app = QApplication(["RoverVehicle"])
    data_dir = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    accounts = args.account_db or Path(data_dir) / "RoverVehicle" / "accounts.sqlite3"
    window = VehicleAdminWindow(VehicleAdminConfig(
        args.cert_dir, accounts, str(args.bind_host), args.management_port, args.control_port,
    ))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
