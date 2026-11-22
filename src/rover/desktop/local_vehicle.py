"""Own the loopback vehicle started by the desktop."""

from contextlib import suppress
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time


_READY_LINE = re.compile(
    rb"^Local rover ready: control_port=([0-9]{1,5}) "
    rb"telemetry_source_port=([0-9]{1,5})\r?\n",
    re.MULTILINE,
)
_START_TIMEOUT_S = 10.0
_STOP_TIMEOUT_S = 0.8


@dataclass(frozen=True)
class LocalVehiclePorts:
    control_port: int
    telemetry_source_port: int


class LocalVehicleStartupError(RuntimeError):
    """The loopback vehicle failed to become ready."""


class LocalVehicleProcess:
    def __init__(self, process: subprocess.Popen[bytes], log_path: Path) -> None:
        self._process = process
        self.log_path = log_path
        self._start_deadline = time.monotonic() + _START_TIMEOUT_S
        self._ports: LocalVehiclePorts | None = None

    @classmethod
    def start(cls, telemetry_port: int, log_directory: Path) -> "LocalVehicleProcess":
        if type(telemetry_port) is not int or not 1 <= telemetry_port <= 65535:
            raise ValueError("遥测端口必须介于 1 和 65535 之间。")
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--vehicle"]
        else:
            command = [sys.executable, "-X", "utf8", "-u", "-B", "-m", "rover.vehicle.app"]
        command.extend((
            "--bind-host", "127.0.0.1", "--telemetry-host", "127.0.0.1",
            "--telemetry-port", str(telemetry_port), "--auto-control-port",
        ))

        log_directory.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=log_directory, prefix="vehicle-", suffix=".log", delete=False) as log:
            log_path = Path(log.name)
            try:
                process = subprocess.Popen(
                    command, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError as error:
                raise LocalVehicleStartupError(f"无法启动本机车端（{error}）。日志 {log_path}") from error
        return cls(process, log_path)

    @property
    def returncode(self) -> int | None:
        return self._process.poll()

    def poll_ready(self) -> LocalVehiclePorts | None:
        """Read the vehicle's first complete readiness record without blocking."""
        if (returncode := self.returncode) is not None:
            self.stop()
            raise LocalVehicleStartupError(f"本机车端已退出（代码 {returncode}）。日志 {self.log_path}")
        if self._ports is not None:
            return self._ports

        try:
            with self.log_path.open("rb") as log:
                readiness = _READY_LINE.search(log.read(4096))
        except OSError as error:
            self.stop()
            raise LocalVehicleStartupError(f"无法读取本机车端启动日志。日志 {self.log_path}") from error
        if readiness is not None:
            control_port, telemetry_source_port = map(int, readiness.groups())
            if 1 <= control_port <= 65535 and 1 <= telemetry_source_port <= 65535:
                self._ports = LocalVehiclePorts(control_port, telemetry_source_port)
                return self._ports
        if time.monotonic() >= self._start_deadline:
            self.stop()
            raise LocalVehicleStartupError(f"本机车端启动超时。日志 {self.log_path}")
        return None

    def stop(self) -> None:
        """Signal EOF to this child and wait briefly for socket cleanup."""
        process = self._process
        if process.stdin is not None:
            with suppress(BrokenPipeError):
                process.stdin.close()
        if process.poll() is not None:
            return
        try:
            process.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_STOP_TIMEOUT_S)
