import socket
import sys
from time import monotonic

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from desktop_map import VehicleMap
from udp_transport import try_receive_telemetry


class DesktopConsole(QWidget):  # 继承了 QWidget 这个超级父类
    def __init__(self):
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

        self.map = VehicleMap()
        self.status = QLabel("等待遥测")
        # QLabel 默认不换行,所以要设置setWordWrap为自动换行
        self.status.setWordWrap(True)
        self.values = QLabel("x：—\ny：—\n朝向：—\n速度：—\n保护：—")
        self.values.setMinimumWidth(250)
        self.values.setWordWrap(True)

        side = QVBoxLayout()  # V代表Vertical：创建一个垂直的布局盒子
        # addWidget：往盒子里放控件
        side.addWidget(QLabel("最近收到的车辆状态"))  #标题文字
        side.addWidget(self.status)  #状态标签
        side.addWidget(  #监听地址
            QLabel(f"监听 {self.listen_address[0]}:{self.listen_address[1]}")
        )
        side.addWidget(self.values)  #数值标签
        side.addStretch()

        layout = QHBoxLayout(self)  # H代表Horizontal：创建一个水平的布局盒子
        layout.addWidget(self.map, 1) #左边地图控件
        layout.addLayout(side)  # 右边状态控件

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        try:
            self.sock.bind(self.listen_address)
        except OSError:
            self.sock.close()
            raise

        self.timer = QTimer(self) #Qt的定时器
        self.timer.timeout.connect(self.poll_telemetry)  # 定时调用poll_telemetry函数
        self.timer.start(20) #设置20ms(50Hz)的间隔

    def poll_telemetry(self):
        latest = None

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
                self.status.setText(f"接收错误：{exc}")
                return

            if received is None:
                break

            latest, peer = received
            self.last_received_s = monotonic()

        if latest is not None:
            state = latest.state
            self.map.set_state(state) # 更新地图上的车辆位置,调用update()后会触发paintEvent()重绘
            fault_text = "已锁定" if latest.fault_latched else "未锁定"

            self.values.setText(
                f"x = {state.x_m:.3f} m\n"
                f"y = {state.y_m:.3f} m\n"
                f"朝向 = {state.heading_rad:.3f} rad\n"
                f"速度 = {state.speed_mps:.1f} m/s\n"
                f"保护 = {fault_text}\n"
                f"来源 = {peer[0]}:{peer[1]}"
            )

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
        self.timer.stop()
        self.sock.close()
        event.accept()


def main() -> int:
    # QApplication 负责管理整个程序的全局资源
    # sys.argv：把程序参数传给 Qt
    app = QApplication(sys.argv)

    try:
        window = DesktopConsole() #窗口的首次显示、鼠标拖拽拉伸：全靠 Qt 底层的操作系统事件
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
    return app.exec() # 启动主时间循环，阻塞直到窗口关闭


if __name__ == "__main__":
    sys.exit(main())
