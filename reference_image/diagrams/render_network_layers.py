"""Render the project's network-layer map using the existing diagram style.

Update AS_OF, LAYERS and the channel notes when implementation changes.
The SVG remains editable; README links to the SVG and its high-resolution PNG.
This script reuses the motion diagram's drawing helpers without rebuilding it.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import render_vehicle_motion as draw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
STEM = "05-network-layers"
WIDTH, HEIGHT = 1000, 1830
PNG_SCALE = 3
AS_OF = "2026-11-12"
ROW_TOP, ROW_HEIGHT, ROW_GAP = 186, 148, 16

# These are functional comparisons, not seven separately implemented protocols.
# title, status, code line, explanation, boundary
LAYERS = [
    ("L7", "应用层", "Application", "自定义控制与遥测消息", "已使用",
     "control_target / vehicle_state；version / type",
     "速度、朝向、位置、保护标志；字段及数值校验。",
     "控制保护由应用决定；目前还没有认证管理通道。"),
    ("L6", "表示层", "Presentation", "数据表示：JSON ＋ UTF-8", "已使用",
     "encode_target() / decode_target()；遥测也有编解码",
     "对象 ↔ JSON文本 ↔ bytes；由应用代码完成转换。",
     "这是表示功能的对照；画布坐标换算属于GUI。"),
    ("L5", "会话层", "Session", "会话与控制准入：独立逻辑已验证", "开发中",
     "ControlCommand(session_id, seq, target)",
     "update_command_guard()：使能、会话、序号与超时已单测。",
     "实际收发仍用旧消息；认证／会话管理与有效窗口待接入。"),
    ("L4", "传输层", "Transport", "UDP：控制与遥测各用一个通道", "已使用",
     "SOCK_DGRAM；sendto() / recvfrom()",
     "控制 → 9999；遥测 → 10000；系统处理UDP头部。",
     "UDP不保证送达、顺序或去重；TCP管理通道待接入。"),
    ("L3", "网络层", "Network", "IPv4：当前使用本机回环", "系统提供",
     "AF_INET；127.0.0.1",
     "操作系统负责IP处理；当前报文只在本机流转。",
     "局域网双机连接与实际地址配置，仍需验证。"),
    ("L2", "数据链路层", "Data link", "真实网卡链路：当前回环不经过", "跨机待验",
     "后续按实际环境记录：Ethernet 或 Wi-Fi",
     "由系统、驱动与网卡处理链路，不在Python里造帧。",
     "目前不能把本机演示记为以太网／Wi-Fi实测。"),
    ("L1", "物理层", "Physical", "真实网络介质：当前回环不经过", "跨机待验",
     "有线链路的电／光信号，或Wi-Fi无线信号",
     "以后双机运行时由所用硬件和网络介质承载。",
     "不要求自制小车硬件，也不要求自行实现物理层。"),
]

STATUS = {
    "已使用": ("#e5f4f0", "#087f70"),
    "开发中": ("#fff0d5", "#946000"),
    "系统提供": ("#e9efff", "#2563b8"),
    "跨机待验": ("#f0f3f7", "#596b80"),
}


def label(x, y, value, *, code=False, bold=False, size=17,
          color=draw.INK, width=580, anchor="start"):
    draw.text(x, y, value, size=size, bold=bold, code=code,
              color=color, max_width=width, anchor=anchor)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">本项目的网络协议分层与实现状态</title>')
    draw.raw('<desc id="description">OSI七层用于功能对照；本项目的表示与会话功能放在TCP/IP应用代码中。'
             '当前使用自定义JSON、UTF-8、UDP、IPv4回环；控制信封编解码与准入已单测但尚未接入实际收发，TCP、TLS、HMAC和真实局域网验证待接入。'
             '底层由操作系统和硬件提供，高级验收后复用基础扩展专家控制权机制。</desc>')
    draw.rect(0, 0, WIDTH, HEIGHT, '#ffffff', '#ffffff', 0)
    label(30, 40, '我们用到了哪些网络层？哪些还在开发？', bold=True, size=25, width=940)
    label(30, 72, f'按源码核对：{AS_OF}；OSI是功能参照，不是开发进度条。', size=16, color=draw.MUTED, width=940)

    for i, (status, (fill, color)) in enumerate(STATUS.items()):
        x = 30 + 210 * i
        draw.rect(x, 92, 115, 32, fill, color, radius=7)
        label(x + 57.5, 114, status, size=15, bold=True, color=color,
              width=105, anchor='middle')
    label(20, 164, 'OSI七层', size=18, bold=True, width=130)
    label(170, 164, '本项目实际技术、代码入口与边界', size=18, bold=True, width=620)
    label(810, 164, 'TCP/IP对照', size=18, bold=True, width=170)

    for index, (level, chinese, english, title, status, code, detail, boundary) in enumerate(LAYERS):
        y = ROW_TOP + index * (ROW_HEIGHT + ROW_GAP)
        color = draw.BLUE if index < 3 else draw.TEAL if index < 5 else '#66798f'
        fill = '#f4f7ff' if index < 3 else '#f0f9f8' if index < 5 else '#f5f7fa'
        draw.raw(f'<g class="layer-row" data-level="{level}" data-status="{status}">')
        draw.rect(20, y, 130, ROW_HEIGHT, fill, color)
        label(85, y + 38, level, size=24, bold=True, color=color, width=110, anchor='middle')
        label(85, y + 79, chinese, size=18, bold=True, width=118, anchor='middle')
        label(85, y + 113, english, size=13, color=draw.MUTED, width=120, anchor='middle')
        draw.rect(170, y, 620, ROW_HEIGHT, '#ffffff', color)
        label(185, y + 30, title, size=18, bold=True, width=475)
        chip_fill, chip_color = STATUS[status]
        draw.rect(679, y + 10, 99, 28, chip_fill, 'none', radius=6)
        label(728.5, y + 30, status, size=14, bold=True, color=chip_color, width=91, anchor='middle')
        label(185, y + 64, code, code=True, size=15, color=draw.BLUE, width=590)
        label(185, y + 97, detail, size=16, width=590)
        label(185, y + 129, boundary, size=15, color=draw.MUTED, width=590)
        draw.raw('</g>')

    # Grouping is a teaching comparison; L5/L6 are functions within this app.
    groups = [
        (186, 476, '应用层', 'Application', [
            '自定义消息', '数据编解码', '会话与准入规则',
            '本项目在应用代码', '承担上面这些功能',
        ], draw.BLUE, '#f4f7ff'),
        (678, 148, '传输层', 'Transport', ['当前：UDP', '后续：TCP管理'], draw.TEAL, '#f0f9f8'),
        (842, 148, '网际层', 'Internet', ['当前：IPv4回环', '后续：双机IPv4'], draw.TEAL, '#f0f9f8'),
        (1006, 312, '网络接口层', 'Link', ['链路与物理介质', '由系统／硬件提供', '当前只做本机回环', '真实链路待跨机验'], '#66798f', '#f5f7fa'),
    ]
    for y, h, name, english, notes, color, fill in groups:
        draw.rect(810, y, 170, h, fill, color)
        label(895, y + 32, name, size=18, bold=True, width=156, anchor='middle')
        label(895, y + 61, english, size=14, color=color, width=156, anchor='middle')
        start = y + 97 if h <= 148 else y + 124
        for i, note in enumerate(notes):
            label(895, start + 31 * i, note, size=14, color=draw.MUTED, width=156, anchor='middle')

    draw.panel(20, 1350, 960, 192, '通道路线：当前已有，与后续计划分别看', '#f5f8ff', '#8ba5cc')
    label(40, 1417, '当前控制／遥测：自定义JSON + UTF-8 → UDP → IPv4（回环）', size=17, width=920)
    label(40, 1452, '后续管理通道：管理消息 → TLS → TCP → IPv4', size=17, width=920)
    label(40, 1487, '后续控制通道：带会话／序号／有效窗口并校验HMAC → UDP → IPv4', size=17, width=920)
    label(40, 1520, 'TLS只保护对应连接；HMAC用于真实性／完整性校验，不等于UDP加密。', size=15, color=draw.MUTED, width=920)

    draw.panel(20, 1566, 960, 194, '实现路线：高级完整验收 → 复用基础，扩展专家机制', '#f0f9f8', '#7eada8')
    label(40, 1631, '高级目标以教师标准为准：TLS等技术落实、完整功能与实验证据。', size=17, width=920)
    label(40, 1666, '专家扩展：在同一车端裁决上增加控制权交接、授权代次与租约。', size=17, width=920)
    label(40, 1701, '提前预留授权边界；新增必填字段或不兼容语义时，明确升级协议。', size=16, width=920)
    label(40, 1735, '这不是从某个OSI层升级到更高层；专家认定仍需老师确认与实验证据。', size=15, color=draw.MUTED, width=920)
    label(30, 1800, '维护时同步：实现状态、报文类型、代码入口、通道与README；未完成的能力不标为已使用。', size=15, color=draw.MUTED, width=940)
    draw.raw('</svg>')
    return '\n'.join(draw.parts) + '\n'


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    if not QFontDatabase.families():
        fonts = Path(os.environ.get('WINDIR', r'C:\Windows')) / 'Fonts'
        for filename in ('msyh.ttc', 'msyhbd.ttc', 'consola.ttf', 'consolab.ttf'):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    svg = build_svg()
    renderer = QSvgRenderer(svg.encode('utf-8'))
    if not renderer.isValid():
        raise RuntimeError('Invalid SVG')
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor('#ffffff'))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE))
    painter.end()
    (ROOT / f'{STEM}.svg').write_bytes(svg.encode('utf-8'))
    if not png.save(str(ROOT / f'{STEM}.png')):
        raise RuntimeError('PNG export failed')
    result = {'as_of': AS_OF, 'width': WIDTH, 'height': HEIGHT, 'png_scale': PNG_SCALE,
              'layers': len(LAYERS), 'row_gap': ROW_GAP,
              'checked_labels': len(draw.text_checks), 'overflow': []}
    if len(os.sys.argv) > 1:
        qa = Path(os.sys.argv[1])
        qa.mkdir(parents=True, exist_ok=True)
        (qa / 'labels-and-cards.json').write_bytes((json.dumps(
            {'result': result, 'text_checks': draw.text_checks, 'cards': []},
            ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        png.scaledToWidth(880, Qt.TransformationMode.SmoothTransformation).save(str(qa / 'readme-width-880.png'))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
