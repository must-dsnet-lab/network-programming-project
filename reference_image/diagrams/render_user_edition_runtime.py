"""Render the 2D user edition lifecycle as SVG and a high-resolution PNG."""

import argparse
import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

import render_vehicle_motion as draw


ROOT = Path(__file__).resolve().parent
STEM = "13-user-edition-runtime"
WIDTH, HEIGHT = 1440, 1908
PNG_SCALE = 2
PAPER = "#f5f8fc"
WHITE = "#ffffff"
INK = "#1a2c44"
MUTED = "#4b6077"
EDGE = "#d5dfeb"
TLS = "#205c9c"
UDP = "#096a64"
VEHICLE = "#644393"
CLOSE = "#9a3849"
TINT = {TLS: "#edf5ff", UDP: "#e9f7f4", VEHICLE: "#f0ebf9", CLOSE: "#fceef1"}
DESKTOP, SERVER, COLUMN = 40, 848, 552
CHANNEL = 720


def label(x, y, value, width, *, size=22, color=INK, code=False, bold=False, anchor="start"):
    draw.text(x, y, value, size=size, color=color, code=code, bold=bold, max_width=width, anchor=anchor)


def arrow(*points, color=VEHICLE, dashed=False):
    draw.line(points, color=color, width=3.7, dashed=dashed)


def card(x, y, width, height, title, rows, *, color):
    draw.rect(x, y, width, height, TINT[color], color, radius=13, width=1.7)
    label(x + 18, y + 36, title, width - 36, size=28, color=color, bold=True)
    for index, (value, code) in enumerate(rows):
        label(x + 18, y + 68 + 28 * index, value, width - 36, size=20 if code else 21, code=code)


def lane_heading(x, title, subtitle, *, color):
    draw.rect(x, 208, COLUMN, 62, WHITE, EDGE, radius=10, width=1.5)
    label(x + 18, 234, title, COLUMN - 36, size=25, color=color, bold=True)
    label(x + 18, 257, subtitle, COLUMN - 36, size=17, color=MUTED)


def header():
    draw.rect(0, 0, WIDTH, HEIGHT, PAPER, "none", radius=0)
    label(40, 48, "用户版 · 运行结构", 500, size=23, color=MUTED, bold=True)
    label(40, 110, "图 13｜二维用户版运行关系", 1358, size=52, bold=True)
    for x, color, dashed, title in (
        (42, TLS, False, "TLS 管理请求"),
        (300, TLS, True, "TLS 状态回复"),
        (580, UDP, False, "UDP 登记或遥测"),
        (904, VEHICLE, False, "同端流程"),
        (1167, CLOSE, False, "断开清理"),
    ):
        arrow((x, 161), (x + 46, 161), color=color, dashed=dashed)
        label(x + 54, 168, title, 210, size=22, color=color)


def channel(y, title, *, inbound=False, color=TLS, dashed=False):
    label(CHANNEL, y - 8, title, 226, size=19, color=color, anchor="middle")
    left, right = (DESKTOP + COLUMN, y + 7), (SERVER, y + 7)
    arrow(*(right, left) if inbound else (left, right), color=color, dashed=dashed)


