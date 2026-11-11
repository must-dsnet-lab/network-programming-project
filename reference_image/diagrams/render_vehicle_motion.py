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

WIDTH, HEIGHT = 1000, 3280
PNG_SCALE = 3
FLOW_GAP = 40
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
    parts.clear()
    text_checks.clear()
    cards.clear()
    flows = []

    def flow(points, color=BLUE, dashed=False, label=None, label_at=None, limit=210):
        flows.append((points, color, dashed, label, label_at, limit))

    def flow_label(x, y, value, color, limit):
        font = QFont(FONT)
        font.setPixelSize(18)
        width = QFontMetricsF(font).horizontalAdvance(value)
        raw('<g class="flow-label">')
        rect(x - 5, y - 19, width + 10, 25, '#ffffff', 'none', 3, 0)
        text(x, y, value, 15, color=color, max_width=limit)
        raw('</g>')

    raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    raw('<title id="title">车辆运动与画布坐标：计算、保存、调度与绘制</title>')
    raw('<desc id="description">按真实源码区分车端运动、遥测采样、桌面逐包保存、Qt异步调度和画布绘制。'
        '实线表示执行顺序，灰线表示数据保存或读取，青色虚线表示Qt调度；同链路主框间距统一为40。'
        '每个合法包保存当前状态，并按去重规则维护轨迹；重绘读取缓存，不推进运动。</desc>')
    rect(0, 0, WIDTH, HEIGHT, '#ffffff', '#ffffff', 0)
    text(30, 38, '车辆怎样运动，状态怎样保存，又怎样画出来？', 25, True, max_width=940)
    text(30, 68, '对照当前实现阅读；函数调用、数据保存与异步绘图分别表达。', 16, color=MUTED, max_width=940)
    line([(35, 99), (85, 99)], color=BLUE)
    text(96, 105, '调用／执行顺序', 15, color=BLUE, max_width=210)
    line([(355, 99), (405, 99)], color=MUTED)
    text(416, 105, '数据保存／读取', 15, color=MUTED, max_width=210)
    line([(675, 99), (725, 99)], color=TEAL, dashed=True)
    text(736, 105, 'Qt稍后调度', 15, color=TEAL, max_width=210)

    # Stage 1: actual call order inside step_vehicle, with one compact guard box.
    panel(20, 130, 960, 850, '① 车端：由控制目标计算新世界状态', '#f5f8ff', '#8ba5cc')
    text(50, 190, 'step_vehicle(state, last_accepted, now_s, dt_s)', 17, True, code=True, max_width=900)
    card(50, 215, 450, 95, '本轮输入', (
        'state：位置x_m / y_m、朝向与速度',
        'last_accepted；now_s（保护）/ dt_s（位移）',
    ), size=15)
    card(50, 350, 450, 80, 'target_for_tick(...)', (
        '选择有效目标／零速目标',
    ), code_header=True, fill='#e9efff', size=15)
    card(50, 470, 450, 95, 'apply_target(state, target)', (
        '返回 controlled_state',
        '更新速度与绝对朝向；世界位置不变',
    ), code_header=True, fill='#e9efff', size=15)
    card(50, 605, 450, 150, 'advance(controlled_state, dt_s)', (
        'distance_m = speed_mps × dt_s',
        '新x_m = 旧x_m + distance_m×cos(heading_rad)',
        '新y_m = 旧y_m + distance_m×sin(heading_rad)',
        '返回新 VehicleState，原状态对象不变',
    ), code_header=True, fill='#e9efff', size=15)
    card(50, 795, 450, 95, '调用方保存返回值', (
        'state = step_vehicle(...)',
        '下一轮以这个新 state 继续计算',
    ), fill='#e5eefb', size=15)
    for bottom, top, caption in [
        (310, 350, '① 选择本轮目标'),
        (430, 470, '② 使用 target'),
        (565, 605, '③ 按 dt_s 推进'),
        (755, 795, '④ 保存返回的新状态'),
    ]:
        assert top - bottom == FLOW_GAP
        flow([(275, bottom), (275, top)], label=caption, label_at=(300, top-14))
    flow([(50, 842), (34, 842), (34, 262), (50, 262)], color=MUTED)
    text(50, 934, '回到下一轮：沿新 state 累积运动，不从初始位置重算。', 15, color=BLUE, max_width=900)
    text(50, 963, '连续车端每轮只推进一次；没有新控制包时，也继续检查保护与更新车辆。', 15, color=MUTED, max_width=900)

    # World geometry and its numerical interpretation.
    rect(540, 215, 410, 330, '#ffffff', '#a5b8d0')
    text(556, 242, '世界坐标：将位移分解到两根轴', 18, True, max_width=378)
    text(556, 271, '旧位置取(0,0)；图示不按数值比例', 15, color=MUTED, max_width=378)
    ox, oy, px, py = 580, 478, 850, 315
    line([(555, oy), (920, oy)], color='#7890ad')
    line([(ox, 514), (ox, 296)], color='#7890ad')
    text(920, 507, '+X（m）', 15, color=MUTED, anchor='end')
    text(592, 303, '+Y（m）', 15, color=MUTED)
    line([(ox, oy), (px, py)], color=BLUE, width=2.6)
    line([(px, py), (px, oy)], color=TEAL, dashed=True, arrow=False)
    line([(ox, oy), (px, oy)], color=TEAL, width=2.6, arrow=False)
    dot(ox, oy); dot(px, py)
    text(592, 528, '旧位置', 15, color=BLUE)
    text(860, 308, '新位置', 15, color=BLUE)
    text(710, 505, 'Δx', 18, True, color=TEAL)
    text(862, 409, 'Δy', 18, True, color=TEAL)
    text(713, 373, 'distance_m', 16, True, color=BLUE, code=True, rotate=-31)
    raw(f'<path d="M {ox+63} {oy} A 63 63 0 0 0 {ox+54} {oy-33}" '
        'fill="none" stroke="#a77915" stroke-width="1.8"/>')
    text(653, 458, 'heading_rad', 15, color='#95640c', code=True)
    card(540, 585, 410, 125, '单步例子：向右上方移动', (
        'speed_mps = 1；heading_rad = π/4',
        'dt_s = 0.02 s → distance_m = 0.02 m',
        'Δx ≈ 0.0141 m；Δy ≈ 0.0141 m',
    ), size=15)
    card(540, 750, 410, 140, '模型与时间边界', (
        '绝对朝向直接设定；未模拟加速度',
        '正速度沿车头前进，负速度倒退',
        'now_s 是本地单调时钟读数',
        'dt_s 是本轮模拟推进量',
    ), size=15)

    # One explicit bridge across processes, rather than shared objects or 1:1 ticks.
    card(50, 1020, 900, 110, '跨进程遥测：车端 state → 桌面 latest.state', (
        '车端推进后按期限采样；send_telemetry() → UDP 10000 → 桌面解码',
        '遥测目标周期0.05 s，不对应每个仿真步；两个进程不共享Python对象',
    ), fill='#f7f9fc', size=16)
    flow([(500, 980), (500, 1020)], color=MUTED,
         label='按遥测期限采样', label_at=(525, 1006), limit=400)

    # Stage 2: per-packet updates, persistent caches, post-loop sidebar and Qt queue.
    panel(20, 1170, 960, 830, '② 桌面：逐包保存世界数据，再请求Qt重绘', '#f6fafc', '#9aafbf')
    text(50, 1232, '桌面定时轮询自己的Socket；车端不会直接调用桌面函数。', 15, color=MUTED, max_width=900)
    card(50, 1250, 900, 105, 'poll_telemetry()', (
        '约20ms触发；每轮最多8次接收尝试，遇None提前结束本轮收包',
        '合法结果 received = (latest, peer)；每个合法包都进入下方保存步骤',
    ), code_header=True, size=16)
    flow([(500, 1130), (500, 1170)], color=MUTED,
         label='由桌面轮询取出', label_at=(525, 1156), limit=400)
    rect(50, 1395, 470, 475, '#eef6fb', '#94adbf', radius=12)
    text(70, 1424, 'map.set_state(latest.state)', 18, True, code=True, max_width=430)
    text(70, 1454, '调用前先刷新 last_received_s = monotonic()', 15, color=MUTED, max_width=430)
    card(70, 1475, 430, 85, '① self.state = state', (
        '保存最新VehicleState，供绘制车头',
    ), code_header=True, size=15)
    card(70, 1600, 430, 105, '② history.record(state)', (
        '提取 (x_m, y_m)，保存世界坐标',
        '最多1000点；仅跳过相邻重复位置',
    ), code_header=True, size=15)
    card(70, 1745, 430, 85, '③ self.update()', (
        '只请求重绘，不传递状态或轨迹',
    ), code_header=True, size=15)
    text(70, 1854, '三步更新同一个map对象；方法返回None。', 15, color=MUTED, max_width=430)
    flow([(285, 1355), (285, 1395)], label='每个合法包', label_at=(302, 1381))
    flow([(285, 1560), (285, 1600)], label='随后记录轨迹', label_at=(302, 1586), limit=205)
    flow([(285, 1705), (285, 1745)], label='然后请求重绘', label_at=(302, 1731), limit=205)
    card(560, 1395, 375, 123, '无包／坏包／过期', (
        'None：结束本轮，保留旧数据',
        'ValueError：跳过，继续尝试',
        '已有合法记录，1s无新包才过期',
    ), fill='#f8fafc', size=15)
    flow([(755, 1355), (755, 1395)], color=MUTED,
         label='无包／坏包', label_at=(775, 1381), limit=170)
    card(560, 1705, 375, 170, '循环结束后：侧栏与状态', (
        'latest：本轮最后一个合法快照',
        '有新快照才 values.setText(...)',
        '侧栏还显示故障位与来源 peer',
        '首次没收到合法包：等待遥测',
    ), fill='#f8fafc', size=15)
    card(560, 1560, 375, 105, '已保存的两份世界数据', (
        'map.state：最新完整车辆状态',
        'map.history：世界坐标历史',
    ), fill='#e7f5f1', stroke='#7eada8', size=15)
    flow([(500, 1517), (532, 1517), (532, 1610), (560, 1610)], color=MUTED)
    flow([(500, 1652), (540, 1652), (540, 1640), (560, 1640)], color=MUTED)
    card(50, 1910, 900, 70, 'Qt事件队列：稍后调度 paintEvent()', (
        '多个update请求可能合并；窗口尺寸变化也能触发重绘',
    ), fill='#e5f4f0', stroke='#7eada8', size=16)
    flow([(70, 1787), (58, 1787), (58, 1890), (500, 1890), (500, 1910)], color=TEAL, dashed=True)

    # Stage 3: paint reads caches; the helper is a nested call expansion, not a new tick.
    panel(20, 2040, 960, 1180, '③ 画布：读取世界数据，按当前尺寸换算并绘制', '#f2faf9', '#7eada8')
    card(50, 2110, 900, 110, 'paintEvent()', (
        '读取 map.state 与 history.points()；绘制本帧，不修改世界状态',
        'state为None：画网格与等待提示后返回；有状态：继续以下绘制',
    ), code_header=True, stroke='#7eada8', size=16)
    flow([(50, 1945), (32, 1945), (32, 2165), (50, 2165)], color=TEAL, dashed=True)
    flow([(935, 1612), (955, 1612), (955, 2165), (950, 2165)], color=MUTED)
    rect(50, 2260, 900, 230, '#ffffff', '#7eada8', radius=12)
    text(70, 2289, '共用坐标助手：screen_point(x_m, y_m) → QPointF', 18, True, max_width=860)
    text(70, 2318, '轨迹点与车辆位置分别调用；读取地图中心和pixels_per_meter = 50。', 15, color=MUTED, max_width=860)
    card(70, 2340, 860, 100, '内部调用 world_to_screen(...) 执行公式', (
        'screen_x_px = origin_x_px + x_m × pixels_per_meter',
        'screen_y_px = origin_y_px − y_m × pixels_per_meter',
    ), stroke='#7eada8', body_code=True, size=20)
    text(70, 2470, '原点=(width//2,height//2)；二元组返回后，screen_point再包装成QPointF。', 15, color=TEAL, max_width=860)

    # Reused numerical coordinate example, with visibly distinct canvas/world origins.
    rect(50, 2530, 440, 385, '#ffffff', '#7eada8')
    text(66, 2558, '示例：地图800×600，比例50 px/m', 18, True, max_width=408)
    x0, y0, cw, ch = 84, 2610, 304, 228
    rect(x0, y0, cw, ch, '#f8fafc', '#b7c7d2', radius=0, width=1)
    line([(x0, y0), (x0+cw+27, y0)], color='#718096')
    line([(x0, y0), (x0, y0+ch+12)], color='#718096')
    text(x0, y0-12, '画布原点(0,0)', 15, color=MUTED)
    text(x0+cw+34, y0+6, '+X', 15, color=MUTED)
    text(x0+8, y0+ch+25, '+Y向下', 15, color=MUTED)
    cx, cy = x0+cw/2, y0+ch/2
    dot(cx, cy, BLUE, 5)
    spx, spy = cx+100*.38, cy-50*.38
    dot(spx, spy, TEAL, 5)
    line([(spx, spy), (spx+25, spy-30), (442, spy-30)], color=TEAL, arrow=False, width=1.2)
    text(442, spy-38, '(500,250) px', 15, color=TEAL, anchor='end')
    line([(cx, cy), (cx-37, cy+52), (106, cy+52)], color=BLUE, arrow=False, width=1.2)
    text(106, cy+73, '世界(0,0) m', 15, color=BLUE)
    text(106, cy+96, '对应画布(400,300) px', 15, color=BLUE)
    text(66, 2894, '世界(2,1) m → 画布(500,250) px', 16, color=TEAL, max_width=408)

    # The paint order bypasses the explanatory helper box via the right gutter.
    card(530, 2530, 420, 120, 'drawPolyline(path)', (
        '≥2点：逐个screen_point(...)',
        '转换结果组成path，再绘制折线',
        '不足2点：跳过轨迹，继续画车',
    ), code_header=True, stroke='#7eada8', fill='#e5f4f0', size=15)
    card(530, 2690, 420, 100, 'translate(...)', (
        '车辆位置 → screen_point(x_m,y_m)',
        '把局部原点移到该画布位置',
    ), code_header=True, stroke='#7eada8', fill='#e5f4f0', size=15)
    card(530, 2830, 420, 100, 'rotate(...)', (
        '参数：-degrees(heading_rad)',
        '只改变后续车头绘制的朝向',
    ), code_header=True, stroke='#7eada8', fill='#e5f4f0', size=15)
    card(530, 2970, 420, 100, 'drawPolygon(...)', (
        '按局部坐标画车头三角形',
        '默认朝+X；不修改世界数据',
    ), code_header=True, stroke='#7eada8', fill='#e5f4f0', size=15)
    flow([(500, 2220), (500, 2240), (968, 2240), (968, 2510), (740, 2510), (740, 2530)],
         label='实际绘制顺序', label_at=(768, 2516), limit=180)
    for bottom, top, caption in [(2650,2690,'轨迹之后画车'),(2790,2830,'再旋转局部坐标'),(2930,2970,'最后画车头')]:
        assert top-bottom == FLOW_GAP
        flow([(740,bottom),(740,top)], label=caption, label_at=(762,top-14), limit=184)
    card(50, 2970, 440, 100, '窗口尺寸变化时', (
        '中心变化 → 重新换算像素位置',
        '世界state/history与比例50保持',
    ), stroke='#7eada8', size=15)
    card(50, 3110, 900, 80, '两条时间线：收包更新缓存；Qt读取缓存绘制', (
        '逐包更新状态，轨迹按规则记录；不保证每包一帧，重绘不推进运动。',
    ), stroke='#7eada8', size=16)
    text(30, 3252, '状态在车端推进；桌面保留世界数据；像素坐标只用于本次显示。', 16, color=MUTED, max_width=940)

    # Draw connectors after all backgrounds, so cross-panel paths remain continuous.
    for index,(points,color,dashed,label,where,limit) in enumerate(flows):
        raw(f'<g class="flow-connector" data-index="{index}" data-kind="{"event" if dashed else "data" if color==MUTED else "call"}">')
        line(points,color=color,dashed=dashed)
        raw('</g>')
    for points,color,dashed,label,where,limit in flows:
        if label:
            flow_label(*where,label,color,limit)
    raw('</svg>')
    return '\n'.join(parts)+'\n'


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    # The Windows offscreen plugin may have an empty system font database.
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    svg = build_svg()
    chains = [
        ['本轮输入', 'target_for_tick(...)', 'apply_target(state, target)',
         'advance(controlled_state, dt_s)', '调用方保存返回值'],
        ['① self.state = state', '② history.record(state)', '③ self.update()'],
        ['drawPolyline(path)', 'translate(...)', 'rotate(...)', 'drawPolygon(...)'],
    ]
    by_header = {item['header']: item for item in cards}
    main_gaps = []
    for chain in chains:
        for before, after in zip(chain, chain[1:]):
            a, b = by_header[before], by_header[after]
            gap = b['y'] - (a['y'] + a['height'])
            if gap != FLOW_GAP:
                raise ValueError(f'Unbalanced main flow: {before} -> {after}: {gap}')
            main_gaps.append(gap)
    svg_path = ROOT / f"{STEM}.svg"
    svg_path.write_bytes(svg.encode("utf-8"))
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor("#ffffff"))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE))
    painter.end()
    png_path = ROOT / f"{STEM}.png"
    if not png.save(str(png_path)):
        raise RuntimeError("PNG export failed")
    result = {
        "svg": str(svg_path), "png": str(png_path),
        "width": WIDTH, "height": HEIGHT, "png_scale": PNG_SCALE, "flow_gap": FLOW_GAP,
        "cards": len(cards), "checked_labels": len(text_checks), "main_flow_gaps": main_gaps,
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
