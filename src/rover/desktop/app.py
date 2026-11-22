"""初始文件名：demo_desktop_console.py"""

import socket
import sys
from time import monotonic
from math import radians

from rover.core.control_target import ControlTarget
from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QPushButton,
)
import argparse
from functools import partial
from getpass import getpass
from pathlib import Path

from rover.desktop.appearance import CONSOLE_STYLE, STATUS_COLORS, desktop_font
from rover.desktop.control_worker import SecureControlWorker
from rover.desktop.secure_connection import connect_management
from rover.desktop.desktop_map import VehicleMap
from rover.transport.udp_transport import send_target, try_receive_telemetry


EVENT_LOG_LIMIT = 100


class DesktopConsole(QWidget):
    def __init__(
        self,
        *,
        secure_worker: SecureControlWorker | None = None,
        telemetry_address: tuple[str, int] = ("127.0.0.1", 10000),
        operator_id: str | None = None,
    ):
        super().__init__()
        self.secure_worker = secure_worker
        self.operator_id = operator_id
        self._secure_close_pending = False
        self._last_secure_state: str | None = None
        self._last_telemetry_fault_latched = False
        self._telemetry_control_suspended = False
        self.setObjectName("console")
        self.setWindowTitle("虚拟小车控制台")
        self.resize(1140, 740)
        self.setFont(desktop_font(13))
        self.setStyleSheet(CONSOLE_STYLE)

        self.listen_address = telemetry_address
        self.last_received_s = None
        self.telemetry_timeout_s = 1.0

        self.control_address = ("127.0.0.1", 9999)
        self.current_target: ControlTarget | None = None

        self._build_ui()

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) #接收
        self.control_sock = None #发送

        try:
            self.sock.setblocking(False)
            self.sock.bind(self.listen_address)

            if self.secure_worker is None:
                self.control_sock = socket.socket(
                    socket.AF_INET,
                    socket.SOCK_DGRAM,
                )
                self.control_sock.setblocking(False)
                self.control_sock.bind(("127.0.0.1", 0))
        except OSError:
            self.sock.close()
            if self.control_sock is not None:
                self.control_sock.close()
            raise

        ####################### 两个定时器 #######################
        # self.timer：接收定时器。每 20 毫秒触发一次，跑去 self.sock 里看看有没有新数据，有就更新地图。
        self.timer = QTimer(self)  # Qt的定时器
        self.timer.timeout.connect(self.poll_telemetry)  # 定时调用poll_telemetry函数
        self.timer.start(20)  # 设置20ms(50Hz)的间隔

        # self.control_timer：发送定时器。每 100 毫秒触发一次，跑去 self.control_sock 里，把最新的控制指令发出去。
        self.control_timer = QTimer(self)
        self.control_timer.setInterval(100)
        self.control_timer.timeout.connect(self.send_control) #timeout就是setInterval的时间，建立每100ms send_control 的连接
        self.start_button.clicked.connect(self.start_control) #通过人点击触发
        self.stop_button.clicked.connect(self.stop_motion)
        self.stop_sending_button.clicked.connect(self.stop_control)
        if self.secure_worker is not None:
            self.secure_status_timer = QTimer(self)
            self.secure_status_timer.timeout.connect(self.poll_secure_status)
            self.secure_worker.start()
            self.poll_secure_status()
            self.secure_status_timer.start(50)
        self.speed_input.setFocus()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)

        canvas = QVBoxLayout()
        canvas.setSpacing(8)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self._label("车辆地图", size=21, bold=True))
        toolbar.addStretch()
        self.map = VehicleMap()
        for title, hint, action in (
            ("−", "缩小地图", self.map.zoom_out),
            ("+", "放大地图", self.map.zoom_in),
            ("定位车辆", "将最后上报的位置移到地图中心", self.map.center_vehicle),
            ("清除轨迹", "从车辆的当前位置重新绘制轨迹", self.map.clear_trajectory),
        ):
            button = self._button(title, hint)
            button.clicked.connect(action)
            if title in ("−", "+"):
                button.setFixedWidth(33)
                button.setAccessibleName(hint)
            toolbar.addWidget(button)
        canvas.addLayout(toolbar)

        frame = QFrame()
        frame.setObjectName("mapFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        frame_layout.addWidget(self.map)
        canvas.addWidget(frame, 1)

        map_info = QHBoxLayout()
        map_info.addWidget(self._label("滚轮缩放，拖拽平移", muted=True))
        map_info.addStretch()
        self.source_label = self._label("遥测来源：—", muted=True)
        map_info.addWidget(self.source_label)
        canvas.addLayout(map_info)
        canvas.addWidget(self._label("事件日志", size=14, bold=True))
        self.event_log = QPlainTextEdit()
        self.event_log.setAccessibleName("事件日志")
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(EVENT_LOG_LIMIT)
        self.event_log.setFixedHeight(98)
        canvas.addWidget(self.event_log)
        layout.addLayout(canvas, 1)

        panel = QFrame()
        panel.setObjectName("sidebar")
        panel.setFixedWidth(340)
        side = QVBoxLayout(panel)
        side.setContentsMargins(16, 15, 16, 15)
        side.setSpacing(9)
        self._build_readouts(side)
        side.addWidget(self._divider())
        self._build_controls(side)
        side.addStretch()
        layout.addWidget(panel)

    def _build_readouts(self, side):
        title = QHBoxLayout()
        title.addWidget(self._label("车辆遥测", size=17, bold=True))
        title.addStretch()
        self.status = self._label("等待遥测", muted=True)
        self.status.setWordWrap(True)
        title.addWidget(self.status)
        side.addLayout(title)

        self.speed_caption = self._label("实测速度", muted=True)
        side.addWidget(self.speed_caption)
        speed = QHBoxLayout()
        self.speed_value = self._label("—", size=34, bold=True, numeric=True)
        self.speed_value.setAccessibleName("实测速度，米每秒")
        speed.addWidget(self.speed_value)
        speed.addWidget(self._label("m/s", muted=True))
        speed.addStretch()
        protection = QVBoxLayout()
        protection.setSpacing(2)
        self.protection_caption = self._label("车端保护", muted=True)
        protection.addWidget(self.protection_caption)
        self.protection_value = self._label("—", size=14, bold=True)
        self.protection_value.setAccessibleName("车端保护")
        protection.addWidget(self.protection_value)
        speed.addLayout(protection)
        side.addLayout(speed)

        pose = QHBoxLayout()
        self.pose_values = []
        for name in ("X / m", "Y / m", "朝向 / rad"):
            metric = QVBoxLayout()
            metric.setSpacing(2)
            metric.addWidget(self._label(name, muted=True))
            value = self._label("—", size=15, numeric=True)
            value.setAccessibleName(name)
            metric.addWidget(value)
            self.pose_values.append(value)
            pose.addLayout(metric, 1)
        side.addLayout(pose)

        side.addWidget(self._divider())
        connection = QHBoxLayout()
        connection.addWidget(self._label("管理连接" if self.secure_worker else "运行模式", size=14, bold=True))
        connection.addStretch()
        self.connection_value = self._label("待认证" if self.secure_worker else "教学 UDP", muted=True)
        connection.addWidget(self.connection_value)
        side.addLayout(connection)
        initial_operator = "待认证" if self.secure_worker else "本地演示"
        self.operator_label = self._label(f"当前操作员：{initial_operator}", muted=True)
        self.operator_label.setWordWrap(True)
        side.addWidget(self.operator_label)
        host, port = self.listen_address
        side.addWidget(self._label(f"遥测监听  {host}:{port}", muted=True))

    def _build_controls(self, side):
        side.addWidget(self._label("控制目标", size=17, bold=True))
        form = QFormLayout()
        form.setVerticalSpacing(7)
        self.speed_input = QDoubleSpinBox()
        self.speed_input.setRange(-2.0, 2.0)
        self.speed_input.setDecimals(2)
        self.speed_input.setSingleStep(0.1)
        self.speed_input.setSuffix(" m/s")
        self.speed_input.setMinimumHeight(32)
        self.heading_input = QDoubleSpinBox()
        self.heading_input.setRange(-180.0, 180.0)
        self.heading_input.setDecimals(1)
        self.heading_input.setSingleStep(5.0)
        self.heading_input.setSuffix(" °")
        self.heading_input.setMinimumHeight(32)
        form.addRow("目标速度", self.speed_input)
        form.addRow("目标朝向", self.heading_input)
        side.addLayout(form)

        actions = QGridLayout()
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        self.start_button = self._button("开始／应用", "应用当前输入的目标速度和朝向", role="primary")
        self.stop_button = self._button("正常停车", "将已应用的速度设为零，继续周期发包")
        self.stop_sending_button = self._button("停止发包", "结束周期发包，继续接收车辆遥测")
        actions.addWidget(self.start_button, 0, 0)
        actions.addWidget(self.stop_button, 0, 1)
        actions.addWidget(self.stop_sending_button, 1, 0)
        if self.secure_worker is not None:
            self.reset_button = self._button("显式复位", "申请解除车端锁存；复位后需重新应用目标")
            self.emergency_stop_button = self._button("急停", "通过安全连接请求车端立即停车并锁存", role="danger")
            self.reset_button.clicked.connect(self.request_reset)
            self.emergency_stop_button.clicked.connect(self.request_emergency_stop)
            actions.addWidget(self.reset_button, 1, 1)
            actions.addWidget(self.emergency_stop_button, 2, 0, 1, 2)
        side.addLayout(actions)
        self.control_status = self._label("未发送控制")
        self.control_status.setObjectName("controlStatus")
        self.control_status.setWordWrap(True)
        side.addWidget(self.control_status)

    @staticmethod
    def _label(text, *, size=None, bold=False, numeric=False, muted=False):
        label = QLabel(text)
        label.setTextFormat(Qt.TextFormat.PlainText)
        if size is not None:
            label.setFont(desktop_font(size, bold=bold, numeric=numeric))
        if muted:
            label.setProperty("role", "muted")
        return label

    @staticmethod
    def _button(text, hint, *, role="secondary"):
        button = QPushButton(text)
        button.setProperty("role", role)
        button.setToolTip(hint)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    @staticmethod
    def _divider():
        line = QFrame()
        line.setObjectName("divider")
        return line

    @staticmethod
    def _set_tone(label, tone):
        style = f"color: {STATUS_COLORS[tone]};" if tone else ""
        if label.styleSheet() != style:
            label.setStyleSheet(style)

    def start_control(self):
        target = ControlTarget(
            speed_mps=self.speed_input.value(),
            heading_rad=radians(self.heading_input.value()),
        )
        if self.secure_worker is not None:
            if self._secure_motion_blocked():
                return
            if self.secure_worker.apply_target(target):
                self.current_target = target
            self.poll_secure_status()
            return

        self.current_target = target
        self.control_timer.start()  # 进入100ms倒计时，每隔100ms就send_control
        self.send_control()  # 立即send_control -> 零延迟响应

    def send_control(self):
        if self.secure_worker is not None:
            return
        if not self.control_timer.isActive() or self.current_target is None:
            return
        try:
            sent = send_target(
                self.control_sock,
                self.current_target,
                self.control_address,
            )
        # 发送端可能因为断网等原因无法发送数据,但桌面不能因此系统崩溃，仍需保留接收车端遥测的能力
        except OSError as exc:
            self.control_timer.stop()
            self.control_status.setText(f"发送失败：{exc}")
            return

        self.control_status.setText(
            f"周期发送中：{sent} 字节／包\n"
            f"已应用速度 {self.current_target.speed_mps:.2f} m/s\n"
            f"已应用朝向 {self.current_target.heading_rad:.3f} rad"
        )

    # 保留 current_target 中已应用的朝向，所以输入框里尚未应用的不会生效。
    # 它立即发出零速目标，原定时器继续周期发送。
    def stop_motion(self):
        if self.secure_worker is not None:
            if self.current_target is None:
                return
            target = ControlTarget(0.0, self.current_target.heading_rad)
            if self.secure_worker.apply_target(target):
                self.current_target = target
                self.speed_input.setValue(0.0)
                self._append_event("正常停车：已提交零速目标，继续发包")
            self.poll_secure_status()
            return

        if not self.control_timer.isActive() or self.current_target is None:
            return

        self.current_target = ControlTarget(
            speed_mps=0.0,
            heading_rad=self.current_target.heading_rad,
        )
        self.speed_input.setValue(0.0)
        self.send_control()
        if self.control_timer.isActive():
            self._append_event("正常停车：已发送零速目标，继续发包")

    # 停止控制定时器，保留两个 Socket 和遥测轮询。
    # 之后即使触发发送回调，send_control() 的入口检查也会阻止发包。
    def stop_control(self):
        if self.secure_worker is not None:
            self.current_target = None
            self.secure_worker.stop_sending()
            self.poll_secure_status()
            return

        if not self.control_timer.isActive():
            return

        self.control_timer.stop()
        self.control_status.setText("已停止发包")
        self._append_event("已停止发包，继续接收遥测")

    def request_emergency_stop(self):
        if self.secure_worker.emergency_stop():
            self.current_target = None
        self.poll_secure_status()

    def request_reset(self):
        if self.secure_worker.reset_control():
            self.current_target = None
        self.poll_secure_status()

    def poll_secure_status(self):
        worker = self.secure_worker
        if worker is None:
            return
        if self._secure_close_pending:
            if worker.wait_closed(0):
                self.close()
            return

        snapshot = worker.status()
        state = snapshot.state
        if state in (
            "stopping", "latched", "resetting", "blocked",
            "failed", "closing", "closed",
        ):
            self.current_target = None

        can_apply = state in ("ready", "sending", "stopped") and not self._secure_motion_blocked()
        has_target = can_apply and self.current_target is not None
        self.start_button.setEnabled(can_apply)
        self.stop_button.setEnabled(has_target)
        self.stop_sending_button.setEnabled(has_target)
        self.emergency_stop_button.setEnabled(
            state in ("ready", "sending", "stopped", "latched", "blocked")
        )
        self.reset_button.setEnabled(
            state in ("ready", "stopped", "latched", "blocked")
            and self.current_target is None
        )
        labels = {
            "new": "安全连接尚未启动",
            "connecting": "正在连接和认证",
            "ready": "安全连接就绪",
            "sending": "正在周期发送安全控制指令",
            "stopped": "已停止发包",
            "failed": "安全控制失败",
            "closing": "正在关闭安全连接",
            "closed": "安全连接已关闭",
            "stopping": "急停请求已提交，等待车端确认",
            "latched": "车端已确认急停并锁存",
            "resetting": "正在复位",
            "blocked": "安全控制已暂停",
        }
        text = labels[state]
        if has_target and state in ("ready", "stopped"):
            text = "控制目标已提交，等待后台发送"
        elif has_target and state == "sending" and self.current_target.speed_mps == 0:
            text = "正在发送零速目标，按车端遥测确认停车"
        elif state in ("ready", "sending", "stopped") and self._telemetry_control_suspended:
            text = "遥测不可用，已停止发包"
        elif state in ("ready", "stopped") and self._last_telemetry_fault_latched:
            text = "车端故障已锁存，等待显式复位"
        if snapshot.detail:
            text += f"：{snapshot.detail}"
        self.control_status.setText(text)

        if state != self._last_secure_state:
            if state in ("new", "connecting"):
                operator = "待认证"
                connection, tone = "认证中", "quiet"
            elif state in ("failed", "closing", "closed"):
                operator = "—"
                connection = {"failed": "连接失败", "closing": "关闭中", "closed": "已关闭"}[state]
                tone = "alert" if state == "failed" else "quiet"
            else:
                operator = self.operator_id or "—"
                connection, tone = "TLS 已认证", "live"
            self.connection_value.setText(connection)
            self._set_tone(self.connection_value, tone)
            self.operator_label.setText(f"当前操作员：{operator}")
            self._append_event(labels[state])
            self._last_secure_state = state

    def _append_event(self, message: str) -> None:
        timestamp = QTime.currentTime().toString("HH:mm:ss")
        self.event_log.appendPlainText(f"[{timestamp}] {message}")

    def _set_telemetry_status(self, text: str, event_message: str | None = None, *, fresh=False) -> None:
        last_report = self.last_received_s is not None and not fresh
        self.map.set_telemetry_fresh(fresh)
        self._set_tone(self.speed_value, "alert" if last_report else None)
        self._set_tone(self.status, "live" if fresh else "alert" if last_report or event_message else "quiet")
        self.speed_caption.setText("最后上报速度" if last_report else "实测速度")
        self.protection_caption.setText("最后上报保护" if last_report else "车端保护")
        if self.status.text() != text:
            self.status.setText(text)
            self._append_event(event_message or text)

    def _secure_motion_blocked(self) -> bool:
        return self._telemetry_control_suspended or self._last_telemetry_fault_latched

    def _set_telemetry_control_suspended(self, suspended: bool) -> None:
        if (
            self.secure_worker is None
            or self._secure_close_pending
            or self._telemetry_control_suspended == suspended
        ):
            return
        self._telemetry_control_suspended = suspended
        if suspended:
            self.stop_control()
        else:
            self.poll_secure_status()

    def poll_telemetry(self):
        latest = None
        receive_error = None

        # 每次最多尝试8包，留出时间处理窗口事件
        for _ in range(8):
            try:
                received = try_receive_telemetry(self.sock)
            except ValueError:
                # 坏包不刷新接收时效
                continue
            except OSError as exc:
                self.timer.stop()
                self.sock.close()
                receive_error = exc
                break

            if received is None:
                break

            latest, peer = received
            self.last_received_s = monotonic()

            # 每个合法状态都交给地图，保留中间位置
            self.map.set_state(latest.state)
            if latest.fault_latched != self._last_telemetry_fault_latched:
                self._last_telemetry_fault_latched = latest.fault_latched
                message = "故障锁存" if latest.fault_latched else "故障锁存已解除"
                self._append_event(message)
                self.poll_secure_status()

        if latest is not None:
            state = latest.state
            self.speed_value.setText(f"{state.speed_mps:.2f}")
            for label, reading in zip(self.pose_values, (state.x_m, state.y_m, state.heading_rad)):
                label.setText(f"{reading:.3f}")
            self.protection_value.setText("已锁存" if latest.fault_latched else "未锁存")
            self._set_tone(self.protection_value, "alert" if latest.fault_latched else "quiet")
            self.source_label.setText(f"遥测来源：{peer[0]}:{peer[1]}")

        # 报错前若收到过合法数据，先同步读数，再显示错误
        if receive_error is not None:
            self._set_telemetry_status(f"接收错误：{receive_error}", "遥测接收错误")
            self._set_telemetry_control_suspended(True)
            return

        # 没有新包时，也继续检查显示状态是否过期
        if self.last_received_s is None:
            self._set_telemetry_status("等待遥测")
        elif monotonic() - self.last_received_s >= self.telemetry_timeout_s:
            self._set_telemetry_status("遥测过期", "遥测过期")
            self._set_telemetry_control_suspended(True)
        else:
            self._set_telemetry_status("遥测可用", fresh=True)
            self._set_telemetry_control_suspended(False)

    # 操作系统会捕捉到用户用鼠标点击窗口右上角的“❌”的动作，并把它告诉 Qt 框架。Qt 框架会自动去寻找 closeEvent 方法
    def closeEvent(self, event):
        if self.secure_worker is not None:
            if not self._secure_close_pending:
                self._secure_close_pending = True
                self.secure_worker.close()
                self.control_status.setText("正在关闭安全连接")
                self.connection_value.setText("关闭中")
                self._set_tone(self.connection_value, "quiet")
                self.operator_label.setText("当前操作员：—")
                self._append_event("正在关闭安全连接")
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.stop_sending_button.setEnabled(False)
                self.emergency_stop_button.setEnabled(False)
                self.reset_button.setEnabled(False)

            if not self.secure_worker.wait_closed(0):
                event.ignore()
                return
            self.secure_status_timer.stop()

        self.control_timer.stop()
        self.timer.stop()
        if self.control_sock is not None:
            self.control_sock.close()
        self.sock.close()
        event.accept()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="虚拟小车桌面控制台")
    parser.add_argument("--secure", action="store_true", help="启用安全控制")
    parser.add_argument("--ca-file", type=Path, help="公开 CA 证书")
    parser.add_argument("--server-name", help="车端证书上的名称")
    parser.add_argument("--client-id", help="桌面账号")
    parser.add_argument("--vehicle-host", type=str.strip, help="车端地址")
    parser.add_argument("--management-port", type=int, help="车端 TCP 管理端口")
    parser.add_argument("--control-port", type=int, help="车端 UDP 控制端口")
    parser.add_argument("--telemetry-bind-host", type=str.strip, help="桌面端监听遥测的本机地址")
    parser.add_argument("--telemetry-port", type=int, help="桌面端监听遥测的 UDP 端口")
    args = parser.parse_args(argv)

    tls_args = (args.ca_file, args.server_name, args.client_id)
    if not args.secure and any(value is not None for value in tls_args):
        parser.error("--ca-file、--server-name 和 --client-id 需要 --secure")

    lan_args = (
        args.vehicle_host,
        args.management_port,
        args.control_port,
        args.telemetry_bind_host,
        args.telemetry_port,
    )
    if not args.secure and any(value is not None for value in lan_args):
        parser.error("局域网参数需要 --secure")
    if args.vehicle_host == "" or args.telemetry_bind_host == "":
        parser.error("--vehicle-host 和 --telemetry-bind-host 的地址不能为空")
    for option, port in (
        ("--management-port", args.management_port),
        ("--control-port", args.control_port),
        ("--telemetry-port", args.telemetry_port),
    ):
        if port is not None and not 1 <= port <= 65535:
            parser.error(f"{option} 需要 1–65535 的端口")

    vehicle_host = args.vehicle_host or "127.0.0.1"
    telemetry_address = (
        args.telemetry_bind_host or "127.0.0.1",
        args.telemetry_port or 10000,
    )

    worker = None
    client_id = args.client_id or "desktop-a"
    if args.secure:
        credential = getpass("Credential (hidden): ")
        connect = partial(
            connect_management,
            (vehicle_host, args.management_port or 19177),
            ca_file=args.ca_file or Path("local/certs/ca.pem"),
            server_hostname=args.server_name or args.vehicle_host or "localhost",
            client_id=client_id,
            credential=credential,
        )
        worker = SecureControlWorker(connect, (vehicle_host, args.control_port or 9999))

    # QApplication 负责管理整个程序的全局资源
    # sys.argv：把程序参数传给 Qt
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    try:
        window = DesktopConsole(
            secure_worker=worker,
            telemetry_address=telemetry_address,
            operator_id=client_id if worker is not None else None,
        )  # 窗口的首次显示、鼠标拖拽拉伸：全靠 Qt 底层的操作系统事件
    except OSError as exc:
        QMessageBox.critical(
            None,
            "监听失败",
            "无法启动遥测监听，请检查旧接收器或另一个观察台" f"是否仍占用端口。\n{exc}",
        )
        return 1

    # show()是QWidget的成员函数，window是DesktopConsole的实例，
    # DesktopConsole继承了QWidget，所以可以调用父类的show()函数
    window.show()
    return app.exec()  # 启动主时间循环，阻塞直到窗口关闭


if __name__ == "__main__":
    sys.exit(main())
