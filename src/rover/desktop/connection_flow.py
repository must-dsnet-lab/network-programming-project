"""Coordinate the connection form and one desktop console at a time."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, QTimer, Slot
from PySide6.QtWidgets import QDialog, QMessageBox

from rover.desktop.connection_dialog import ConnectionDialog, ConnectionSelection
from rover.desktop.connection_profile import (
    DesktopConnectionSettings,
    load_desktop_profile,
    save_desktop_profile,
)
from rover.desktop.control_worker import SecureControlWorker
from rover.desktop.expert_control_worker import ExpertControlWorker
from rover.desktop.launch_dialog import choose_launch_role
from rover.desktop.observer_dialog import choose_telemetry_address
from rover.desktop.secure_connection import connect_configured_management


def default_profile_path() -> Path:
    directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericConfigLocation)
    base = Path(directory) if directory else Path.home() / ".config"
    return base / "Rover" / "desktop-connection.json"


def local_vehicle_log_directory() -> Path:
    directory = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericDataLocation)
    base = Path(directory) if directory else Path.home()
    return base / "Rover" / "logs"


class DesktopConnectionFlow(QObject):
    def __init__(self, application, console_factory, *, profile_path: Path | None = None):
        super().__init__()
        self._application = application
        self._console_factory = console_factory
        self._profile_path = profile_path if profile_path is not None else default_profile_path()
        self._window = None
        self._settings: DesktopConnectionSettings | None = None
        self._pending: ConnectionSelection | None = None
        self._application.setQuitOnLastWindowClosed(False)

    def attach(self, window, settings: DesktopConnectionSettings | None = None) -> None:
        self._window = window
        self._settings = settings
        window.connection_settings_requested.connect(self.configure)
        window.console_closed.connect(self._window_closed)
        window.show_connection_actions()

    def start_configured(self) -> bool:
        while (selection := self._ask_for_selection()) is not None:
            if self._create_configured_console(selection):
                return True
        return False

    def start_launcher(self) -> bool:
        while (role := choose_launch_role()) is not None:
            if role == "connect" and self.start_configured():
                return True
            if role == "local" and self._start_local():
                return True
            if role == "observe":
                address = choose_telemetry_address(None, ("127.0.0.1", 10001))
                if address is not None and self.start_observer(address):
                    return True
        return False

    def start_observer(self, address: tuple[str, int]) -> bool:
        window = self._open_console(read_only=True, telemetry_address=address)
        if window is None:
            return False
        self.attach(window)
        window.show()
        return True

    def _start_local(self) -> bool:
        window = self._open_console(local_demo=True, telemetry_address=("127.0.0.1", 0))
        if window is None:
            return False
        window.start_local_vehicle(local_vehicle_log_directory())
        self.attach(window)
        window.show()
        return True

    def _ask_for_selection(self) -> ConnectionSelection | None:
        try:
            profile = load_desktop_profile(self._profile_path)
        except (OSError, ValueError):
            QMessageBox.warning(self._window, "无法读取连接配置", "将使用当前配置或连接默认值。")
            profile = None

        settings = self._settings or profile or DesktopConnectionSettings()
        saved = profile is not None and profile == replace(settings, ca_file=settings.ca_file.resolve())
        dialog = ConnectionDialog(settings, saved=saved, parent=self._window)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            selection = dialog.take_selection()
        finally:
            dialog.deleteLater()

        if selection is not None and selection.remember:
            try:
                save_desktop_profile(self._profile_path, selection.settings)
            except (OSError, ValueError):
                QMessageBox.warning(
                    self._window, "无法保存连接配置",
                    "请检查配置目录权限，或取消勾选保存后重新连接。",
                )
                return None
        return selection

    @Slot()
    def configure(self) -> None:
        if self._window is None or self._pending is not None:
            return
        current = self._window.current_target
        if current is not None and current.speed_mps != 0.0:
            QMessageBox.information(self._window, "车辆控制进行中", "请先停车或断开连接，再打开连接设置。")
            return

        selection = self._ask_for_selection()
        if selection is not None:
            self._pending = selection
            self._window.close()

    @Slot()
    def _window_closed(self) -> None:
        window, self._window = self._window, None
        if window is not None:
            window.deleteLater()
        if self._pending is None:
            self._application.quit()
        else:
            QTimer.singleShot(0, self._finish_change)

    def _finish_change(self) -> None:
        selection, self._pending = self._pending, None
        if selection is not None and self._create_configured_console(selection):
            return
        if not self.start_configured():
            self._application.quit()

    def _create_configured_console(self, selection: ConnectionSelection) -> bool:
        settings = selection.settings
        self._settings = settings
        expert = settings.mode == "expert"
        worker_type = ExpertControlWorker if expert else SecureControlWorker
        peer_host = None

        @contextmanager
        def connect():
            nonlocal peer_host
            with connect_configured_management(settings, selection.credential) as client:
                peer_host = client.peer_host
                yield client

        def telemetry_peer_host() -> str | None:
            return peer_host

        worker = worker_type(connect, settings.control_address, use_management_peer=True)
        window = self._open_console(
            secure_worker=worker, telemetry_address=settings.telemetry_address,
            operator_id=settings.client_id, expert_mode=expert, require_initial_telemetry=True,
            telemetry_peer_host=telemetry_peer_host,
        )
        if window is None:
            return False
        self.attach(window, settings)
        window.show()
        return True

    def _open_console(self, **options):
        try:
            return self._console_factory(**options)
        except OSError:
            QMessageBox.critical(
                self._window, "遥测监听失败", "请核对本机监听地址和端口，并确认端口未被占用。",
            )
            return None