def connection_and_telemetry():
    lane_heading(DESKTOP, "桌面端", "Qt 主线程 · ExpertControlWorker", color=TLS)
    lane_heading(SERVER, "车端", "TLS 管理 · 仿真线程 · UDP 遥测", color=VEHICLE)

    card(DESKTOP, 286, COLUMN, 128, "首次注册", (
        ("register_configured_account()", True),
        ("校验证书和主机名后提交账号与凭据", False),
    ), color=TLS)
    card(SERVER, 286, COLUMN, 128, "存储账号 · 默认观察员", (
        ("AccountStore.register()", True),
        ("SQLite 保存角色和加盐凭据摘要", False),
    ), color=TLS)
    channel(320, "注册请求")
    channel(376, "注册结果", inbound=True, dashed=True)

    arrow((308, 414), (308, 454), color=TLS)
    label(327, 442, "注册后点击「连接」", 254, size=18, color=TLS)
    card(DESKTOP, 454, COLUMN, 143, "登录 · 自动绑定遥测端口", (
        ("connect_configured_management()", True),
        ("本地 UDP 先绑定 0.0.0.0:0", False),
        ("TLS 验证通过后认证并打开会话", False),
    ), color=TLS)
    card(SERVER, 454, COLUMN, 143, "建立已认证会话", (
        ("ExpertManagementRuntime.serve()", True),
        ("匹配 SQLite 凭据和角色", False),
        ("按连接绑定 session_id · 最多 6 个会话", False),
    ), color=TLS)
    channel(496, "认证／打开会话")
    channel(555, "会话 ID", inbound=True, dashed=True)

    arrow((308, 597), (308, 638))
    arrow((1124, 597), (1124, 638))
    card(DESKTOP, 638, COLUMN, 130, "申请遥测登记信息", (
        ("prepare_telemetry(session_id)", True),
        ("由后台线程在已认证 TLS 连接上请求", False),
    ), color=TLS)
    card(SERVER, 638, COLUMN, 130, "发放一次性登记令牌", (
        ("TelemetryRegistry.issue()", True),
        ("令牌绑定会话与 TLS 对端 IP · 默认 5 秒", False),
    ), color=TLS)
    channel(676, "登记准备")
    channel(730, "令牌／遥测端口", inbound=True, dashed=True)

    arrow((308, 768), (308, 810))
    arrow((1124, 768), (1124, 810))
    card(DESKTOP, 810, COLUMN, 145, "从遥测接收套接字发送令牌", (
        ("_register_telemetry()", True),
        ("向 TLS 对端 IP 和协商的车端端口发送", False),
        ("query_authority() 查询登记是否成功", False),
    ), color=UDP)
    card(SERVER, 810, COLUMN, 145, "验令牌 · 源地址成为目的地址", (
        ("TelemetryRegistry.accept()", True),
        ("校验令牌与时限，核对 TLS 对端 IP", False),
        ("记录实际 UDP 源 IP:端口，消耗令牌", False),
    ), color=UDP)
    channel(852, "UDP 登记包", color=UDP)
    channel(913, "TLS 登记状态", inbound=True, dashed=True)

    arrow((308, 955), (308, 996), color=UDP)
    arrow((1124, 955), (1124, 996), color=UDP)
    card(DESKTOP, 996, COLUMN, 145, "收到遥测后更新界面", (
        ("DesktopConsole.poll_telemetry()", True),
        ("只接受已协商车端 IP:端口的遥测", False),
        ("显示位置、朝向、实际车速与故障锁存", False),
    ), color=UDP)
    card(SERVER, 996, COLUMN, 145, "向各登记端点回传遥测", (
        ("telemetry_destinations()", True),
        ("send_telemetry()", True),
        ("从接收登记包的同一个车端套接字发出", False),
    ), color=UDP)
    channel(1060, "周期回传", inbound=True, color=UDP)

    arrow((40, 527), (16, 527), (16, 1240), (40, 1240), color=VEHICLE)
    card(40, 1185, 612, 141, "已认证会话 · 角色与登记状态", (
        ("query_authority()", True),
        ("按角色分流，操作者须先完成遥测登记", False),
        ("TLS 断开时清理该会话", False),
    ), color=VEHICLE)
    arrow((1124, 1141), (1124, 1185))
    card(688, 1185, 712, 141, "车端管理窗口 · 会话快照", (
        ("publish_admin_snapshot()", True),
        ("VehicleAdminWindow._update_sessions()", True),
        ("表中「已发送」表示车端 UDP 发送调用成功", False),
    ), color=VEHICLE)


def roles_and_handoff():
    label(40, 1371, "会话角色与交接", 290, size=33, color=VEHICLE, bold=True)
    arrow((346, 1326), (346, 1381), (215, 1381), (215, 1396))
    arrow((346, 1381), (613, 1381), (613, 1396))
    card(40, 1396, 350, 133, "观察员", (
        ("可登录、登记并接收遥测", False),
        ("车端拒绝控制动作", False),
    ), color=UDP)
    card(414, 1396, 398, 133, "操作者", (
        ("登记成功后可申请独占控制权", False),
        ("当前持有者才能发起交接", False),
    ), color=VEHICLE)
    arrow((812, 1468), (848, 1468))
    card(848, 1396, 552, 133, "交接候选 · 其他已登记操作者", (
        ("0 人不可交接 · 1 人直接交接", False),
        ("2 人及以上弹出窗口，按会话选人", False),
    ), color=VEHICLE)
    arrow((1124, 1529), (1124, 1573))
    card(414, 1573, 986, 118, "车端确认归零后交给目标会话", (
        ("observe_vehicle() → ControlAuthority.motion_stopped()", True),
        ("停止原持有者并确认零速 → 授予新代次 → 新持有者点击「开始／应用」", False),
    ), color=VEHICLE)


def disconnect():
    arrow((40, 1282), (16, 1282), (16, 1785), (40, 1785), color=CLOSE)
    card(40, 1733, 1360, 145, "TLS 断开与重连", (
        ("_apply_closed_connections() → TelemetryRegistry.disconnect()", True),
        ("清掉令牌和端点，原持有者断开时撤权停车。角色修改后，旧会话在下次请求时结束。", False),
        ("重新登录建立新会话，使用新令牌登记本次 UDP 接收端点。", False),
    ), color=CLOSE)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">图 13｜二维用户版运行关系</title>')
    draw.raw('<desc id="description">桌面和车端之间的 TLS 注册、登录、遥测令牌签发与状态查询。'
             '接收套接字发送 UDP 登记包，车端回传遥测。下方展示管理快照、角色权限、交接和断开清理。</desc>')
    header()
    connection_and_telemetry()
    roles_and_handoff()
    disconnect()
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, help="optional 900-pixel preview PNG")
    args = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    if "Microsoft YaHei" not in QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            if (path := fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(path))

    svg = build_svg().encode("utf-8")
    renderer = QSvgRenderer(svg)
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor(PAPER))
    with QPainter(png) as painter:
        renderer.render(painter, QRectF(0, 0, png.width(), png.height()))
    (ROOT / f"{STEM}.svg").write_bytes(svg)
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")
    if args.preview is not None:
        preview = png.scaledToWidth(900, Qt.TransformationMode.SmoothTransformation)
        if not preview.save(str(args.preview)):
            raise RuntimeError("Preview export failed")
    print(f"Rendered {len(draw.text_checks)} width-checked labels at {png.width()} × {png.height()}")


if __name__ == "__main__":
    main()
