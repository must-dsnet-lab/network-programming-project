"""Render the P3–P6 authority flow as editable SVG and a high-resolution PNG."""

import argparse
import os
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

import render_vehicle_motion as draw


ROOT = Path(__file__).resolve().parent
STEM = "11-expert-authority-flow"
WIDTH, HEIGHT = 1680, 1518
PNG_SCALE = 2

PAPER = "#f5f8fc"
WHITE = "#ffffff"
INK = "#1a2c44"
MUTED = "#4b6077"
EDGE = "#d5dfeb"
TLS = "#205c9c"
UDP = "#984913"
TELEMETRY = "#096a64"
VEHICLE = "#644393"
LEASE = "#81601b"
FAULT = "#9a3849"
TINT = {
    TLS: "#edf5ff",
    UDP: "#fcf1e8",
    TELEMETRY: "#e9f7f4",
    VEHICLE: "#f0ebf9",
    LEASE: "#fcf5e3",
    FAULT: "#fceef1",
}

LEFT, CENTER, RIGHT = (40, 440), (544, 592), (1200, 440)
ROWS = (302, 432, 562, 692, 822)
MAIN_CARD_H = 110
MID_X = WIDTH // 2


def text(x, y, value, *, size=21, color=INK, code=False, bold=False, width=None, anchor="start"):
    draw.text(x, y, value, size=size, color=color, code=code, bold=bold,
              max_width=width, anchor=anchor)


def arrow(*points, color=VEHICLE, dashed=False, head=True):
    draw.line(points, color=color, dashed=dashed, arrow=head, width=3.8)


def lane(x, width, title, subtitle, color):
    draw.rect(x, 215, width, 720, WHITE, EDGE, radius=15, width=1.5)
    draw.rect(x, 215, width, 71, TINT[color], "none", radius=15)
    text(x + 16, 248, title, size=27, bold=True, color=color, width=width - 32)
    text(x + 16, 274, subtitle, size=18, color=MUTED, width=width - 32)


def card(x, y, width, height, title, rows, *, color, fill=None):
    draw.rect(x, y, width, height, fill or TINT[color], color, radius=11, width=1.6)
    text(x + 16, y + 35, title, size=25, bold=True, color=color, width=width - 32)
    for index, (value, code) in enumerate(rows):
        text(x + 16, y + 66 + index * 27, value, size=18 if code else 21,
             code=code, color=INK, width=width - 32)


def header():
    draw.rect(0, 0, WIDTH, HEIGHT, PAPER, "none", radius=0)
    text(40, 42, "图 11  /  虚拟小车 · 专家版 P3–P6", size=20, bold=True, color=MUTED, width=700)
    text(40, 99, "控制权交接、续期与停车", size=49, bold=True, width=1550)
    text(42, 139, "从上到下阅读。A 与 B 可同时在线，车端只给一个会话控制权。", color=MUTED, width=1360)
    for x, color, dashed, label in (
        (44, TLS, False, "TLS 管理请求"),
        (352, TLS, True, "TLS 状态回复"),
        (656, UDP, False, "UDP v3 控制（HMAC）"),
        (1024, TELEMETRY, True, "UDP 遥测"),
        (1296, VEHICLE, False, "车端裁决顺序"),
    ):
        arrow((x, 181), (x + 49, 181), color=color, dashed=dashed)
        text(x + 60, 188, label, size=20, color=color, width=298)


def main_sequence():
    lane(*LEFT, "控制台 A", "Qt 界面与后台控制线程", TLS)
    lane(*CENTER, "车端裁决", "TLS 会话 · 仿真线程 · UDP 保护", VEHICLE)
    lane(*RIGHT, "控制台 B", "Qt 界面与后台控制线程", TELEMETRY)

    for upper, lower in zip(ROWS, ROWS[1:]):
        arrow((MID_X, upper + MAIN_CARD_H), (MID_X, lower))

    for y in (ROWS[0], ROWS[2]):
        arrow((480, y + 38), (544, y + 38), color=TLS)
        arrow((544, y + 77), (480, y + 77), color=TLS, dashed=True)
        arrow((1200, y + 38), (1136, y + 38), color=TLS)
        arrow((1136, y + 77), (1200, y + 77), color=TLS, dashed=True)
    y = ROWS[1]
    arrow((480, y + 29), (544, y + 29), color=TLS)
    arrow((480, y + 64), (544, y + 64), color=UDP)
    arrow((544, y + 94), (480, y + 94), color=TELEMETRY, dashed=True)
    arrow((1136, y + 82), (1200, y + 82), color=TELEMETRY, dashed=True)
    y = ROWS[3]
    arrow((544, y + 62), (480, y + 62), color=TLS, dashed=True)
    arrow((1200, y + 33), (1136, y + 33), color=TLS)
    arrow((1136, y + 78), (1200, y + 78), color=TLS, dashed=True)
    y = ROWS[4]
    arrow((544, y + 83), (480, y + 83), color=TELEMETRY, dashed=True)
    arrow((1200, y + 30), (1136, y + 30), color=TLS)
    arrow((1200, y + 63), (1136, y + 63), color=UDP)
    arrow((1136, y + 94), (1200, y + 94), color=TELEMETRY, dashed=True)

    sequence = (
        (
            ("点击「申请控制权」", (("acquire_authority()", True), ("以 A 的认证会话申请", False)), TLS),
            ("授予 A · g1 待使能", (("车端开始 3 秒租约", False), ("持有控制权，车仍保持静止", False)), VEHICLE),
            ("B 在旁待命", (("状态显示 A 持有 g1", False), ("B 继续接收遥测", False)), TELEMETRY),
        ),
        (
            ("点击「开始／应用」", (("使能后按窗口周期发送", False), ("UDP v3 携带 g1", False)), UDP),
            ("校验报文后应用目标", (("验证 HMAC、会话、代次、窗口和序号", False),
                               ("update_expert_command_guard_with_reason()", True)), VEHICLE),
            ("B 查看实际车速", (("车辆按 A 的目标行驶", False), ("B 的应用操作仍禁用", False)), TELEMETRY),
        ),
        (
            ("点击「交接」", (("transfer_authority()", True), ("交给另一已认证会话 B", False)), TLS),
            ("撤销 g1 · stopping", (("撤销旧密钥与窗口，清空序号", False), ("清掉控制目标，车端开始停车", False)), VEHICLE),
            ("B 等待车端停车", (("归零前仍未持有控制权", False), ("收到新授权后再操作", False)), TELEMETRY),
        ),
        (
            ("A 退出控制", (("状态更新为未持有", False), ("仍可接收车辆遥测", False)), TELEMETRY),
            ("确认零速 → 授予 B · g2", (("ExpertVehicleControl.observe_vehicle()", True),
                                   ("B 待使能，新租约从授予时开始", False)), VEHICLE),
            ("B 查询并确认持有 g2", (("query_authority()", True), ("遥测有效时才可点击应用", False)), TLS),
        ),
        (
            ("A 观察 B 的控制", (("继续查看权限与实际车速", False), ("旧代次无法重新生效", False)), TELEMETRY),
            ("使能 B · 校验 g2 的 v3", (("新窗口与密钥随新控制上下文发放", False), ("准入后才更新目标和序号", False)), VEHICLE),
            ("B 点击「开始／应用」", (("明确使能并发送 g2 控制", False), ("从遥测确认车辆重新起步", False)), UDP),
        ),
    )
    for y, row in zip(ROWS, sequence, strict=True):
        for (x, width), (title, lines, color) in zip((LEFT, CENTER, RIGHT), row, strict=True):
            card(x, y, width, MAIN_CARD_H, title, lines, color=color)


