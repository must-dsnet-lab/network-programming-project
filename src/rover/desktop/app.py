"""Desktop console for the teaching demo, secure control, and passive observation."""

import argparse
from functools import partial
from math import radians
from pathlib import Path
import socket
import sys
from time import monotonic

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from rover.core.control_target import ControlTarget
from rover.desktop.acceptance import AcceptanceRecording, ControlDatagrams, TELEMETRY_DELAY_S, TelemetryDelivery
from rover.desktop.appearance import CONSOLE_STYLE, desktop_font
from rover.desktop.control_worker import SecureControlWorker
from rover.desktop.desktop_map import VehicleMap
from rover.desktop.secure_connection import connect_management
from rover.transport.udp_transport import try_receive_telemetry


class DesktopConsole(QWidget):
    def __init__(
        self, *, listen_address=("127.0.0.1", 10000), control_address=("127.0.0.1", 9999),
        secure_worker: SecureControlWorker | None = None, management_connect=None,
        management_address=("127.0.0.1", 19177), observe=False, client_id="desktop-a",
    ):
        super().__init__()
        self.setObjectName("console")
        self.setWindowTitle("虚拟小车控制台")
        self.resize(1140, 730)
        self.setFont(desktop_font(13))
        self.setStyleSheet(CONSOLE_STYLE)

        self.listen_address = listen_address
        self.control_address = control_address
        self.observe = observe
        self.secure_worker = secure_worker
        self.management_connect = management_connect
        self.management_address = management_address
        self.secure_mode = secure_worker is not None or management_connect is not None
        self._secure_close_pending = False
        self._disconnect_pending = False
        self._worker_state = None
        self.control_datagrams = ControlDatagrams()
        self.current_target: ControlTarget | None = None
        self.last_received_s = None
        self.last_raw_fault = None
        self.telemetry_timeout_s = 1.0
        self.telemetry_condition = "waiting"
        self.delivery = TelemetryDelivery()
        self.recording = AcceptanceRecording()

        self._build_ui(client_id)
        self.control_sock = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.setblocking(False)
            self.sock.bind(listen_address)
            if not observe and not self.secure_mode:
                self.control_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.control_sock.setblocking(False)
                self.control_sock.bind(("0.0.0.0", 0))
        except OSError:
            self.sock.close()
            if self.control_sock is not None:
                self.control_sock.close()
            raise

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_telemetry)
        self.timer.start(20)
        self.control_timer = QTimer(self)
        self.control_timer.setInterval(100)
        self.control_timer.timeout.connect(self.send_control)
        if self.secure_mode:
            self.secure_status_timer = QTimer(self)
            self.secure_status_timer.timeout.connect(self.poll_secure_status)
            if self.secure_worker is not None:
                self.secure_worker.start()
            self.poll_secure_status()
            self.secure_status_timer.start(50)
        if self.management_connect is not None:
            self.credential_input.setFocus()

    def _build_ui(self, client_id):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 11)
        outer.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        map_column = QVBoxLayout()
        map_column.setSpacing(10)
        map_bar = QHBoxLayout()
        map_bar.setSpacing(6)
        map_bar.addWidget(self.caption("车辆地图", size=21, bold=True))
        map_bar.addStretch()
        frame = QFrame()
        frame.setObjectName("mapFrame")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(1, 1, 1, 1)
        self.map = VehicleMap()
        frame_layout.addWidget(self.map)
        for title, hint, handler in (
            ("−", "缩小地图", lambda: self.map.zoom_at(1 / 1.2, self.map.viewport_center())),
            ("+", "放大地图", lambda: self.map.zoom_at(1.2, self.map.viewport_center())),
            ("定位车辆", "将车辆移到地图中心", self.map.center_vehicle),
            ("清除轨迹", "从当前位置重新绘制轨迹", self.map.clear_trajectory),
        ):
            button = self.add_button(map_bar, title, handler)
            button.setToolTip(hint)
            button.setAccessibleName(hint)
            if title in ("−", "+"):
                button.setFixedWidth(32)
        map_column.addLayout(map_bar)
        map_column.addWidget(frame, 1)
        body.addLayout(map_column, 1)

        panel = QWidget()
        panel.setObjectName("sidebar")
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        panel.setFixedWidth(358)
        sidebar = QVBoxLayout(panel)
        sidebar.setContentsMargins(15, 13, 15, 13)
        sidebar.setSpacing(9)
        body.addWidget(panel)
        self._build_telemetry(sidebar)

        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        sidebar.addWidget(tabs, 1)
        if not self.observe:
            self._build_control_tab(tabs, client_id)
        self._build_acceptance_tab(tabs)

        footer = QHBoxLayout()
        footer.setSpacing(14)
        footer.addWidget(self.caption("滚轮缩放，拖拽平移", muted=True))
        footer.addStretch()
        self.source_label = self.caption("来源  —", muted=True)
        footer.addWidget(self.source_label)
        mode = "只读观察" if self.observe else ("TLS 安全控制" if self.secure_mode else "教学 UDP")
        footer.addWidget(self.caption(f"{mode}  /  遥测 {self.listen_address[0]}:{self.listen_address[1]}", muted=True))
        outer.addLayout(footer)

    def _build_telemetry(self, layout):
        title = QHBoxLayout()
        title.addWidget(self.caption("车辆遥测", size=16, bold=True))
        title.addStretch()
        self.status = self.caption("等待遥测", muted=True)
        self.status.setProperty("tone", "quiet")
        title.addWidget(self.status)
        layout.addLayout(title)

        reading = QHBoxLayout()
        reading.setSpacing(6)
        self.speed_value = self.caption("—", size=34, numeric=True)
        reading.addWidget(self.speed_value)
        reading.addWidget(self.caption("m/s", muted=True), 0, Qt.AlignmentFlag.AlignVCenter)
        reading.addStretch()
        self.protection_value = self.caption("保护待确认", muted=True)
        reading.addWidget(self.protection_value)
        layout.addLayout(reading)

        pose = QHBoxLayout()
        self.pose_values = []
        for label in ("X  /  m", "Y  /  m", "朝向  /  rad"):
            metric = QVBoxLayout()
            metric.setSpacing(1)
            metric.addWidget(self.caption(label, muted=True))
            value = self.caption("—", size=15, numeric=True)
            metric.addWidget(value)
            self.pose_values.append(value)
            pose.addLayout(metric, 1)
        layout.addLayout(pose)

    @staticmethod
    def caption(text, *, muted=False, size=None, bold=False, numeric=False):
        widget = QLabel(text)
        if muted:
            widget.setProperty("role", "muted")
        if size is not None:
            widget.setFont(desktop_font(size, bold=bold, numeric=numeric))
        return widget

    @staticmethod
    def set_tone(label, tone):
        if label.property("tone") != tone:
            label.setProperty("tone", tone)
            label.style().unpolish(label)
            label.style().polish(label)

    def section_title(self, parent, title, address):
        row = QHBoxLayout()
        row.addWidget(self.caption(title, size=14, bold=True))
        row.addStretch()
        row.addWidget(self.caption(f"{address[0]}:{address[1]}", muted=True, size=11, numeric=True))
        parent.addLayout(row)

    @staticmethod
    def add_button(layout, title, handler, *, role="secondary"):
        button = QPushButton(title)
        button.setProperty("role", role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(handler)
        layout.addWidget(button)
        return button

    def _build_control_tab(self, tabs, client_id):
        controls = QWidget()
        column = QVBoxLayout(controls)
        column.setContentsMargins(0, 11, 0, 0)
        column.setSpacing(7)
        tabs.addTab(controls, "驾驶")
        if self.management_connect is not None:
            self.section_title(column, "管理连接", self.management_address)
            form = QFormLayout()
            self.client_id_input = QLineEdit(client_id)
            self.credential_input = QLineEdit()
            self.credential_input.setEchoMode(QLineEdit.EchoMode.Password)
            self.credential_input.returnPressed.connect(self.connect_session)
            form.addRow("客户端 ID", self.client_id_input)
            form.addRow("凭据", self.credential_input)
            column.addLayout(form)

            row = QHBoxLayout()
            self.connect_button = self.add_button(row, "连接并认证", self.connect_session, role="primary")
            self.disconnect_button = self.add_button(row, "断开 TLS", self.disconnect_session)
            column.addLayout(row)
            self.session_status = self.caption("未连接", muted=True)
            self.session_status.setWordWrap(True)
            column.addWidget(self.session_status)
            divider = QFrame()
            divider.setObjectName("divider")
            column.addWidget(divider)

        self.section_title(column, "控制目标", self.control_address)
        form = QFormLayout()
        self.speed_input = QDoubleSpinBox()
        self.speed_input.setRange(-2.0, 2.0)
        self.speed_input.setDecimals(2)
        self.speed_input.setSingleStep(0.1)
        self.speed_input.setSuffix(" m/s")
        self.heading_input = QDoubleSpinBox()
        self.heading_input.setRange(-180.0, 180.0)
        self.heading_input.setDecimals(1)
        self.heading_input.setSingleStep(5.0)
        self.heading_input.setSuffix(" °")
        form.addRow("目标速度", self.speed_input)
        form.addRow("目标朝向", self.heading_input)
        column.addLayout(form)

        row = QHBoxLayout()
        self.start_button = self.add_button(row, "开始／应用", self.start_control, role="primary")
        self.stop_button = self.add_button(row, "正常停车", self.stop_motion)
        self.stop_sending_button = self.add_button(row, "停止发包", self.stop_control)
        column.addLayout(row)
        if self.secure_mode:
            row = QHBoxLayout()
            self.emergency_stop_button = self.add_button(row, "急停", self.request_emergency_stop, role="danger")
            self.reset_button = self.add_button(row, "故障复位", self.request_reset)
            column.addLayout(row)
        self.control_status = self.caption("未发送控制", muted=True)
        self.control_status.setWordWrap(True)
        column.addWidget(self.control_status)
        column.addStretch()

    def _build_acceptance_tab(self, tabs):
        acceptance = QWidget()
        layout = QVBoxLayout(acceptance)
        layout.setContentsMargins(0, 11, 0, 0)
        layout.setSpacing(8)
        tabs.addTab(acceptance, "故障与验收")
        self.fault_mode = QComboBox()
        self.fault_mode.addItem("正常交付", "live")
        self.fault_mode.addItem(f"延迟遥测 {TELEMETRY_DELAY_S:g} 秒", "delay")
        self.fault_mode.addItem("暂停遥测显示", "pause")
        self.fault_mode.currentIndexChanged.connect(
            lambda _: self.set_telemetry_fault(self.fault_mode.currentData())
        )
        layout.addWidget(self.caption("遥测显示", size=14, bold=True))
        layout.addWidget(self.fault_mode)
        if not self.observe:
            loss_toggle = QCheckBox("控制 UDP 每 3 次丢弃 1 次")
            loss_toggle.toggled.connect(self.set_control_loss)
            layout.addWidget(loss_toggle)
            note = self.caption("「驾驶 → 停止发包」可验证车端失联保护。", muted=True)
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addSpacing(4)
        row = QHBoxLayout()
        self.record_button = self.add_button(row, "开始记录", self.toggle_recording, role="primary")
        self.export_button = self.add_button(row, "导出 CSV", self.export_recording)
        self.export_button.setEnabled(False)
        layout.addLayout(row)
        self.record_status = self.caption("尚未开始记录", muted=True)
        events_header = QHBoxLayout()
        events_header.addWidget(self.caption("事件记录", size=14, bold=True))
        events_header.addStretch()
        events_header.addWidget(self.record_status)
        layout.addLayout(events_header)
        self.events_view = QPlainTextEdit()
        self.events_view.setReadOnly(True)
        self.events_view.document().setMaximumBlockCount(80)
        layout.addWidget(self.events_view, 1)

    def connect_session(self):
        if self.secure_worker is not None and not self.secure_worker.wait_closed(0):
            return
        client_id = self.client_id_input.text().strip()
        credential = self.credential_input.text()
        if not client_id or not credential:
            self.session_status.setText("请填写客户端 ID 和凭据")
            return
        connect = partial(self.management_connect, client_id=client_id, credential=credential)
        self.secure_worker = SecureControlWorker(
            connect, self.control_address, send_command=self.control_datagrams.send_windowed,
        )
        self._worker_state = None
        self.secure_worker.start()
        self.poll_secure_status()

    def disconnect_session(self):
        if self.secure_worker is None:
            return
        self._disconnect_pending = True
        self.current_target = None
        self.secure_worker.close()
        self.poll_secure_status()

    def request_emergency_stop(self):
        if self.secure_worker is not None and self.secure_worker.emergency_stop():
            self.current_target = None
            self.record("emergency_request", "请求车端急停")
        self.poll_secure_status()

    def request_reset(self):
        if self.secure_worker is not None and self.secure_worker.reset_control():
            self.current_target = None
            self.record("reset_request", "申请车端故障复位")
        self.poll_secure_status()

    def poll_secure_status(self):
        worker = self.secure_worker
        if self._secure_close_pending:
            if worker.wait_closed(0):
                self.close()
            return
        if self._disconnect_pending and worker.wait_closed(0):
            self.secure_worker = worker = None
            self._disconnect_pending = False
            self.record("management_closed", "TLS 管理连接已关闭")

        snapshot = worker.status() if worker is not None else None
        state = snapshot.state if snapshot is not None else "closed"
        if state in ("stopping", "latched", "resetting", "blocked", "failed", "closing", "closed"):
            self.current_target = None
        can_apply = state in ("ready", "sending", "stopped")
        has_target = can_apply and self.current_target is not None
        self.start_button.setEnabled(can_apply)
        self.stop_button.setEnabled(has_target)
        self.stop_sending_button.setEnabled(has_target)
        self.emergency_stop_button.setEnabled(state in ("ready", "sending", "stopped", "latched", "blocked"))
        self.reset_button.setEnabled(
            state in ("ready", "stopped", "latched", "blocked") and self.current_target is None
        )
        if self.management_connect is not None:
            can_connect = state in ("failed", "closed")
            self.connect_button.setEnabled(can_connect)
            self.client_id_input.setEnabled(can_connect)
            self.credential_input.setEnabled(can_connect)
            self.disconnect_button.setEnabled(state not in ("failed", "closing", "closed"))
            session_text = {
                "connecting": "正在验证证书和客户端身份…",
                "failed": "管理连接失败，可重新连接",
                "closing": "正在关闭 TLS 管理连接…",
                "closed": "未连接",
            }.get(state, "TLS 已认证")
            self.session_status.setText(session_text)

        labels = {
            "new": "安全连接尚未启动",
            "connecting": "正在连接和认证",
            "ready": "安全连接就绪",
            "sending": "正在周期发送安全控制指令",
            "stopped": "已停止发包",
            "failed": "安全控制失败",
            "closing": "正在关闭安全连接",
            "closed": "未发送控制",
            "stopping": "急停请求已提交，等待车端确认",
            "latched": "车端已确认急停并锁存",
            "resetting": "正在复位",
            "blocked": "安全控制已暂停",
        }
        text = labels[state]
        if has_target and state in ("ready", "stopped"):
            text = "控制目标已提交，等待后台发送"
        if snapshot and snapshot.detail:
            text += f"：{snapshot.detail}"
        self.control_status.setText(text)

        if snapshot != self._worker_state and snapshot is not None:
            event = {
                "ready": "management_connected",
                "latched": "emergency_accepted",
                "failed": "management_error",
                "blocked": "control_blocked",
            }.get(state)
            if snapshot.detail.startswith("复位成功"):
                event = "reset_accepted"
            elif snapshot.detail == "车端拒绝复位":
                event = "reset_denied"
            elif snapshot.detail == "车端拒绝急停":
                event = "emergency_denied"
            if event:
                self.record(event, text)
        self._worker_state = snapshot
        self.drain_control_events()

    def start_control(self):
        if self.observe:
            return
        target = ControlTarget(self.speed_input.value(), radians(self.heading_input.value()))
        if self.secure_mode:
            if self.secure_worker is not None and self.secure_worker.apply_target(target):
                self.current_target = target
                self.record("target_applied", self.target_text())
            self.poll_secure_status()
            return
        self.current_target = target
        self.record("target_applied", self.target_text())
        self.control_timer.start()
        self.send_control()

    def target_text(self):
        return (f"速度 {self.current_target.speed_mps:.2f} m/s，"
                f"朝向 {self.current_target.heading_rad:.3f} rad")

    def send_control(self):
        if not self.control_timer.isActive() or self.current_target is None:
            return
        try:
            sent = self.control_datagrams.send_target(
                self.control_sock, self.current_target, self.control_address,
            )
        except (OSError, ValueError) as error:
            self.clear_motion()
            self.control_status.setText(f"发送失败：{error}")
            self.record("control_send_error", f"控制发送失败 · {error}")
            return
        detail = self.target_text()
        status = f"周期发送中 · {sent} 字节／包" if sent else "实验丢包 · 本次未发送"
        self.control_status.setText(f"{status}\n{detail}")
        self.drain_control_events()

    def stop_motion(self):
        if self.current_target is None:
            return
        target = ControlTarget(0.0, self.current_target.heading_rad)
        if self.secure_mode:
            if self.secure_worker.apply_target(target):
                self.current_target = target
                self.speed_input.setValue(0.0)
                self.record("normal_stop", "发送零速目标，保留周期发送")
            self.poll_secure_status()
            return
        if self.control_timer.isActive():
            self.current_target = target
            self.speed_input.setValue(0.0)
            self.record("normal_stop", "发送零速目标，保留周期发送")
            self.send_control()

    def clear_motion(self):
        self.control_timer.stop()
        self.current_target = None

    def stop_control(self):
        if self.current_target is None:
            return
        self.clear_motion()
        self.record("control_paused", "停止发包，观察车端保护")
        if self.secure_worker is not None:
            self.secure_worker.stop_sending()
            self.poll_secure_status()
        else:
            self.control_status.setText("已停止发包，继续接收遥测")

    def poll_telemetry(self):
        self.drain_control_events()
        receive_error = None
        for _ in range(8):
            try:
                received = try_receive_telemetry(self.sock)
            except ValueError:
                continue
            except OSError as error:
                self.timer.stop()
                self.sock.close()
                receive_error = error
                break
            if received is None:
                break
            snapshot, peer = received
            now = monotonic()
            self.recording.append(now, "telemetry", snapshot=snapshot, peer=peer)
            if snapshot.fault_latched != self.last_raw_fault:
                if snapshot.fault_latched:
                    self.record("vehicle_fault_latched", "车端遥测上报故障已锁存")
                elif self.last_raw_fault is not None:
                    self.record("vehicle_fault_cleared", "车端遥测上报故障已解除")
                self.last_raw_fault = snapshot.fault_latched
            self.delivery.receive(now, snapshot, peer)

        for received_s, snapshot, peer in self.delivery.deliver(monotonic()):
            self.last_received_s = received_s
            self.map.set_state(snapshot.state)
            self.display_telemetry(snapshot, peer)
        if receive_error:
            self.status.setText(f"遥测接收错误：{receive_error}")
            self.status.setWordWrap(True)
            self.set_tone(self.status, "alert")
            self.map.set_telemetry_fresh(False)
            self.record("telemetry_receive_error", f"遥测接收错误 · {receive_error}")
        else:
            self.update_telemetry_status()

    def display_telemetry(self, snapshot, peer):
        state = snapshot.state
        self.speed_value.setText(f"{state.speed_mps:.2f}")
        for label, value in zip(self.pose_values, (state.x_m, state.y_m, state.heading_rad)):
            label.setText(f"{value:.3f}")
        self.protection_value.setText("保护已锁存" if snapshot.fault_latched else "保护未锁存")
        self.set_tone(self.protection_value, "alert" if snapshot.fault_latched else "quiet")
        self.source_label.setText(f"来源  {peer[0]}:{peer[1]}")

    def update_telemetry_status(self):
        if self.last_received_s is None:
            condition, text = "waiting", "等待遥测"
        else:
            age = monotonic() - self.last_received_s
            if age >= self.telemetry_timeout_s:
                condition, text = "stale", f"遥测过期 · {age:.1f} s"
            else:
                condition, text = "fresh", "遥测可用"
        self.status.setText(text)
        self.set_tone(self.status, {"waiting": "quiet", "stale": "alert", "fresh": "live"}[condition])
        self.set_tone(self.speed_value, "alert" if condition == "stale" else None)
        self.map.set_telemetry_fresh(condition == "fresh")
        if condition != self.telemetry_condition:
            if condition == "stale":
                self.record("telemetry_stale", "遥测显示过期，保留最后位置")
            elif self.telemetry_condition == "stale":
                self.record("telemetry_recovered", "遥测显示已恢复")
            self.telemetry_condition = condition

    def set_telemetry_fault(self, mode):
        self.delivery.set_mode(mode)
        name = {"live": "恢复正常交付", "delay": "启用遥测显示延迟", "pause": "暂停遥测显示"}[mode]
        self.record(f"telemetry_{mode}", name)

    def set_control_loss(self, enabled):
        self.control_datagrams.set_loss(enabled)
        self.record("control_loss_on" if enabled else "control_loss_off",
                    "开启控制丢包，每 3 次丢弃 1 次" if enabled else "恢复控制正常发包")

    def drain_control_events(self):
        for when, event, detail in self.control_datagrams.drain():
            self.recording.append(when, event, detail)

    def record(self, event, detail):
        self.events_view.appendPlainText(detail)
        self.recording.append(monotonic(), event, detail)

    def begin_recording(self):
        self.recording.begin(monotonic())
        self.control_datagrams.set_capture(True)
        self.record_button.setText("结束记录")
        self.record_status.setText(f"正在记录第 {self.recording.trial} 轮")
        self.export_button.setEnabled(True)
        mode = "只读观察" if self.observe else ("安全控制" if self.secure_mode else "教学控制")
        delivery = {"live": "实时", "delay": "延迟", "pause": "暂停"}[self.delivery.mode]
        loss = "开启" if self.control_datagrams.enabled else "关闭"
        self.record("trial_start", f"第 {self.recording.trial} 轮开始 · {mode}，"
                    f"遥测{delivery}，控制丢包{loss}")

    def toggle_recording(self):
        if not self.recording.active:
            self.begin_recording()
            return
        self.control_datagrams.set_capture(False)
        self.drain_control_events()
        self.record("trial_end", f"结束第 {self.recording.trial} 轮")
        self.recording.active = False
        self.record_button.setText("开始下一轮")
        self.record_status.setText(f"已记录 {self.recording.trial} 轮，可导出 CSV")

    def write_recording(self, path):
        self.drain_control_events()
        self.recording.write(path)

    def export_recording(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出验收记录", "rover-acceptance.csv", "CSV (*.csv)")
        if path:
            try:
                self.write_recording(path)
            except OSError as error:
                QMessageBox.critical(self, "导出失败", str(error))

    def closeEvent(self, event):
        if self.secure_worker is not None:
            if not self._secure_close_pending:
                self._secure_close_pending = True
                self.secure_worker.close()
                if self.management_connect is not None:
                    self.connect_button.setEnabled(False)
                    self.disconnect_button.setEnabled(False)
                self.start_button.setEnabled(False)
                self.stop_button.setEnabled(False)
                self.stop_sending_button.setEnabled(False)
                self.emergency_stop_button.setEnabled(False)
                self.reset_button.setEnabled(False)
                self.control_status.setText("正在关闭安全连接")
            if not self.secure_worker.wait_closed(0):
                event.ignore()
                return
        self.clear_motion()
        self.timer.stop()
        if self.secure_mode:
            self.secure_status_timer.stop()
        if self.control_sock is not None:
            self.control_sock.close()
        self.sock.close()
        event.accept()


def port(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        number = 0
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Open the rover desktop console.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--secure", action="store_true", help="use TLS management and authenticated UDP")
    modes.add_argument("--observe", action="store_true", help="listen for telemetry only")
    parser.add_argument("--ca-file", type=Path)
    parser.add_argument("--client-id")
    parser.add_argument("--server-name")
    parser.add_argument("--control-host", default="127.0.0.1")
    parser.add_argument("--control-port", type=port, default=9999)
    parser.add_argument("--management-port", type=port, default=19177)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--telemetry-port", type=port, default=10000)
    args = parser.parse_args(argv)
    if not args.secure and any(value is not None for value in (args.ca_file, args.client_id, args.server_name)):
        parser.error("--ca-file, --client-id and --server-name require --secure")

    app = QApplication([sys.argv[0]])
    app.setStyle("Fusion")
    management_address = (args.control_host, args.management_port)
    connect = None
    if args.secure:
        server_name = args.server_name or ("localhost" if args.control_host == "127.0.0.1" else args.control_host)
        connect = partial(connect_management, management_address,
                          ca_file=args.ca_file or Path("local/certs/ca.pem"), server_hostname=server_name)
    try:
        window = DesktopConsole(
            listen_address=(args.listen_host, args.telemetry_port),
            control_address=(args.control_host, args.control_port),
            observe=args.observe, management_connect=connect, management_address=management_address,
            client_id=args.client_id or "desktop-a",
        )
    except OSError as error:
        QMessageBox.critical(None, "监听失败", f"请检查监听地址和端口占用。\n{error}")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
