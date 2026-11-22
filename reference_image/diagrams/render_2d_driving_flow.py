"""Render the U4–U5 driving flow as editable SVG and a high-resolution PNG."""

import argparse
import os
from pathlib import Path

import render_vehicle_motion as draw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent
STEM = "14-2d-driving-flow"
WIDTH, HEIGHT = 1380, 1620
PAPER = "#f5f8fc"
INK = "#1a2c44"
MUTED = "#4b6077"
TLS = "#205c9c"
CONTROL = "#984913"
VEHICLE = "#644393"
TELEMETRY = "#096a64"
FAULT = "#9a3849"
TINT = {
    INK: "#ffffff",
    TLS: "#edf5ff",
    CONTROL: "#fcf1e8",
    VEHICLE: "#f0ebf9",
    TELEMETRY: "#e9f7f4",
    FAULT: "#fceef1",
}

LEFT, RIGHT = 42, 724
CARD_W = 614
LEFT_AXIS, RIGHT_AXIS = 349, 1031


def label(x, y, value, width, *, size=23, color=INK, bold=False, code=False):
    draw.text(x, y, value, size=size, max_width=width, color=color, bold=bold, code=code)


def connector(*points, color=VEHICLE, dashed=False, arrow=True):
    draw.line(points, color=color, dashed=dashed, arrow=arrow, width=3.6)


def box(x, y, width, height, title, color, rows):
    draw.rect(x, y, width, height, TINT[color], color, radius=14, width=1.5)
    label(x + 19, y + 40, title, width - 38, size=29, color=color, bold=True)
    for index, (value, code) in enumerate(rows):
        label(x + 19, y + 78 + 35 * index, value, width - 38, size=21 if code else 23, code=code)


def header():
    draw.rect(0, 0, WIDTH, HEIGHT, PAPER, "none", radius=0)
    label(LEFT, 42, "图 14  /  虚拟小车 · U4–U5", 700, size=22, color=MUTED, bold=True)
    label(LEFT, 105, "二维驾驶运行链路", 1200, size=53, bold=True)
    label(LEFT, 147, "从摇杆和踏板到车端步进，再由遥测回到桌面。", 1200, color=MUTED)
    for x, color, dashed, title in (
        (LEFT, TLS, False, "TLS 管理"),
        (276, CONTROL, False, "UDP 控制"),
        (510, VEHICLE, False, "车端步进"),
        (744, FAULT, True, "撤权或保护"),
        (1034, TELEMETRY, False, "UDP 遥测"),
    ):
        connector((x, 184), (x + 39, 184), color=color, dashed=dashed)
        label(x + 51, 192, title, 235, size=21, color=color)


def desktop_inputs():
    box(LEFT, 221, CARD_W, 174, "操纵输入", INK, (
        ("摇杆／踏板 · 按键 A / D / W / S", False),
        ("DrivingControls._publish_input()", True),
        ("松开回中／归零，摇杆保留 8% 死区", False),
    ))
    box(RIGHT, 221, CARD_W, 174, "桌面 Qt 采样与提交", INK, (
        ("「开始／应用」→ start_control() 启用采样", False),
        ("_sample_driving_input() → input_value()", True),
        ("每 50 ms，变化时 _apply_motion_intent()", True),
    ))
    connector((656, 307), (RIGHT, 307), color=INK)

    box(LEFT, 423, CARD_W, 162, "窗口失焦／正常停车", CONTROL, (
        ("stop_motion() → _disarm_driving()", True),
        ("提交全刹输入，再次操作需重新应用", False),
        ("制动提交失败则停发", False),
    ))
    connector((RIGHT_AXIS, 395), (RIGHT_AXIS, 599), color=CONTROL, arrow=False)
    connector((656, 509), (RIGHT_AXIS, 509), color=CONTROL)
    draw.dot(RIGHT_AXIS, 509, CONTROL, r=5)
    label(681, 491, "DriveInput(0, 1, 0)", 342, size=21, color=CONTROL, code=True)


