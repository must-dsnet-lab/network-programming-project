"""Generate the editable motion diagram and its PNG using the project's PySide6.

Edit the text/layout below, then run this file from any working directory.
Outputs live beside this script; README uses relative links to them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from math import ceil, hypot
from xml.sax.saxutils import escape

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

WIDTH, HEIGHT = 1000, 2195
ROOT = Path(__file__).resolve().parent
STEM = "04-vehicle-motion"
FONT = "Microsoft YaHei"
CODE_FONT = "Consolas"
BODY_FONT_INCREMENT = 3
BOLD_FONT_INCREMENT = 2
INK = "#172b4d"
MUTED = "#4b6078"
BLUE = "#2563b8"
TEAL = "#087f8c"

parts: list[str] = []
text_checks: list[dict] = []
cards: list[dict] = []


def raw(value: str) -> None:
    parts.append(value)


def rect(x, y, w, h, fill="#ffffff", stroke="#a5b8d0", radius=10, width=1.4):
    raw(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')


def text(x, y, value, size=17, bold=False, color=INK, code=False,
         max_width=None, anchor="start", rotate=None):
    size += BOLD_FONT_INCREMENT if bold else BODY_FONT_INCREMENT
    family = CODE_FONT if code else FONT
    font = QFont(family)
    font.setPixelSize(int(size))
    font.setWeight(QFont.Weight.Bold if bold else QFont.Weight.Normal)
    measured = QFontMetricsF(font).horizontalAdvance(value)
    if max_width is not None:
        text_checks.append({
            "text": value, "width": round(measured, 2),
            "allowed": max_width, "fits": measured <= max_width,
        })
        if measured > max_width:
            raise ValueError(f"Text does not fit: {value!r}: {measured} > {max_width}")
    transform = f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ""
    css_family = f"{family}, Microsoft YaHei UI, Arial, sans-serif"
    raw(f'<text x="{x}" y="{y}" font-family="{css_family}" font-size="{size}" '
        f'font-weight="{700 if bold else 400}" fill="{color}" '
        f'text-anchor="{anchor}"{transform}>{escape(value)}</text>')


def line(points, color="#58718f", dashed=False, arrow=True, width=1.8):
    points = list(points)
    # A card may grow to fit larger text; start its outgoing connector at its new edge.
    if len(points) >= 2 and points[1][1] > points[0][1]:
        x, y = points[0]
        for item in cards:
            if item["x"] <= x <= item["x"] + item["width"] and y == item["y"] + item["base_height"]:
                points[0] = (x, item["y"] + item["height"])
                break
    p = " ".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="6 5"' if dashed else ""
    raw(f'<polyline points="{p}" fill="none" stroke="{color}" '
        f'stroke-width="{width}" stroke-linejoin="round"{dash}/>')
    if arrow:
        ax, ay = points[-2]
        bx, by = points[-1]
        length = hypot(bx - ax, by - ay)
        ux, uy = (bx - ax) / length, (by - ay) / length
        base_x, base_y = bx - ux * 9, by - uy * 9
        vertices = [(bx, by), (base_x - uy * 4, base_y + ux * 4),
                    (base_x + uy * 4, base_y - ux * 4)]
        coords = " ".join(f"{vx:.2f},{vy:.2f}" for vx, vy in vertices)
        raw(f'<polygon points="{coords}" fill="{color}"/>')


def card(x, y, w, h, header, rows=(), *, code_header=False,
         fill="#ffffff", stroke="#a5b8d0", body_code=False, size=16):
    base_height = h
    line_gap = max(25, size + BODY_FONT_INCREMENT + 5)
    if rows:
        required = 52 + (len(rows) - 1) * line_gap + (size + BODY_FONT_INCREMENT) * 0.3 + 8
        h = max(h, ceil(required))
    raw('<g class="flow-card">')
    rect(x, y, w, h, fill, stroke)
    cards.append({"header": header, "x": x, "y": y, "width": w, "height": h, "base_height": base_height})
    text(x + 14, y + 25, header, 18, True, code=code_header, max_width=w - 28)
    for i, value in enumerate(rows):
        baseline = y + 52 + line_gap * i
        text(x + 14, baseline, value, size, color=MUTED,
             code=body_code, max_width=w - 28)
    raw("</g>")


def panel(x, y, w, h, title, tint, stroke):
    rect(x, y, w, h, tint, stroke, radius=14, width=1.8)
    text(x + 20, y + 32, title, 21, True, max_width=w - 40)


def dot(x, y, color=BLUE, r=4):
    raw(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>')


def build_svg():
    raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    raw('<title id="title">车辆运动与画布坐标：从世界状态到窗口显示</title>')
    raw('<desc id="description">车端每轮选择目标、更新运动参数、用速度时间和三角函数计算新世界位置。'
        '桌面保存遥测状态及世界轨迹，Qt稍后重绘，将米制世界坐标映射到画布像素坐标，先画轨迹再定位和旋转车头。</desc>')
    rect(0, 0, WIDTH, HEIGHT, "#ffffff", "#ffffff", 0)
    text(30, 38, "车辆怎样运动，又怎样显示在画布上？", 26, True, max_width=940)
    text(30, 67, "沿主箭头阅读；函数名加粗，参数与关键变量分行；两种坐标始终分开。", 17,
         color=MUTED, max_width=940)

    # 1. World-space simulation. Six explicit substeps, not one giant box.
    panel(20, 92, 960, 764, "① 世界坐标中的运动 · 车端每轮调用 step_vehicle()", "#f5f8ff", "#8ba5cc")
    card(55, 151, 430, 80, "本轮输入：当前车辆状态 state", (
        "位置 x_m / y_m（m）",
        "heading_rad（rad）· speed_mps（m/s）",
    ), size=15)
    card(55, 258, 430, 65, "target_for_tick()", (
        "选择有效目标／零速目标",
    ), code_header=True, fill="#e9efff")
    card(55, 350, 430, 82, "apply_target()", (
        "参数：state, target → 返回 controlled_state",
        "更新速度与绝对朝向；位置 x_m / y_m 不变",
    ), code_header=True, fill="#e9efff", size=15)
    card(55, 459, 430, 83, "advance() · ① 计算有向位移", (
        "参数：controlled_state, dt_s",
        "distance_m = speed_mps × dt_s",
    ), code_header=True, fill="#e9efff", size=16)
    card(55, 559, 430, 100, "advance() · ② 分解位移并累加位置", (
        "新x_m = 旧x_m + distance_m×cos(heading_rad)",
        "新y_m = 旧y_m + distance_m×sin(heading_rad)",
        "返回新 VehicleState；原来的状态对象保持不变",
    ), code_header=True, fill="#e9efff", size=14)
    card(55, 686, 430, 78, "返回并保存新状态", (
        "赋值：state = step_vehicle(...)",
        "下一轮从新 state 继续计算",
    ), fill="#e5eefb", size=16)
    for bottom, top in [(231, 258), (323, 350), (432, 459), (542, 559), (659, 686)]:
        line([(270, bottom), (270, top)], color=BLUE)
    line([(55, 725), (36, 725), (36, 191), (55, 191)], color=BLUE, dashed=True)
    text(55, 800, "虚线回到下一轮：累积运动，不从初始位置重算。", 15, color=BLUE, max_width=430)

    # Geometric meaning of cos and sin, with no false numerical scale.
    rect(525, 151, 430, 327, "#ffffff", "#a5b8d0")
    text(541, 177, "世界坐标：把沿车头的位移分解到两根轴", 18, True, max_width=398)
    text(541, 202, "旧位置取 (0,0)；图示不按数值比例", 15, color=MUTED, max_width=398)
    ox, oy, px, py = 580, 416, 850, 253
    line([(555, oy), (920, oy)], color="#7890ad")
    line([(ox, 446), (ox, 228)], color="#7890ad")
    text(920, 439, "+X（m）", 15, color=MUTED, anchor="end")
    text(592, 231, "+Y（m）", 15, color=MUTED)
    line([(ox, oy), (px, py)], color=BLUE, width=2.6)
    line([(px, py), (px, oy)], color=TEAL, dashed=True, arrow=False)
    line([(ox, oy), (px, oy)], color=TEAL, width=2.6, arrow=False)
    dot(ox, oy)
    dot(px, py)
    text(593, 445, "旧位置", 15, color=BLUE)
    text(860, 246, "新位置", 15, color=BLUE)
    text(715, 439, "Δx", 18, True, color=TEAL)
    text(863, 343, "Δy", 18, True, color=TEAL)
    text(717, 309, "distance_m", 16, True, color=BLUE, code=True, rotate=-31)
    raw(f'<path d="M {ox+63} {oy} A 63 63 0 0 0 {ox+54} {oy-33}" '
        'fill="none" stroke="#a77915" stroke-width="1.8"/>')
    text(654, 397, "heading_rad", 16, color="#95640c", code=True)

    card(525, 503, 430, 128, "单步例子：向右上方移动", (
        "speed_mps = 1；heading_rad = π/4（45°）",
        "dt_s = 0.02 s → distance_m = 0.02 m",
        "Δx ≈ 0.0141 m；Δy ≈ 0.0141 m",
    ), size=16)
    card(525, 656, 430, 139, "模型与时间边界", (
        "绝对朝向直接设定；未模拟加速度与转弯半径",
        "正速度沿车头运动；负速度沿相反方向倒退",
        "now_s：本地单调时钟，交给保护逻辑",
        "dt_s = 0.02：本轮模拟推进量，用于位移",
    ), size=15)
    text(55, 832, "连续车端：每轮只推进一次；没有新控制包时，也继续检查保护和更新车辆。", 16,
         color=MUTED, max_width=900)

    # Only one bridge to the existing networking diagrams.
    line([(500, 856), (500, 919)], color="#58718f")
    text(524, 887, "遥测采样、传输与解码（收发过程见图 2 / 图 3）", 15,
         color=MUTED, max_width=449)
    text(524, 909, "桌面得到 snapshot.state；两端不共享 Python 对象", 15,
         color=MUTED, max_width=449)

    # 2. World-state storage is separate from paint scheduling.
    panel(20, 923, 960, 287, "② 桌面保存状态 · VehicleMap.set_state()", "#f6fafc", "#9aafbf")
    card(50, 982, 280, 100, "保存当前状态", (
        "self.state = state",
        "保留世界位置、朝向与速度",
    ), body_code=False, size=15)
    card(360, 982, 280, 100, "history.record()", (
        "参数：state",
        "记录 (x_m, y_m)，不存像素",
    ), code_header=True, size=15)
    card(670, 982, 280, 100, "update()", (
        "请求 Qt 稍后重绘",
        "不是同步调用 paintEvent()",
    ), code_header=True, size=15)
    line([(330, 1032), (360, 1032)])
    line([(640, 1032), (670, 1032)])
    text(50, 1123, "先保存状态与轨迹，再请求绘图。", 16, color=MUTED, max_width=420)
    text(50, 1150, "重绘读取历史，不在重绘时追加历史。", 16, color=MUTED, max_width=420)
    line([(810, 1082), (810, 1168), (500, 1168), (500, 1307)],
         color=TEAL, dashed=True)
    text(524, 1130, "Qt 调度：多个请求可能合并", 16, color=TEAL, max_width=426)
    text(524, 1153, "窗口尺寸变化也会触发重绘", 16, color=TEAL, max_width=426)

    # 3. Mapping to a top-left-origin canvas. Never overwrite world state.
    panel(20, 1235, 960, 911, "③ 画布显示 · 米制世界坐标 → 地图控件的像素坐标", "#f2faf9", "#7eada8")
    card(50, 1308, 900, 70, "paintEvent()", (
        "读取 self.state 与 history；绘制这一帧画面",
    ), code_header=True, stroke="#7eada8")
    card(50, 1410, 280, 96, "screen_point()", (
        "参数：x_m, y_m（m）",
        "供车辆和每个轨迹点共同使用",
    ), code_header=True, stroke="#7eada8", size=15)
    card(360, 1410, 280, 96, "确定显示参数", (
        "原点：(width//2, height//2)",
        "pixels_per_meter = 50 px/m",
    ), stroke="#7eada8", size=14)
    card(670, 1410, 280, 96, "world_to_screen()", (
        "参数：世界位置、原点、比例",
        "→ screen_x_px/screen_y_px",
    ), code_header=True, stroke="#7eada8", size=15)
    line([(500, 1378), (500, 1393), (190, 1393), (190, 1410)], color=TEAL)
    line([(330, 1458), (360, 1458)], color=TEAL)
    line([(640, 1458), (670, 1458)], color=TEAL)
    line([(810, 1506), (810, 1520), (500, 1520), (500, 1537)], color=TEAL)
    card(50, 1538, 900, 104, "坐标转换公式：+X 同向，+Y 反向", (
        "screen_x_px = origin_x_px + x_m × pixels_per_meter",
        "screen_y_px = origin_y_px − y_m × pixels_per_meter",
    ), stroke="#7eada8", body_code=True, size=20)

    # A true coordinate sketch, distinct top-left and center origins.
    rect(50, 1682, 440, 365, "#ffffff", "#7eada8")
    text(66, 1708, "坐标对应例子 · 地图控件 800 × 600", 18, True, max_width=408)
    x0, y0, cw, ch = 84, 1755, 304, 228
    rect(x0, y0, cw, ch, "#f8fafc", "#b7c7d2", radius=0, width=1)
    line([(x0, y0), (x0+cw+27, y0)], color="#718096")
    line([(x0, y0), (x0, y0+ch+12)], color="#718096")
    text(x0, y0-10, "画布原点 (0,0)", 14, color=MUTED)
    text(x0+cw+34, y0+5, "+X", 14, color=MUTED)
    text(x0-6, y0+ch-3, "+Y", 14, color=MUTED, anchor="end")
    cx, cy = x0+cw/2, y0+ch/2
    line([(cx, cy), (cx+119, cy)], color=BLUE)
    line([(cx, cy), (cx, cy-82)], color=BLUE)
    text(cx+104, cy+20, "+X 世界", 14, color=BLUE, anchor="end")
    text(cx+10, cy-80, "+Y 世界", 14, color=BLUE)
    dot(cx, cy, BLUE)
    spx, spy = cx+100*.38, cy-50*.38
    dot(spx, spy, TEAL, 5)
    line([(spx, spy), (spx+25, spy-30), (422, spy-30)], color=TEAL, arrow=False, width=1.2)
    text(422, spy-38, "(500,250) px", 14, color=TEAL, anchor="end")
    line([(cx, cy), (cx-37, cy+52), (106, cy+52)], color=BLUE, arrow=False, width=1.2)
    text(106, cy+73, "世界 (0,0) m", 14, color=BLUE)
    text(106, cy+93, "对应画布 (400,300) px", 14, color=BLUE)
    text(66, 2024, "世界 (2,1) m → 画布 (500,250) px", 17,
         color=TEAL, max_width=408)

    # Draw history first, then transform the car's own local coordinates.
    line([(500, 1642), (500, 1661), (740, 1661), (740, 1681)], color=TEAL)
    card(530, 1682, 420, 78, "drawPolyline()", (
        "参数：path（逐点转换后的轨迹）",
        "先画轨迹，再画车",
    ), code_header=True, stroke="#7eada8", fill="#e5f4f0", size=16)
    card(530, 1787, 420, 82, "translate()", (
        "参数：screen_point(x_m, y_m)",
        "把车的局部原点移到该画布位置",
    ), code_header=True, stroke="#7eada8", fill="#e5f4f0", size=16)
    card(530, 1888, 420, 82, "rotate()", (
        "参数：-degrees(heading_rad)",
        "世界逆时针为正；画布绘制取负角",
    ), code_header=True, stroke="#7eada8", fill="#e5f4f0", size=15)
    card(530, 1990, 420, 58, "drawPolygon()", (
        "画车（默认车头向右）；绘图不改世界状态",
    ), code_header=True, stroke="#7eada8", fill="#e5f4f0", size=15)
    line([(740, 1760), (740, 1787)], color=TEAL)
    line([(740, 1869), (740, 1888)], color=TEAL)
    line([(740, 1970), (740, 1990)], color=TEAL)

    card(50, 2070, 900, 62, "窗口变尺寸 → 重新计算画布中心与像素位置", (
        "state / history 保持世界数据；pixels_per_meter 当前固定为 50，不自动缩放或跟随。",
    ), stroke="#7eada8", fill="#ffffff", size=16)
    text(30, 2180, "读图边界：运动发生在车端；桌面使用收到的状态绘制，重绘本身不推动小车运动。", 16,
         color=MUTED, max_width=940)
    raw("</svg>")
    return "\n".join(parts) + "\n"


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    # The Windows offscreen plugin may have an empty system font database.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    svg = build_svg()
    svg_path = ROOT / f"{STEM}.svg"
    svg_path.write_bytes(svg.encode("utf-8"))
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")
    png = QImage(WIDTH * 2, HEIGHT * 2, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor("#ffffff"))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, WIDTH * 2, HEIGHT * 2))
    painter.end()
    png_path = ROOT / f"{STEM}.png"
    if not png.save(str(png_path)):
        raise RuntimeError("PNG export failed")
    result = {
        "svg": str(svg_path), "png": str(png_path),
        "width": WIDTH, "height": HEIGHT, "png_scale": 2,
        "cards": len(cards), "checked_labels": len(text_checks),
        "text_overflow": [entry for entry in text_checks if not entry["fits"]],
    }
    if len(os.sys.argv) > 1:
        qa = Path(os.sys.argv[1])
        qa.mkdir(parents=True, exist_ok=True)
        (qa / "labels-and-cards.json").write_bytes(
            (json.dumps({"result": result, "cards": cards, "text_checks": text_checks},
                        ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        preview = png.scaledToWidth(880, Qt.TransformationMode.SmoothTransformation)
        preview.save(str(qa / "readme-width-880.png"))
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
