"""The vehicle-side administration console for the SQLite expert runtime."""

import argparse
import sqlite3
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from ipaddress import IPv4Address
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import cast

from PySide6.QtCore import QSignalBlocker, QStandardPaths, Qt, QTimer
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from rover.desktop.appearance import CONSOLE_STYLE, PortSpinBox, desktop_font
from rover.protocol.connection_bundle import ca_certificate_fingerprint, write_connection_bundle
from rover.vehicle.accounts import AccountStore
from rover.vehicle.app import main as vehicle_main
from rover.vehicle.expert_management_runtime import ExpertManagementRuntime, expert_management_listener
from rover.vehicle.lan_firewall import (
    FirewallCheck,
    FirewallPlan,
    configure_lan_firewall,
    inspect_lan_firewall,
    supports_lan_firewall,
)
from rover.vehicle.managed_tls import ensure_vehicle_certificate


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


@dataclass(frozen=True)
class _FirewallStartup:
    config: VehicleAdminConfig
    phase: str
    outcome: Future[FirewallCheck | bool] | None


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
_FIREWALL_PHASE_TEXT = {
    "checking": "正在检查局域网防火墙规则…",
    "confirming": "等待确认局域网访问…",
    "configuring": "正在配置局域网访问，请确认 Windows 的管理员授权…",
}
_ROLE_TEXT = {"observer": "观察员", "operator": "操作者"}
_TELEMETRY_TEXT = {"sent": "已发送", "pending": "等待发送", "error": "发送失败"}
_COLUMNS = ("账号", "角色", "管理连接", "遥测接收端点", "控制权", "最后活动", "遥测发送", "连接状态")


def _rover_vehicle_data_dir() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    return Path(location) / "RoverVehicle"


def _available_ipv4_addresses() -> list[tuple[str, str]]:
    flags = QNetworkInterface.InterfaceFlag
    types = QNetworkInterface.InterfaceType
    active = flags.IsUp | flags.IsRunning
    virtual_names = ("vmware", "virtualbox", "vbox", "vethernet", "vpn", "tailscale", "zerotier", "wireguard")
    candidates = []
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        if state & active != active or state & flags.IsLoopBack:
            continue
        name = interface.humanReadableName()
        normalized_name = name.casefold()
        likely_physical = (
            interface.type() in (types.Wifi, types.Ethernet)
            and not any(marker in normalized_name for marker in virtual_names)
        )
        for entry in interface.addressEntries():
            address = entry.ip()
            if address.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            ipv4 = IPv4Address(address.toString())
            if ipv4.is_loopback or ipv4.is_unspecified or ipv4.is_multicast or ipv4.is_reserved:
                continue
            candidates.append((ipv4.is_link_local, not likely_physical, name, str(ipv4)))
    return [(name, address) for _, _, name, address in sorted(candidates)]


def _sign_vehicle_certificate(store_dir: Path, host: str, outcome: Future[Path]) -> None:
    try:
        outcome.set_result(ensure_vehicle_certificate(store_dir, host))
    except Exception as error:
        outcome.set_exception(error)


def _run_firewall_operation(
    config: VehicleAdminConfig, plan: FirewallPlan | None, outcome: Future[FirewallCheck | bool],
) -> None:
    try:
        if plan is None:
            outcome.set_result(inspect_lan_firewall(config.bind_host, config.management_port))
        else:
            outcome.set_result(configure_lan_firewall(plan))
    except Exception as error:
        outcome.set_exception(error)


def _display_address(address: object) -> str:
    if isinstance(address, tuple) and len(address) == 2:
        return f"{address[0]}:{address[1]}"
    return "—"


