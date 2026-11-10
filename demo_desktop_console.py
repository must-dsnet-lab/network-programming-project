import socket
import sys
from time import monotonic
from math import radians

from control_target import ControlTarget
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QPushButton,
)

from desktop_map import VehicleMap
from udp_transport import send_target, try_receive_telemetry


class DesktopConsole(QWidget):  # 继承了 QWidget 这个超级父类
    def __init__(self):
        ####################### 搭界面 #######################
        super().__init__()
        self.setWindowTitle("虚拟小车遥测观察台")
        self.resize(1000, 600)
        self.setStyleSheet(
            "QWidget { background: #111827; color: #e5e7eb; }"
            "QLabel { font-size: 14px; }"
        )

        self.listen_address = ("127.0.0.1", 10000)
        self.last_received_s = None
        self.telemetry_timeout_s = 1.0

        self.control_address = ("127.0.0.1", 9999)
        self.current_target: ControlTarget | None = None

        side = QVBoxLayout()  # V代表Vertical：创建一个垂直的布局盒子
        layout = QHBoxLayout(self)  # H代表Horizontal：创建一个水平的布局盒子

        """地图"""
        self.map = VehicleMap()

        """"遥测显示标签（self.status, self.values）"""
        self.status = QLabel("等待遥测")# QLabel 默认不换行,所以要设置setWordWrap为自动换行
        self.status.setWordWrap(True)

        self.values = QLabel("x：—\ny：—\n朝向：—\n速度：—\n保护：—")
        self.values.setMinimumWidth(250)
        self.values.setWordWrap(True)

        """"遥测显示标签 添加到右侧的side"""
        # addWidget：往盒子里放控件
        side.addWidget(QLabel("最近收到的车辆状态"))  # 标题文字
        side.addWidget(self.status)  # 状态标签
        side.addWidget(  # 监听地址
            QLabel(f"监听 {self.listen_address[0]}:{self.listen_address[1]}")
        )
        side.addWidget(self.values)  # 数值标签
        side.addStretch()

        """"控制输入区（speed_input、heading_input、_button、control_status）"""
        self.speed_input = QDoubleSpinBox()
        self.speed_input.setRange(-2.0, 2.0)  # 允许范围
        self.speed_input.setDecimals(2)  # 小数位数
        self.speed_input.setSingleStep(0.1)  # 点击上下箭头时的增量
        self.speed_input.setValue(0.0)  # 初始值
        self.speed_input.setSuffix(" m/s")  # 单位后缀

        self.heading_input = QDoubleSpinBox()
        self.heading_input.setRange(-180.0, 180.0)  # 允许范围
        self.heading_input.setDecimals(1)  # 小数位数
        self.heading_input.setSingleStep(5.0)  # 点击上下箭头时的增量
        self.heading_input.setValue(0.0)  # 初始值
        self.heading_input.setSuffix(" °")  # 单位后缀

        self.start_button = QPushButton("开始／应用")

        self.stop_button = QPushButton("正常停车")

        # 停止发包只会使保护锁定，但是不会停止遥控，因此遥测不会过期，
        # 因为停止接收信息只是不能更新状态，但是终端B会一直上传不变的小车信息
        self.stop_sending_button = QPushButton("停止发包")

        self.control_status = QLabel("未发送控制")
        self.control_status.setWordWrap(True)

        """"控制输入区 添加到右侧的side"""
        side.addWidget(QLabel("控制目标（输入值）"))
        side.addWidget(QLabel("目标速度"))
        side.addWidget(self.speed_input)
        side.addWidget(QLabel("目标朝向（度）"))
        side.addWidget(self.heading_input)
        side.addWidget(self.start_button)
        side.addWidget(self.stop_button)
        side.addWidget(self.stop_sending_button)
        side.addWidget(self.control_status)

        """左地图 右控件 拼接"""
        layout.addWidget(self.map, 1)  # 左边地图控件
        layout.addLayout(side)  # 右边状态控件

        ####################### 两个Socket #######################
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) #接收
        self.control_sock = None #发送

        try:
            self.sock.setblocking(False)
            self.sock.bind(self.listen_address)

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

    def start_control(self):
        self.current_target = ControlTarget(
            speed_mps=self.speed_input.value(),
            heading_rad=radians(self.heading_input.value()),
        )
        self.control_timer.start() #进入100ms倒计时，每隔100ms就send_control
        self.send_control() #立即send_control -> 零延迟响应

    def send_control(self):
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
        if not self.control_timer.isActive() or self.current_target is None:
            return

        self.current_target = ControlTarget(
            speed_mps=0.0,
            heading_rad=self.current_target.heading_rad,
        )
        self.speed_input.setValue(0.0)
        self.send_control()

    # 停止控制定时器，保留两个 Socket 和遥测轮询。
    # 之后即使触发发送回调，send_control() 的入口检查也会阻止发包。
    def stop_control(self):
        if not self.control_timer.isActive():
            return

        self.control_timer.stop()
        self.control_status.setText("已停止发包")

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

        if latest is not None:
            state = latest.state
            fault_text = "已锁定" if latest.fault_latched else "未锁定"

            self.values.setText(
                f"x = {state.x_m:.3f} m\n"
                f"y = {state.y_m:.3f} m\n"
                f"朝向 = {state.heading_rad:.3f} rad\n"
                f"速度 = {state.speed_mps:.1f} m/s\n"
                f"保护 = {fault_text}\n"
                f"来源 = {peer[0]}:{peer[1]}"
            )

        # 报错前若收到过合法数据，先同步读数，再显示错误
        if receive_error is not None:
            self.status.setText(f"接收错误：{receive_error}")
            return

        # 没有新包时，也继续检查显示状态是否过期
        if self.last_received_s is None:
            self.status.setText("等待遥测")
        elif monotonic() - self.last_received_s >= self.telemetry_timeout_s:
            self.status.setText(
                f"遥测过期（{self.telemetry_timeout_s:.1f}秒" "未收到合法报文）"
            )
        else:
            self.status.setText("遥测可用")

    # 操作系统会捕捉到用户用鼠标点击窗口右上角的“❌”的动作，并把它告诉 Qt 框架。Qt 框架会自动去寻找 closeEvent 方法
    def closeEvent(self, event):
        self.control_timer.stop()
        self.timer.stop()
        self.control_sock.close()
        self.sock.close()
        event.accept()


def main() -> int:
    # QApplication 负责管理整个程序的全局资源
    # sys.argv：把程序参数传给 Qt
    app = QApplication(sys.argv)

    try:
        window = (
            DesktopConsole()
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