def timed_stops():
    arrow((MID_X, ROWS[-1] + MAIN_CARD_H), (MID_X, 954))
    draw.rect(544, 954, 592, 51, TINT[VEHICLE], VEHICLE, radius=11, width=1.6)
    text(MID_X, 988, "advance_time() 检查两种截止条件", size=25, color=VEHICLE, bold=True,
         width=560, anchor="middle")

    arrow((MID_X, 1005), (MID_X, 1025), (432, 1025), (432, 1047), color=LEASE)
    arrow((MID_X, 1025), (1248, 1025), (1248, 1047), color=FAULT)
    card(40, 1047, 784, 111, "授权租约 · 3 秒", (
        ("当前持有者经 TLS 续期，截止点更新为车端当前时刻 + 3 秒", False),
        ("默认手动续期，可勾选「移动时自动续期」", False),
    ), color=LEASE)
    card(856, 1047, 784, 111, "UDP 看门狗 · 0.5 秒", (
        ("使能后等首包，之后按最近一条有效控制指令计时", False),
        ("两个截止条件同轮满足时，车端优先进入故障锁存", False),
    ), color=FAULT)
    arrow((432, 1158), (432, 1206), color=LEASE)
    arrow((1248, 1158), (1248, 1206), color=FAULT)
    text(450, 1191, "租约到期", size=21, color=LEASE, width=280)
    text(1266, 1191, "有效 UDP 断流", size=21, color=FAULT, width=280)
    card(40, 1206, 784, 111, "撤权 → 零速 → idle", (
        ("清空控制资格及目标，车速归零，保护未锁存", False),
        ("同一 TLS 会话可重新申请，取得新代次后再明确应用", False),
    ), color=LEASE)
    card(856, 1206, 784, 111, "锁存并撤权 → 零速", (
        ("车端处于 fault_latched，TLS 会话仍可查询", False),
        ("显式复位后重新申请，取得新代次后再明确应用", False),
    ), color=FAULT)


def feedback():
    draw.rect(40, 1351, 1600, 124, WHITE, EDGE, radius=14, width=1.6)
    text(57, 1386, "两端如何刷新操作与遥测", size=25, bold=True, width=1530)
    text(57, 1423, "TLS  query_authority()  →  ExpertControlWorker.status()  →  Qt 刷新持有者、代次、可用动作和反馈",
         color=TLS, width=1530)
    text(57, 1453, "UDP 遥测  →  两端独立接收  →  显示实际车速与锁存，遥测失效时暂停控制",
         color=TELEMETRY, width=1530)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">专家版控制权交接、续期与停车</title>')
    draw.raw('<desc id="description">三条泳道按时间展示 A 获得控制权、车辆归零、B 获得新代次后明确应用，'
             '下方分别展示租约到期停车和 UDP 看门狗锁存。TLS 回复与 UDP 遥测在两端独立更新。</desc>')
    header()
    main_sequence()
    timed_stops()
    feedback()
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, help="optional 1180-pixel preview PNG")
    args = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    if "Microsoft YaHei" not in QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
    svg = build_svg()
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")

    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor(PAPER))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, png.width(), png.height()))
    painter.end()
    (ROOT / f"{STEM}.svg").write_bytes(svg.encode("utf-8"))
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")
    if args.preview is not None:
        preview = png.scaledToWidth(1180, Qt.TransformationMode.SmoothTransformation)
        if not preview.save(str(args.preview)):
            raise RuntimeError("Preview export failed")
    print(f"Rendered {len(draw.text_checks)} width-checked labels at {png.width()} × {png.height()}")


if __name__ == "__main__":
    main()