class VehicleAdminWindow(QWidget):
    def __init__(
        self, config: VehicleAdminConfig, *, service: VehicleAdminService | None = None,
        managed_cert_store: Path | None = None,
    ) -> None:
        super().__init__()
        self.service = service if service is not None else VehicleAdminService()
        self._managed_cert_store = (
            managed_cert_store if managed_cert_store is not None else _rover_vehicle_data_dir() / "tls"
        )
        self._certificate_job: tuple[str, Future[Path]] | None = None
        self._managed_selection: tuple[str, Path] | None = None
        self._firewall_job: _FirewallStartup | None = None
        self._firewall_notice: str | None = None
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
        self.detect_ip_button = QPushButton("检测本机 IP")
        self.detect_ip_button.clicked.connect(self._choose_local_ipv4)
        self.management_port = PortSpinBox(config.management_port)
        self.control_port = PortSpinBox(config.control_port)
        endpoints = QHBoxLayout()
        endpoints.addWidget(self.bind_host, 1)
        endpoints.addWidget(self.detect_ip_button)
        endpoints.addWidget(QLabel("管理 TCP"))
        endpoints.addWidget(self.management_port)
        endpoints.addWidget(QLabel("控制 UDP"))
        endpoints.addWidget(self.control_port)
        settings_form.addRow("监听 IPv4 地址", endpoints)

        self.certificate_button = QPushButton("生成／更新证书")
        self.certificate_button.clicked.connect(self._generate_certificate)
        self.export_bundle_button = QPushButton("导出连接包")
        self.export_bundle_button.clicked.connect(self._export_connection_bundle)
        self.provisioning_label = QLabel("生成证书后可导出局域网连接包。")
        self.provisioning_label.setTextFormat(Qt.TextFormat.PlainText)
        self.provisioning_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.provisioning_label.setWordWrap(True)
        provisioning = QHBoxLayout()
        provisioning.addWidget(self.certificate_button)
        provisioning.addWidget(self.export_bundle_button)
        provisioning.addWidget(self.provisioning_label, 1)
        settings_form.addRow(provisioning)
        self.bind_host.textChanged.connect(self._update_export_availability)
        self.cert_dir.textChanged.connect(self._update_export_availability)
        self._settings_widgets = (
            self.cert_dir, self.account_db, cert_picker, db_picker, self.bind_host,
            self.management_port, self.control_port, self.detect_ip_button, self.certificate_button,
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

    def _choose_local_ipv4(self) -> None:
        candidates = _available_ipv4_addresses()
        if not candidates:
            self.provisioning_label.setText("未检测到在线网卡的 IPv4 地址。")
            return
        labels = {f"{name} · {address}": address for name, address in candidates}
        current = self.bind_host.text().strip()
        suggested = next((index for index, address in enumerate(labels.values()) if address == current), 0)
        choice, accepted = QInputDialog.getItem(self, "检测本机 IP", "选择监听网卡地址", list(labels), suggested, False)
        if accepted and choice in labels:
            self.bind_host.setText(labels[choice])
            self.provisioning_label.setText("已选择本机地址，可为该地址生成或更新证书。")

    def _generate_certificate(self) -> None:
        if self._certificate_job is not None or self._firewall_job is not None or self.service.is_active():
            return
        try:
            address = IPv4Address(self.bind_host.text().strip())
        except ValueError:
            self.provisioning_label.setText("请先选择或填写有效的监听 IPv4 地址。")
            return
        if address.is_unspecified or address.is_multicast or address.is_reserved:
            self.provisioning_label.setText("请填写网卡的具体 IPv4 地址。")
            return
        host = str(address)
        outcome: Future[Path] = Future()
        self._certificate_job = (host, outcome)
        self.provisioning_label.setText("正在检查并准备车端证书…")
        try:
            Thread(
                target=_sign_vehicle_certificate,
                args=(self._managed_cert_store, host, outcome),
                name="vehicle-admin-certificate",
            ).start()
        except RuntimeError:
            self._certificate_job = None
            self.provisioning_label.setText("无法启动证书准备，请重试。")
        self.refresh()

    def _finish_certificate_job(self) -> None:
        if self._certificate_job is None:
            return
        host, outcome = self._certificate_job
        if not outcome.done():
            return
        self._certificate_job = None
        try:
            directory = outcome.result()
        except Exception as error:
            self.provisioning_label.setText(f"证书准备失败：{error}")
            return
        self._managed_selection = (host, directory)
        self.cert_dir.setText(str(directory))
        if IPv4Address(host).is_loopback:
            self.provisioning_label.setText("证书已就绪，可启动车端。")
        else:
            self.provisioning_label.setText("证书已就绪，可启动车端或导出连接包。")

    def _update_export_availability(self) -> None:
        can_export = (
            self._certificate_job is None
            and self._firewall_job is None
            and self._managed_selection is not None
            and self._managed_selection == (self.bind_host.text().strip(), Path(self.cert_dir.text().strip()))
            and not IPv4Address(self._managed_selection[0]).is_loopback
        )
        self.export_bundle_button.setEnabled(can_export and not self._close_requested)

    def _export_connection_bundle(self) -> None:
        if not self.export_bundle_button.isEnabled() or self._managed_selection is None:
            return
        host, directory = self._managed_selection
        path, _ = QFileDialog.getSaveFileName(
            self, "导出连接包", str(Path.home() / "Rover.rover-connection.json"),
            "Rover 连接包 (*.rover-connection.json)",
        )
        if not path:
            return
        if not path.lower().endswith(".rover-connection.json"):
            self.provisioning_label.setText("连接包文件名须以 .rover-connection.json 结尾。")
            return
        public_ca = directory / "ca.pem"
        try:
            fingerprint = ca_certificate_fingerprint(public_ca)
            write_connection_bundle(
                Path(path), vehicle_host=host, management_port=self.management_port.value(),
                control_port=self.control_port.value(), ca_file=public_ca,
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.provisioning_label.setText(f"连接包导出失败：{error}")
            return
        self.provisioning_label.setText(f"已导出连接包。公开 CA 的 SHA-256 指纹\n{fingerprint}")

    def _start_service(self) -> None:
        if self._certificate_job is not None or self._firewall_job is not None or self._close_requested:
            return
        self._firewall_notice = None
        host = self.bind_host.text().strip()
        try:
            host = str(IPv4Address(host))
        except ValueError:
            self._validation_error = "请填写有效的监听 IPv4 地址。"
            self.refresh()
            return

        cert_dir, account_db = self.cert_dir.text().strip(), self.account_db.text().strip()
        self._validation_error = None
        if self._managed_selection is not None:
            signed_host, signed_directory = self._managed_selection
            if Path(cert_dir) == signed_directory and host != signed_host:
                self._validation_error = "监听地址已更改，请先生成或更新证书。"
                self.refresh()
                return
        if not cert_dir or not account_db:
            self._validation_error = "请选择证书目录和账号数据库。"
        else:
            config = VehicleAdminConfig(
                Path(cert_dir), Path(account_db), host, self.management_port.value(), self.control_port.value(),
            )
            if supports_lan_firewall(host):
                self._start_firewall_operation(config)
            else:
                self._launch_service(config)
        self.refresh()

    def _launch_service(self, config: VehicleAdminConfig) -> None:
        self._firewall_job = None
        if self._close_requested:
            return
        try:
            self.service.start(config)
        except RuntimeError as error:
            self._validation_error = str(error)

    def _start_firewall_operation(self, config: VehicleAdminConfig, plan: FirewallPlan | None = None) -> None:
        phase = "checking" if plan is None else "configuring"
        outcome: Future[FirewallCheck | bool] = Future()
        self._firewall_job = _FirewallStartup(config, phase, outcome)
        try:
            Thread(
                target=_run_firewall_operation,
                args=(config, plan, outcome),
                name=f"vehicle-admin-firewall-{phase}",
            ).start()
        except RuntimeError as error:
            outcome.set_exception(error)

    def _finish_firewall_job(self) -> None:
        job = self._firewall_job
        if job is None or job.outcome is None or not job.outcome.done():
            return
        if self._close_requested:
            self._firewall_job = None
            return

        # Qt's modal dialog processes timer events. Keep the controls locked and
        # consume the result before showing it to prevent duplicate dialogs.
        self._firewall_job = _FirewallStartup(job.config, "confirming", None)
        try:
            result = job.outcome.result()
        except Exception as error:
            action = "配置" if job.phase == "configuring" else "检查"
            self._confirm_start_with_existing_rules(job.config, f"无法{action}局域网防火墙规则。{error}")
            return

        if job.phase == "configuring":
            if result is True:
                self._launch_service(job.config)
            else:
                self._confirm_start_with_existing_rules(job.config, "Windows 管理员授权已取消。")
            return

        check = cast(FirewallCheck, result)
        if check.ready:
            self._launch_service(job.config)
            return

        plan = check.plan
        profile = "公用网络" if plan.profile == "Public" else "专用网络"
        public_scope = f"公用网络仅使用当前监听地址 {plan.bind_host}。\n" if plan.profile == "Public" else ""
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(
            self, "允许局域网访问",
            f"在 Windows 的{profile}配置中，仅允许本地子网访问此车端程序的"
            f"管理 TCP {job.config.management_port} 和控制及遥测 UDP。\n"
            f"{public_scope}允许后将请求 Windows 管理员授权，随后启动车端。\n"
            "选择「否」可沿用现有规则启动车端。选择「取消」可返回设置。",
            buttons.Yes | buttons.No | buttons.Cancel, buttons.Cancel,
        )
        if self._close_requested:
            self._firewall_job = None
        elif answer == buttons.Yes:
            self._start_firewall_operation(job.config, plan)
        elif answer == buttons.No:
            self._firewall_notice = "已沿用现有防火墙规则。"
            self._launch_service(job.config)
        else:
            self._firewall_job = None

    def _confirm_start_with_existing_rules(self, config: VehicleAdminConfig, reason: str) -> None:
        buttons = QMessageBox.StandardButton
        choice = QMessageBox.question(
            self, "局域网防火墙", f"{reason}\n是否沿用现有防火墙规则启动车端？",
            buttons.Yes | buttons.No, buttons.No,
        )
        self._firewall_job = None
        if not self._close_requested and choice == buttons.Yes:
            self._firewall_notice = "已沿用现有防火墙规则。"
            self._launch_service(config)
        elif not self._close_requested:
            self._validation_error = reason

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
        self._finish_certificate_job()
        self._finish_firewall_job()
        state = self.service.status()
        inactive = state.phase in ("idle", "error")
        settings_enabled = (
            inactive and self._certificate_job is None and self._firewall_job is None and not self._close_requested
        )
        for widget in self._settings_widgets:
            widget.setEnabled(settings_enabled)
        self.start_button.setEnabled(settings_enabled)
        self._update_export_availability()
        self.stop_button.setEnabled(state.phase in ("starting", "running"))
        if self._validation_error:
            self.service_label.setText(self._validation_error)
        elif self._firewall_job is not None:
            self.service_label.setText(_FIREWALL_PHASE_TEXT[self._firewall_job.phase])
        elif state.error is not None:
            self.service_label.setText(f"车端服务失败：{state.error}")
        elif self._firewall_notice:
            self.service_label.setText(f"{_PHASE_TEXT[state.phase]}。{self._firewall_notice}")
        else:
            self.service_label.setText(_PHASE_TEXT[state.phase])

        self._update_sessions(state.sessions)
        if (
            self._close_requested and self._certificate_job is None and self._firewall_job is None
            and not self.service.is_active()
        ):
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
        service_active = self.service.is_active()
        if service_active or self._certificate_job is not None or self._firewall_job is not None:
            self._close_requested = True
            if service_active:
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
    accounts = args.account_db or _rover_vehicle_data_dir() / "accounts.sqlite3"
    window = VehicleAdminWindow(VehicleAdminConfig(
        args.cert_dir, accounts, str(args.bind_host), args.management_port, args.control_port,
    ))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
