"""Render the editable encapsulation diagram and its high-resolution PNG.

Edit LABELS, FIELDS and the layout constants below when names or layout change.
This documentation renderer does not execute or modify the application code.
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
STEM = "06-encapsulation"
WIDTH, HEIGHT = 1400, 1570
PNG_SCALE = 3
AS_OF = "2026-11-16"
ROW_Y = (300, 520, 740, 960)
BAR_HEIGHT = 100
ARROW_X = 920

# Displayed code names and text are kept together for later renaming.
LABELS = {
    "title": "上层定义消息，下层负责承载：我们的数据怎样逐层封装？",
    "subtitle": "管理JSON编解码、TCP记录编解码与增量解析已通过测试，实际TCP收发待接入。",
    "encoder": "encode_open_session_request(request)",
    "decoder": "decode_open_session_request(payload)",
    "record_encoder": "encode_record(payload)",
    "length": "N = len(payload)",
    "tcp_receive": "一次recv()可得到半条或多条应用记录。",
    "udp_send": "sendto：一条JSON → 一份数据报",
    "udp_receive": "recvfrom：返回正文（接收容量足够）",
}

# x / width show nesting, not actual byte-size ratios.
FIELDS = {
    "ethernet": (235, 150, "以太网头", "MAC等", "#edf1f7", "#66798f"),
    "ip": (385, 130, "IPv4头", "系统添加", "#fff3dc", "#a37820"),
    "tcp": (515, 150, "TCP头", "系统添加", "#e5f4f0", "#087f70"),
    "length": (665, 170, "应用长度N", "4字节·大端", "#e9efff", draw.BLUE),
    "body": (835, 290, "JSON正文", "payload：N字节", "#f6f8fc", "#7890ad"),
    "fcs": (1125, 70, "FCS", "尾", "#edf1f7", "#66798f"),
}


def label(x, y, value, *, size=23, bold=False, color=draw.INK,
          code=False, width=1300, anchor="start"):
    draw.text(x, y, value, size=size, bold=bold, color=color,
              code=code, max_width=width, anchor=anchor)


def field(y, key):
    x, width, title, detail, fill, stroke = FIELDS[key]
    draw.rect(x, y, width, BAR_HEIGHT, fill, stroke, radius=0, width=1.8)
    label(x + width / 2, y + 40, title, size=23, bold=True,
          width=width - 12, anchor="middle")
    label(x + width / 2, y + 77, detail, size=20, color=draw.MUTED,
          width=width - 12, anchor="middle")


def side_card(x, y, width, title, detail, note, color, fill):
    draw.rect(x, y - 24, width, 148, fill, color, width=1.8)
    for offset, value, bold in ((14, title, True), (55, detail, False), (94, note, False)):
        label(x + width / 2, y + offset, value, size=21, bold=bold,
              color=color if bold else draw.MUTED,
              width=width - 16, anchor="middle")


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">应用记录边界与网络逐层封装</title>')
    draw.raw('<desc id="description">参考课堂HTTP、TCP、IPv4和以太网的封装示意，'
             '以本项目管理JSON代替HTTP。应用长度前缀位于TCP的Data内，不是TCP首部或L2帧头。'
             '图示一条短消息装入一段的逻辑例子，实际可跨段或合并；本机回环不走真实以太网。'
             'UDP保留数据报边界，本项目不额外加应用长度前缀。TCP应用记录编解码与增量解析均已验证，实际收发待接入。</desc>')
    draw.rect(0, 0, WIDTH, HEIGHT, "#ffffff", "#ffffff", radius=0, width=0)
    label(28, 48, LABELS["title"], size=32, bold=True, width=1344)
    label(28, 88, LABELS["subtitle"], size=22, color=draw.MUTED, width=1344)

    draw.rect(24, 112, 1352, 100, "#f5f8ff", "#9cb2d2", radius=12)
    label(44, 151, f'管理对象 → {LABELS["encoder"]} → payload（JSON UTF-8字节）',
          size=23, width=1312)
    label(44, 190, "长度小例：正文b'hello'为5字节 → 前缀为00 00 00 05 → 应用记录共9字节",
          size=22, color=draw.BLUE, width=1312)

    label(24, 253, "谁负责", size=23, bold=True, width=185)
    label(235, 253, "同色字段：上层字节在下层数据区域中继续被承载", size=23,
          bold=True, width=940)
    label(1215, 253, "PDU／记录", size=21, bold=True, width=160)

    role_rows = (
        ("应用协议", "Python代码", "TCP/IP应用层", draw.BLUE, "#f4f7ff"),
        ("传输层L4", "系统TCP栈", "提供字节流", "#087f70", "#f0f9f6"),
        ("网络层L3", "系统IP栈", "负责IP承载", "#a37820", "#fff9ef"),
        ("链路层L2", "驱动／网卡", "Ethernet示例", "#66798f", "#f5f7fa"),
    )
    pdus = (
        ("应用记录", "Record", "自定消息格式"),
        ("TCP段", "Segment", "含TCP头"),
        ("IP包", "Packet", "含IP头"),
        ("以太网帧", "L2 Frame", "含链路头尾"),
    )
    row_fields = (
        ("length", "body"),
        ("tcp", "length", "body"),
        ("ip", "tcp", "length", "body"),
        ("ethernet", "ip", "tcp", "length", "body", "fcs"),
    )
    for index, y in enumerate(ROW_Y):
        title, detail, note, color, fill = role_rows[index]
        side_card(24, y, 185, title, detail, note, color, fill)
        side_card(1215, y, 160, *pdus[index], color, fill)
        for key in row_fields[index]:
            field(y, key)
        draw.raw(f'<g class="layer-marker" data-row="{index}" data-y="{y}"/>')

    label(235, 319, "应用自行约定消息边界", size=23, bold=True, width=410)
    label(235, 359, LABELS["length"], size=23, code=True, color=draw.BLUE, width=410)
    label(235, 399, "长度不含前缀自身4字节", size=21, color=draw.MUTED, width=410)
    label(665, 283, LABELS["record_encoder"], size=23, code=True, color=draw.BLUE, width=460)

    # Each connector has the same vertical span, and its label stays beside it.
    for index in range(3):
        start_y = ROW_Y[index] + BAR_HEIGHT
        end_y = ROW_Y[index + 1]
        draw.line([(ARROW_X, start_y), (ARROW_X, end_y)], color="#58718f", width=2.3)
        label(955, (start_y + end_y) / 2 + 8, "交给下层承载", size=22,
              color=draw.MUTED, width=240)

    for x, y in ((675, 500), (525, 720), (395, 940)):
        label(x, y, "Data / Payload", size=21, color=draw.MUTED, width=230)

    label(24, 1119, "逻辑封装示例：这里画一条短消息；实际可跨多个TCP段，多条消息也可同批收到。",
          size=22, color=draw.MUTED, width=1352)
    label(24, 1155, "本机127.0.0.1回环不经过真实以太网；图中链路层用于理解以太网跨机通信。",
          size=22, color=draw.MUTED, width=1352)

    draw.panel(24, 1190, 660, 264, "UDP对照：已有数据报边界", "#f0f9f8", "#7eada8")
    draw.rect(48, 1252, 164, 72, "#e5f4f0", "#087f70", radius=0)
    draw.rect(212, 1252, 448, 72, "#f6f8fc", "#7890ad", radius=0)
    label(130, 1296, "系统UDP头", size=22, bold=True, width=148, anchor="middle")
    label(436, 1296, "JSON正文", size=23, bold=True, width=432, anchor="middle")
    label(48, 1361, LABELS["udp_send"], size=21, width=612)
    label(48, 1399, LABELS["udp_receive"], size=21, width=612)
    label(48, 1435, "本项目不额外加应用长度前缀。", size=21, color=draw.MUTED, width=612)

    draw.panel(704, 1190, 672, 264, "TCP接收：先累计头，再累计正文", "#f5f8ff", "#8ba5cc")
    label(728, 1268, "累计4字节头 → 读出N → 累计N字节正文", size=21, width=624)
    label(728, 1310, LABELS["tcp_receive"], size=21, width=624)
    label(728, 1352, LABELS["decoder"], size=21, code=True, width=624)
    label(728, 1394, "系统处理TCP/IP头，Python处理应用记录。", size=21, width=624)
    label(728, 1435, "长度前缀也可能分次到达；应用记录≠L2帧。", size=21, color=draw.MUTED, width=624)

    label(28, 1495, "参考老师课堂封装示意，以本项目管理消息替代HTTP；四字节前缀是项目约定。",
          size=20, color=draw.MUTED, width=1344)
    label(28, 1532, f"字段宽度仅供阅读，不表示真实字节比例；文字、函数名和布局可在生成源中修改。  {AS_OF}",
          size=20, color=draw.MUTED, width=1344)
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    if not QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    svg = build_svg()
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE,
                 QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor("#ffffff"))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE))
    painter.end()
    (ROOT / f"{STEM}.svg").write_bytes(svg.encode("utf-8"))
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")
    result = {"as_of": AS_OF, "width": WIDTH, "height": HEIGHT,
              "png_scale": PNG_SCALE, "rows": len(ROW_Y),
              "arrow_gap": ROW_Y[1] - ROW_Y[0] - BAR_HEIGHT,
              "checked_labels": len(draw.text_checks), "overflow": []}
    if len(os.sys.argv) > 1:
        qa = Path(os.sys.argv[1])
        qa.mkdir(parents=True, exist_ok=True)
        (qa / "labels-and-layout.json").write_bytes((json.dumps(
            {"result": result, "text_checks": draw.text_checks,
             "fields": FIELDS, "row_y": ROW_Y}, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        png.scaledToWidth(880, Qt.TransformationMode.SmoothTransformation).save(str(qa / "readme-width-880.png"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
