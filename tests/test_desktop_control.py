import json
import unittest
from unittest.mock import Mock, patch
from math import pi
from io import StringIO
from PySide6.QtGui import QCloseEvent

from rover.core.control_target import ControlTarget
from rover.core.vehicle_model import VehicleState
from rover.desktop.control_worker import SecureControlWorker, WorkerStatus
from rover.protocol.telemetry_codec import TelemetrySnapshot
from PySide6.QtWidgets import QApplication

from rover.desktop.app import DesktopConsole, main

# 用列表记录发送报文，不实际占用端口。按钮、窗口和项目里的编码逻辑仍真实运行
class FakeSocket:
    """替代系统网络收发，记录程序实际准备发送的报文。"""

    def __init__(self, sent):
        self.sent = sent
        self.closed = False
        self.send_attempts = 0

    def setblocking(self, flag):
        pass # pass 代表什么也不做就跳过

    def bind(self, address):
        pass

    def recvfrom(self, size):
        if self.closed:
            raise OSError("socket is closed")
        raise BlockingIOError  # 模拟没有收到任何遥测数据，由 try_receive_telemetry 捕获并返回 None

    def sendto(self, packet, address):
        self.send_attempts += 1
        if self.closed:
            raise OSError("socket is closed")
        self.sent.append((packet, address)) #没有真的发数据，而是记录下来，方便测试断言
        return len(packet)

    def close(self):
        self.closed = True


class TestDesktopControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 涉及 PySide6 界面的测试，都必须有一个 QApplication 实例支撑
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sent = []
        socket_patch = patch(  # patch(...)：拦截器，把系统 Socket 替换成 MagicMock 的假类
            "rover.desktop.app.socket.socket",
            # 假对象（Mock）被调用时，如果配置了 side_effect，
            # 就不再返回默认的假数据，而是转去执行 side_effect 里面绑定的代码
            # *args, **kwargs：接收任意位置参数和关键字参数
            side_effect=lambda *args, **kwargs: FakeSocket(self.sent),
        )
        socket_patch.start()
        self.addCleanup(socket_patch.stop)#addCleanup(...)：即使测试失败，也会关闭窗口、恢复替换

        self.window = DesktopConsole()
        self.addCleanup(self.window.close)

    def control(self, name):
        # getattr 是反射函数：给对象（self.window），再给属性名字符串（name），
        # 动态求出属性(self.window.name)，找不到就返回 None
        value = getattr(self.window, name, None)
        self.assertIsNotNone(value, f"尚未添加控制组件：{name}")
        return value

    def apply_inputs(self, speed_mps, heading_deg):
        # control 函数拿到 “name” 后，getattr 找 self.window.name
        self.control("speed_input").setValue(speed_mps) #速度输入框
        self.control("heading_input").setValue(heading_deg) #朝向输入框
        self.control("start_button").click() #点击按钮

    def message(self, index):
        packet, address = self.sent[index]
        self.assertEqual(address, ("127.0.0.1", 9999))

        result = json.loads(packet.decode("utf-8"))
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["type"], "control_target")
        return result

    def test_window_starts_without_sending(self):
        self.assertEqual(self.window.operator_label.text(), "当前操作员：本地演示")
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.window.map.state)
        self.assertFalse(self.control("control_timer").isActive())

    # 第二项测试中，输入的是 90 度，报文里应该是 π/2 弧度。同时地图仍然没有车辆，
    # 因为测试没有提供任何遥测，发送目标不能冒充实际状态。
    def test_start_button_sends_target_with_heading_in_radians(self):
        self.apply_inputs(1.5, 90.0)

        self.assertEqual(len(self.sent), 1)
        message = self.message(0)
        self.assertAlmostEqual(message["speed_mps"], 1.5)
        self.assertAlmostEqual(message["heading_rad"], 1.5707963267948966)

        self.assertTrue(self.control("control_timer").isActive())
        self.assertEqual(self.control("control_timer").interval(), 100)
        self.assertIsNone(self.window.map.state)

    def test_input_edits_wait_for_explicit_apply(self):
        self.apply_inputs(1.5, 90.0)

        self.control("speed_input").setValue(-1.0)
        self.control("heading_input").setValue(-90.0)
        # timeout.emit()：主动触发一次定时器信号，验证这次发送使用哪个目标；不需要真的等待 100 ms
        self.control("control_timer").timeout.emit()

        self.assertEqual(len(self.sent), 2)
        previous = self.message(1)
        self.assertAlmostEqual(previous["speed_mps"], 1.5)
        self.assertAlmostEqual(previous["heading_rad"], 1.5707963267948966)

        self.control("start_button").click()

        self.assertEqual(len(self.sent), 3)
        applied = self.message(2)
        self.assertAlmostEqual(applied["speed_mps"], -1.0)
        self.assertAlmostEqual(applied["heading_rad"], -1.5707963267948966)
    def test_failed_first_send_stops_control_only(self):
        self.window.control_sock.close()

        try:
            self.window.start_control()
        except OSError as exc:
            self.fail(f"首次发送失败应由界面处理，不能向外抛出：{exc}")

        self.assertFalse(self.window.control_timer.isActive())
        self.assertTrue(self.window.timer.isActive())
        self.assertFalse(self.window.sock.closed)
        self.assertIn("发送失败", self.window.control_status.text())
        self.assertEqual(self.window.control_sock.send_attempts, 1)
        self.assertEqual(self.sent, [])

    def test_failed_periodic_send_preserves_telemetry(self):
        self.apply_inputs(1.5, 90.0)
        readings = [self.window.speed_value, *self.window.pose_values, self.window.protection_value]
        before_values = [label.text() for label in readings]
        before_status = self.window.status.text()
        before_state = self.window.map.state

        self.window.control_sock.close()

        try:
            self.window.send_control()
        except OSError as exc:
            self.fail(f"周期发送失败应由界面处理，不能向外抛出：{exc}")

        self.assertFalse(self.window.control_timer.isActive())
        self.assertTrue(self.window.timer.isActive())
        self.assertFalse(self.window.sock.closed)
        self.assertIn("发送失败", self.window.control_status.text())

        self.assertEqual([label.text() for label in readings], before_values)
        self.assertEqual(self.window.status.text(), before_status)
        self.assertIs(self.window.map.state, before_state)

        self.assertEqual(self.window.control_sock.send_attempts, 2)
        self.window.send_control()
        self.assertEqual(self.window.control_sock.send_attempts, 2)
        self.assertEqual(len(self.sent), 1)

    def test_normal_stop_keeps_publishing_zero_speed(self):
        self.apply_inputs(1.5, 90.0)
        self.control("heading_input").setValue(-90.0)

        self.control("stop_button").click()

        self.assertTrue(self.window.control_timer.isActive())
        self.assertEqual(len(self.sent), 2)
        stopped = self.message(1)
        self.assertAlmostEqual(stopped["speed_mps"], 0.0)
        self.assertAlmostEqual(stopped["heading_rad"], 1.5707963267948966)

        self.window.control_timer.timeout.emit()

        self.assertEqual(len(self.sent), 3)
        repeated = self.message(2)
        self.assertAlmostEqual(repeated["speed_mps"], 0.0)
        self.assertAlmostEqual(repeated["heading_rad"], 1.5707963267948966)

    def test_stop_sending_blocks_later_ticks(self):
        self.apply_inputs(1.5, 90.0)
        attempts_before = self.window.control_sock.send_attempts
        sent_before = len(self.sent)

        self.control("stop_sending_button").click()

        self.assertFalse(self.window.control_timer.isActive())
        self.assertEqual(self.window.control_sock.send_attempts, attempts_before)

        self.window.control_timer.timeout.emit()

        self.assertEqual(self.window.control_sock.send_attempts, attempts_before)
        self.assertEqual(len(self.sent), sent_before)
        self.assertTrue(self.window.timer.isActive())
        self.assertFalse(self.window.sock.closed)

    def test_legacy_control_keeps_publishing_when_telemetry_expires(self):
        self.apply_inputs(1.5, 0.0)
        snapshot = TelemetrySnapshot(state=VehicleState(), fault_latched=False)
        with (
            patch("rover.desktop.app.monotonic") as clock,
            patch("rover.desktop.app.try_receive_telemetry", side_effect=[
                (snapshot, ("127.0.0.1", 9999)), None, None,
            ]),
        ):
            clock.return_value = 10.0
            self.window.poll_telemetry()
            clock.return_value = 11.0
            self.window.poll_telemetry()

        self.assertIn("遥测过期", self.window.status.text())
        self.assertTrue(self.window.control_timer.isActive())
        self.window.control_timer.timeout.emit()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.message(1)["speed_mps"], 1.5)

    def test_normal_stop_does_not_start_inactive_publishing(self):
        self.control("stop_button").click()

        self.assertFalse(self.window.control_timer.isActive())
        self.assertEqual(self.window.control_sock.send_attempts, 0)
        self.assertEqual(self.sent, [])

        self.apply_inputs(1.5, 90.0)
        self.control("stop_sending_button").click()
        attempts_before = self.window.control_sock.send_attempts

        self.control("stop_button").click()

        self.assertFalse(self.window.control_timer.isActive())
        self.assertEqual(self.window.control_sock.send_attempts, attempts_before)


class TestSecureDesktopControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sent = []
        self.worker = Mock(spec=SecureControlWorker)
        self.worker.status.return_value = WorkerStatus("ready")
        self.worker.apply_target.return_value = True
        self.worker.wait_closed.return_value = True

        sockets = patch(
            "rover.desktop.app.socket.socket",
            side_effect=lambda *args: FakeSocket(self.sent),
        )
        sockets.start()
        self.addCleanup(sockets.stop)
        self.window = DesktopConsole(secure_worker=self.worker)
        self.addCleanup(self.window.close)

    def poll_telemetry_at(self, now, received=None):
        side_effect = [received] if received is None else [received, None]
        with (
            patch("rover.desktop.app.monotonic", return_value=now),
            patch("rover.desktop.app.try_receive_telemetry", side_effect=side_effect),
        ):
            self.window.poll_telemetry()

    @staticmethod
    def telemetry_packet(*, fault_latched=False):
        return TelemetrySnapshot(state=VehicleState(), fault_latched=fault_latched), ("127.0.0.1", 9999)

    def test_apply_routes_target_to_worker(self):
        self.worker.start.assert_called_once_with()
        self.window.speed_input.setValue(1.5)
        self.window.heading_input.setValue(90.0)
        self.window.start_button.click()

        self.worker.apply_target.assert_called_once_with(ControlTarget(1.5, pi / 2))
        self.assertFalse(self.window.control_timer.isActive())
        self.assertEqual(self.sent, [])

    def test_normal_stop_and_stop_sending_are_different(self):
        self.window.speed_input.setValue(1.5)
        self.window.heading_input.setValue(90.0)
        self.window.start_button.click()
        self.window.heading_input.setValue(-90.0)
        self.window.stop_button.click()

        self.worker.apply_target.assert_called_with(ControlTarget(0.0, pi / 2))
        self.window.stop_sending_button.click()
        self.worker.stop_sending.assert_called_once_with()
        self.window.stop_button.click()
        self.assertEqual(self.worker.apply_target.call_count, 2)
        self.assertEqual(self.sent, [])

    def test_worker_failure_disables_control_and_keeps_telemetry(self):
        self.worker.status.return_value = WorkerStatus("failed", "TLS lost")
        self.window.poll_secure_status()

        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIn("TLS lost", self.window.control_status.text())
        self.assertTrue(self.window.timer.isActive())
        self.assertFalse(self.window.sock.closed)

    def test_stale_readings_are_preserved_and_labeled_as_the_last_report(self):
        vehicle = VehicleState(x_m=3, y_m=-1, heading_rad=0.25, speed_mps=1.25)
        peer = ("127.0.0.1", 9999)
        self.poll_telemetry_at(10.0, (TelemetrySnapshot(vehicle, False), peer))
        self.assertEqual(self.window.speed_value.text(), "1.25")
        self.assertTrue(self.window.map.telemetry_fresh)

        self.poll_telemetry_at(11.0)
        self.assertEqual(self.window.speed_caption.text(), "最后上报速度")
        self.assertEqual(self.window.protection_value.text(), "未锁存")
        self.assertEqual(self.window.speed_value.text(), "1.25")
        self.assertFalse(self.window.map.telemetry_fresh)
        self.assertEqual(self.window.map.state, vehicle)

        self.poll_telemetry_at(11.5, self.telemetry_packet(fault_latched=True))
        self.assertEqual(self.window.speed_caption.text(), "实测速度")
        self.assertEqual(self.window.protection_value.text(), "已锁存")
        self.assertTrue(self.window.map.telemetry_fresh)
        self.assertIsNone(self.window.current_target)

    def test_telemetry_expiry_stops_secure_control_once_at_timeout(self):
        self.poll_telemetry_at(10.0, self.telemetry_packet())
        self.window.speed_input.setValue(1.5)
        self.window.start_button.click()
        self.worker.status.return_value = WorkerStatus("sending")

        self.poll_telemetry_at(10.99)
        self.worker.stop_sending.assert_not_called()
        self.assertTrue(self.window.start_button.isEnabled())

        self.poll_telemetry_at(11.0)
        self.assertIn("遥测过期", self.window.status.text())
        self.assertIsNone(self.window.current_target)
        self.assertFalse(self.window.start_button.isEnabled())
        self.worker.stop_sending.assert_called_once_with()

        self.poll_telemetry_at(11.5)
        self.window.poll_secure_status()
        self.window.start_control()
        self.worker.stop_sending.assert_called_once_with()
        self.worker.apply_target.assert_called_once_with(ControlTarget(1.5, 0.0))
        self.assertIsNone(self.window.current_target)
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.timer.isActive())

    def test_telemetry_recovery_requires_unlocked_state_and_explicit_apply(self):
        self.poll_telemetry_at(10.0, self.telemetry_packet())
        self.window.speed_input.setValue(1.5)
        self.window.start_button.click()
        self.poll_telemetry_at(11.0)
        self.worker.status.return_value = WorkerStatus("stopped")

        self.poll_telemetry_at(11.5, self.telemetry_packet(fault_latched=True))
        self.assertEqual(self.window.status.text(), "遥测可用")
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(self.window.emergency_stop_button.isEnabled())
        self.assertTrue(self.window.reset_button.isEnabled())
        self.window.start_control()

        self.poll_telemetry_at(11.6, self.telemetry_packet())
        self.assertTrue(self.window.start_button.isEnabled())
        self.assertIsNone(self.window.current_target)
        self.worker.apply_target.assert_called_once_with(ControlTarget(1.5, 0.0))
        self.worker.stop_sending.assert_called_once_with()

        self.window.start_button.click()
        self.assertEqual(self.worker.apply_target.call_count, 2)
        self.assertEqual(self.window.current_target, ControlTarget(1.5, 0.0))

    def test_nonblocking_telemetry_receive_error_suspends_secure_control(self):
        self.poll_telemetry_at(10.0, self.telemetry_packet())
        self.window.speed_input.setValue(1.5)
        self.window.start_button.click()
        self.worker.status.return_value = WorkerStatus("sending")

        with (
            patch("rover.desktop.app.monotonic", return_value=10.1),
            patch("rover.desktop.app.try_receive_telemetry", side_effect=OSError("telemetry socket failed")),
        ):
            self.window.poll_telemetry()
            self.window.poll_telemetry()

        self.assertIn("接收错误", self.window.status.text())
        self.assertTrue(self.window.sock.closed)
        self.assertFalse(self.window.timer.isActive())
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertIsNone(self.window.current_target)
        self.worker.stop_sending.assert_called_once_with()
        self.window.start_control()
        self.worker.apply_target.assert_called_once_with(ControlTarget(1.5, 0.0))

    def test_operator_label_tracks_authenticated_session(self):
        self.worker.status.return_value = WorkerStatus("connecting")
        window = DesktopConsole(secure_worker=self.worker, operator_id="desk-a02")
        self.addCleanup(window.close)
        self.assertEqual(window.operator_label.text(), "当前操作员：待认证")

        for state in ("ready", "sending", "latched"):
            self.worker.status.return_value = WorkerStatus(state)
            window.poll_secure_status()
            self.assertEqual(window.operator_label.text(), "当前操作员：desk-a02")

        self.worker.status.return_value = WorkerStatus("failed")
        window.poll_secure_status()
        self.assertEqual(window.operator_label.text(), "当前操作员：—")

        self.worker.status.return_value = WorkerStatus("ready")
        window.poll_secure_status()
        self.worker.wait_closed.return_value = False
        window.closeEvent(QCloseEvent())
        self.worker.wait_closed.return_value = True
        self.assertEqual(window.operator_label.text(), "当前操作员：—")

    def test_event_log_is_bounded_deduplicated_and_omits_failure_detail(self):
        history = self.window.event_log
        self.assertTrue(history.isReadOnly())

        self.worker.status.return_value = WorkerStatus("failed", "private diagnostic")
        self.window.poll_secure_status()
        self.window.poll_secure_status()
        lines = history.toPlainText().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertRegex(lines[0], r"^\[\d{2}:\d{2}:\d{2}\] 安全连接就绪$")
        self.assertRegex(lines[1], r"^\[\d{2}:\d{2}:\d{2}\] 安全控制失败$")

        for _ in range(51):
            for state in ("connecting", "ready"):
                self.worker.status.return_value = WorkerStatus(state)
                self.window.poll_secure_status()
        lines = history.toPlainText().splitlines()
        self.assertEqual(len(lines), 100)
        self.assertTrue(lines[-1].endswith("安全连接就绪"))
        self.assertNotIn("安全控制失败", history.toPlainText())

    def test_event_log_records_telemetry_availability_and_fault_transitions(self):
        peer = ("127.0.0.1", 9999)
        state = VehicleState(x_m=0.0, y_m=0.0, heading_rad=0.0, speed_mps=0.0)
        normal = (TelemetrySnapshot(state=state, fault_latched=False), peer)
        latched = (TelemetrySnapshot(state=state, fault_latched=True), peer)
        with (
            patch("rover.desktop.app.monotonic") as clock,
            patch("rover.desktop.app.try_receive_telemetry", side_effect=[
                normal, None, latched, latched, None, None, None, normal, None,
            ]),
        ):
            for now in (10.0, 10.1, 11.2, 11.3, 11.4):
                clock.return_value = now
                self.window.poll_telemetry()

        messages = [
            line.split("] ", 1)[1]
            for line in self.window.event_log.toPlainText().splitlines()
        ]
        self.assertEqual(messages.count("遥测可用"), 2)
        self.assertEqual(messages.count("遥测过期"), 1)
        self.assertEqual(messages.count("故障锁存"), 1)
        self.assertEqual(messages.count("故障锁存已解除"), 1)

    def test_close_lets_worker_finish_without_blocking_ui(self):
        self.worker.wait_closed.return_value = False
        event = QCloseEvent()
        self.window.closeEvent(event)

        self.worker.close.assert_called_once_with()
        self.worker.wait_closed.assert_called_with(0)
        self.assertFalse(event.isAccepted())
        self.assertFalse(self.window.sock.closed)

        self.worker.wait_closed.return_value = True
        self.window.poll_secure_status()
        self.assertTrue(self.window.sock.closed)
        self.worker.close.assert_called_once_with()

    def test_secure_cli_passes_worker_to_window(self):
        with (
            patch("rover.desktop.app.getpass", return_value="test-only"),
            patch("rover.desktop.app.SecureControlWorker") as create_worker,
            patch("rover.desktop.app.QApplication") as create_app,
            patch("rover.desktop.app.DesktopConsole") as create_window,
        ):
            create_app.return_value.exec.return_value = 0
            self.assertEqual(main(["--secure"]), 0)
            create_worker.assert_called_once()
            create_window.assert_called_once_with(
                secure_worker=create_worker.return_value,
                telemetry_address=("127.0.0.1", 10000),
                operator_id="desktop-a",
            )

    def test_secure_emergency_and_reset_buttons_call_worker(self):
        emergency = getattr(self.window, "emergency_stop_button", None)
        reset = getattr(self.window, "reset_button", None)
        self.assertIsNotNone(emergency)
        self.assertIsNotNone(reset)

        self.window.start_button.click()
        self.assertFalse(reset.isEnabled())
        emergency.click()
        self.worker.emergency_stop.assert_called_once_with()
        self.assertIsNone(self.window.current_target)

        self.worker.status.return_value = WorkerStatus("latched")
        self.window.poll_secure_status()
        self.assertFalse(self.window.start_button.isEnabled())
        self.assertTrue(reset.isEnabled())
        reset.click()
        self.worker.reset_control.assert_called_once_with()

    def test_secure_cli_routes_lan_addresses(self):
        with (
            patch("rover.desktop.app.getpass", return_value="test-only"),
            patch("rover.desktop.app.connect_management") as connect_management,
            patch("rover.desktop.app.SecureControlWorker") as create_worker,
            patch("rover.desktop.app.QApplication") as create_app,
            patch("rover.desktop.app.DesktopConsole") as create_window,
        ):
            create_app.return_value.exec.return_value = 0
            self.assertEqual(
                main(
                    [
                        "--secure",
                        "--vehicle-host",
                        "192.0.2.10",
                        "--management-port",
                        "19178",
                        "--control-port",
                        "9998",
                        "--telemetry-bind-host",
                        "192.0.2.20",
                        "--telemetry-port",
                        "10001",
                        "--server-name",
                        "rover.demo",
                        "--client-id",
                        "desk-a02",
                    ]
                ),
                0,
            )

            connect, control_address = create_worker.call_args.args
            self.assertEqual(control_address, ("192.0.2.10", 9998))
            connect()
            self.assertEqual(
                connect_management.call_args.args[0], ("192.0.2.10", 19178)
            )
            self.assertEqual(
                connect_management.call_args.kwargs["server_hostname"], "rover.demo"
            )
            self.assertEqual(connect_management.call_args.kwargs["client_id"], "desk-a02")
            create_window.assert_called_once_with(
                secure_worker=create_worker.return_value,
                telemetry_address=("192.0.2.20", 10001),
                operator_id="desk-a02",
            )

    def test_telemetry_socket_binds_requested_address(self):
        address = ("192.0.2.20", 10001)
        with patch("rover.desktop.app.socket.socket") as create_socket:
            window = DesktopConsole(
                secure_worker=self.worker, telemetry_address=address
            )
            self.addCleanup(window.close)
            create_socket.return_value.bind.assert_called_once_with(address)

    def test_cli_rejects_invalid_lan_options_before_prompt(self):
        cases = (
            (["--vehicle-host", "192.0.2.10"], "局域网参数需要 --secure"),
            (["--secure", "--control-port", "0"], "--control-port 需要 1–65535"),
            (["--secure", "--vehicle-host", " "], "地址不能为空"),
        )
        for argv, message in cases:
            with (
                self.subTest(argv=argv),
                patch("rover.desktop.app.getpass") as ask_credential,
                patch("sys.stderr", new_callable=StringIO) as stderr,
            ):
                with self.assertRaises(SystemExit) as error:
                    main(argv)
                self.assertEqual(error.exception.code, 2)
                self.assertIn(message, stderr.getvalue())
                ask_credential.assert_not_called()


if __name__ == "__main__":
    unittest.main()