def control_modes():
    connector((RIGHT_AXIS, 599), (LEFT_AXIS, 599), (LEFT_AXIS, 629), color=CONTROL)
    connector((RIGHT_AXIS, 599), (RIGHT_AXIS, 629), color=CONTROL)
    draw.dot(RIGHT_AXIS, 599, CONTROL, r=5)
    box(LEFT, 629, CARD_W, 183, "本地模拟  ·  普通 UDP", CONTROL, (
        ("DesktopConsole.send_control()", True),
        ("send_target() → UDP v1 / drive_input", True),
        ("直接发往本机模拟车端", False),
    ))
    box(RIGHT, 629, CARD_W, 183, "用户版  ·  TLS 授权与签名 UDP", CONTROL, (
        ("ExpertControlWorker.apply_target()", True),
        ("send_expert_control_command() → UDP v3", True),
    ))
    # The authenticated stream manages authorization. Motion stays on the UDP path.
    draw.rect(RIGHT + 17, 747, CARD_W - 34, 50, TINT[TLS], "none", radius=8)
    label(RIGHT + 28, 781, "TLS：控制权 · prepare() 使能及开窗", CARD_W - 56, size=22, color=TLS)

    connector((LEFT_AXIS, 812), (LEFT_AXIS, 858), color=CONTROL)
    connector((RIGHT_AXIS, 812), (RIGHT_AXIS, 858), color=CONTROL)
    draw.rect(LEFT, 858, WIDTH - 2 * LEFT, 184, TINT[VEHICLE], VEHICLE, radius=14, width=1.5)
    label(61, 898, "车端接收、校验并更新有效意图", 1200, bold=True, size=29, color=VEHICLE)
    connector((690, 913), (690, 1025), color="#d8cceb", arrow=False)
    for x, rows in (
        (61, (
            "try_receive_target()",
            "update_control_guard()",
        )),
        (715, (
            "try_receive_expert_control_command()",
            "update_expert_command_guard_with_reason()",
        )),
    ):
        for index, value in enumerate(rows):
            label(x, 937 + 35 * index, value, 610, size=21, code=True)
    label(61, 1009, "解码后检查锁存与超时", 605)
    label(715, 1009, "校验 HMAC、会话／代次、使能、窗口及序号", 610, size=22)


def vehicle_step():
    left_center, right_center = 401, 1079
    connector((690, 1042), (690, 1069), (left_center, 1069), (left_center, 1115))
    connector((690, 1069), (right_center, 1069), (right_center, 1115), color=FAULT, dashed=True)
    draw.dot(690, 1069, VEHICLE, r=5)
    label(77, 1098, "有效驾驶输入", 310, color=VEHICLE)
    label(775, 1098, "目标缺失／失效", 290, color=FAULT)
    box(LEFT, 1115, 718, 236, "固定步长推进  ·  20 ms", VEHICLE, (
        ("step_vehicle() → target_for_tick()", True),
        ("DriveInput → advance_driving()", True),
        ("油门加速 · 松油滑行 · 刹车优先", False),
        ("转向随行驶距离变化，静止保持朝向", False),
    ))
    box(820, 1115, 518, 236, "零速保护与撤权", FAULT, (
        ("target_for_tick() → 零速目标", True),
        ("看门狗 500 ms／TLS 急停：归零锁存", False),
        ("持有者断开：撤权、清目标并归零", False),
        ("用户版显式复位后再申请、再应用", False),
    ))
    connector((left_center, 1351), (left_center, 1412), color=TELEMETRY)
    connector((right_center, 1351), (right_center, 1412), color=TELEMETRY)
    box(LEFT, 1412, WIDTH - 2 * LEFT, 160, "返回车端实际状态", TELEMETRY, (
        ("send_telemetry()   →  UDP 遥测  →   DesktopConsole.poll_telemetry()", True),
        ("VehicleMap.set_state() 更新轨迹，界面回显车速、位置、朝向及锁存", False),
    ))


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">图 14｜二维驾驶运行链路</title>')
    draw.raw('<desc id="description">操纵输入由 Qt 定时采样。本地模拟使用普通 UDP，用户版通过 '
             'TLS 管理授权并发送签名控制。车端依据有效目标步进运动或施加零速保护，再回传遥测。</desc>')
    header()
    desktop_inputs()
    control_modes()
    vehicle_step()
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preview", type=Path, help="optional 900-pixel-wide PNG")
    args = parser.parse_args()
    application = QGuiApplication.instance() or QGuiApplication([])
    if "Microsoft YaHei" not in QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            path = fonts / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))

    svg = build_svg().encode("utf-8")
    renderer = QSvgRenderer(svg)
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")
    png = QImage(WIDTH * 2, HEIGHT * 2, QImage.Format.Format_ARGB32_Premultiplied)
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
