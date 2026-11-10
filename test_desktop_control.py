import json
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from demo_desktop_console import DesktopConsole

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
            "demo_desktop_console.socket.socket",
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
        before_values = self.window.values.text()
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

        self.assertEqual(self.window.values.text(), before_values)
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


if __name__ == "__main__":
    unittest.main()
