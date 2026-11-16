"""Render the TCP record receive diagram as SVG and PNG."""
from __future__ import annotations

import json
import os
from pathlib import Path

import render_vehicle_motion as draw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent
STEM = "07-tcp-record-receive"
WIDTH, HEIGHT = 1400, 1272
PNG_SCALE = 3
MARGIN = 32

SOCKET_BLUE = "#2563b8"
BUFFER_TEAL = "#08796a"
OUTPUT_VIOLET = "#65469b"
TIME_ORANGE = "#98631c"
BORDER = "#c8d6e5"

TRACE_COLUMNS = (32, 222, 478, 858, 1113, 1368)
TRACE_ROWS = (
    ("10.00 s", "00 00 00", "00 00 00", "[]", "10.20 s"),
    ("10.08 s", "05 + b'he'", "00 00 00 05 | b'he'", "[]", "10.20 s"),
    ("10.16 s", "b'llo'", "空", "[b'hello']", "None"),
)


def label(x, y, value, *, size=22, bold=False, color=draw.INK, code=False, width=1336):
    draw.text(x, y, value, size=size, bold=bold, color=color, code=code, max_width=width)


def draw_flow():
    draw.rect(MARGIN, 125, 1336, 99, "#f5f8fc", BORDER, radius=14, width=1.6)
    label(54, 173, "应用调用者", size=25, bold=True, width=220)
    label(240, 163, "records = iter_records(sock, record_timeout_s=0.20)", code=True, width=1098)
    label(240, 198, "payload = next(records)", code=True, color=OUTPUT_VIOLET, width=1098)
    label(628, 198, "首次 next() 在迭代器内创建 TcpRecordBuffer()", size=20,
          color=draw.MUTED, width=710)
    draw.line(((66, 227), (66, 258)), color=SOCKET_BLUE, width=2.2)

    draw.rect(MARGIN, 266, 824, 120, "#f1f6ff", SOCKET_BLUE, radius=14, width=1.6)
    label(54, 300, "iter_records() 中读取字节", size=23, bold=True, color=SOCKET_BLUE, width=780)
    label(54, 335, "chunk = _recv_chunk(sock, deadline_s)", code=True, width=780)
    label(54, 366, "sock.recv(TCP_RECEIVE_CHUNK_BYTES) → chunk: bytes", size=20,
          code=True, color=draw.MUTED, width=780)
    draw.line(((66, 390), (66, 429)), color=SOCKET_BLUE, width=2.2)
    label(88, 416, "非空 chunk → 检查 received_at_s 是否已到 deadline_s", size=20,
          color=SOCKET_BLUE, width=756)

    draw.rect(MARGIN, 437, 824, 241, "#eef8f5", BUFFER_TEAL, radius=14, width=1.6)
    label(54, 472, "payloads = buffer.feed(chunk)", size=24, bold=True,
          color=BUFFER_TEAL, code=True, width=780)
    label(54, 511, "chunk 追加到 self._buffer。收齐前 4 字节作为 header。", width=780)
    label(54, 548, "header → length → _validate_payload_length(length) 校验 1–4096",
          size=20, width=780)
    label(54, 583, "凑齐 record_size = 4 + length 个字节后切出 record", width=780)
    label(54, 618, "decode_record(record) → 一个 payload: bytes → 加入 payloads", width=780)
    label(54, 653, "移除已取出的记录并接着检查下一条。未收齐的字节留在缓冲区。", size=21, width=780)
    draw.line(((66, 682), (66, 721)), color=BUFFER_TEAL, width=2.2)
    label(88, 709, "返回 payloads: list[bytes]。另查 buffer.has_partial_record。", size=20,
          color=BUFFER_TEAL, width=756)

    draw.rect(MARGIN, 729, 824, 145, "#f5f2fb", OUTPUT_VIOLET, radius=14, width=1.6)
    label(54, 766, "更新 deadline_s，再执行 yield from payloads", size=24, bold=True,
          color=OUTPUT_VIOLET, width=780)
    label(54, 800, "缓冲区空 → None　同一条未齐 → 保留原期限　新记录未齐 → 起算", size=20, width=780)
    label(54, 833, "列表为空时，本次 next() 继续读。列表有值时，交付一个 payload: bytes。", size=20, width=780)
    label(54, 858, "同批有多条正文时，后续 next() 接着从列表里取。", size=19,
          color=draw.MUTED, width=780)

    draw.line(((29, 832), (11, 832), (11, 198), (27, 198)), color=OUTPUT_VIOLET, width=2)
    draw.text(30, 674, "payload: bytes", size=16, color=OUTPUT_VIOLET,
              code=True, rotate=-90, max_width=170)

    draw.line(((862, 769), (889, 769), (889, 338), (861, 338)),
              color=TIME_ORANGE, dashed=True, width=2)
    draw.text(910, 663, "更新后的 deadline_s", size=17, color=TIME_ORANGE,
              rotate=-90, max_width=235)


def draw_trace():
    label(MARGIN, 925, "用同一次 next(records) 走一遍三次读取", size=28, bold=True)
    table_top = 948
    header_height = 48
    row_height = 60
    table_width = TRACE_COLUMNS[-1] - TRACE_COLUMNS[0]
    draw.rect(MARGIN, table_top, table_width, header_height, "#e8eff9", BORDER, radius=0)
    headings = ("读取时刻", "recv() 返回的 chunk", "feed() 后的缓冲区", "返回的 payloads", "更新后的 deadline_s")
    for index, heading in enumerate(headings):
        x, right = TRACE_COLUMNS[index:index + 2]
        label(x + 18, table_top + 31, heading, size=21, bold=True, width=right - x - 36)

    for row_index, values in enumerate(TRACE_ROWS):
        y = table_top + header_height + row_index * row_height
        fill = ("#ffffff", "#f7faff", "#eef8f5")[row_index]
        draw.rect(MARGIN, y, table_width, row_height, fill, BORDER, radius=0)
        for column, value in enumerate(values):
            x, right = TRACE_COLUMNS[column:column + 2]
            code = value != "空"
            color = BUFFER_TEAL if column == 3 and row_index == 2 else draw.INK
            label(x + 18, y + 39, value, size=22, bold=column == 3, color=color,
                  code=code, width=right - x - 36)

    table_bottom = table_top + header_height + len(TRACE_ROWS) * row_height
    for x in TRACE_COLUMNS[1:-1]:
        draw.line(((x, table_top), (x, table_bottom)), color=BORDER, arrow=False, width=1)

    label(MARGIN, 1210, "第三次收齐时，next(records) 返回 b'hello'。缓冲区已空，deadline_s 设为 None。",
          size=22, color=BUFFER_TEAL, bold=True)


def draw_boundaries():
    draw.line(((862, 310), (923, 310)), color=SOCKET_BLUE, width=2)
    draw.rect(930, 266, 438, 151, "#f5f8ff", "#aebdd6", radius=14, width=1.6)
    label(952, 303, 'chunk == b"" → EOF', size=23, bold=True, code=True,
          color=SOCKET_BLUE, width=394)
    label(952, 340, "buffer.finish()", code=True, width=394)
    label(952, 373, "缓冲区空 → 迭代正常结束", size=20, width=394)
    label(952, 400, "缓冲区有残留 → ValueError", size=20, width=394)

    draw.rect(930, 437, 438, 437, "#fffbf3", "#dcc392", radius=14, width=1.6)
    label(952, 474, "装配期限怎样参与读取", size=25, bold=True, color=TIME_ORANGE, width=394)
    label(952, 510, "received_at_s = monotonic()", size=21, code=True, width=394)
    label(952, 546, "deadline_s = received_at_s", size=21, code=True, width=394)
    label(952, 578, "    + record_timeout_s", size=21, code=True, width=394)
    label(952, 616, "10.00 s + 0.20 s = 10.20 s", size=21, code=True,
          color=TIME_ORANGE, width=394)
    label(952, 657, "remaining_s = deadline_s", size=21, code=True, width=394)
    label(952, 686, "    - monotonic()", size=21, code=True, width=394)
    label(952, 720, "10.20 s − 10.08 s = 0.12 s", size=21, code=True, color=TIME_ORANGE, width=394)
    label(952, 752, "原超时 1.0 s，本次最多等 0.12 s。", size=19, width=394)
    label(952, 781, "读完恢复原 Socket 超时设置。", size=19, width=394)
    label(952, 809, "读前已到期限，或读到的字节晚到", size=19, width=394)
    label(952, 836, "→ TimeoutError", size=21, code=True, color=TIME_ORANGE, width=394)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">TCP 接收端怎样拼出一条记录</title>')
    draw.raw('<desc id="description">用分三次到达的 hello 应用记录，展示 iter_records、'
             '_recv_chunk、feed、decode_record、逐条交付，以及装配期限与 EOF 的处理。'
             'TCP 每次读取的实际分块可以不同。</desc>')
    draw.rect(0, 0, WIDTH, HEIGHT, "#ffffff", "#ffffff", radius=0, width=0)
    label(MARGIN, 57, "TCP 接收端怎样拼出一条记录？", size=36, bold=True)
    label(MARGIN, 98, "同一连接复用 records。沿着实参、返回值和截止时刻看接收循环。",
          size=23, color=draw.MUTED)
    draw_flow()
    draw_trace()
    draw_boundaries()
    label(MARGIN, 1247, "hello 用来演示分块。完整的管理正文可继续交给 decode_open_session_request(payload) 解码。",
          size=21, color=draw.MUTED)
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
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE, QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor("#ffffff"))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, png.width(), png.height()))
    painter.end()

    (ROOT / f"{STEM}.svg").write_bytes(svg.encode("utf-8"))
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")

    result = {"width": WIDTH, "height": HEIGHT, "png_scale": PNG_SCALE,
              "checked_labels": len(draw.text_checks), "overflow": []}
    if len(os.sys.argv) > 1:
        preview = Path(os.sys.argv[1])
        preview.mkdir(parents=True, exist_ok=True)
        png.scaledToWidth(880, Qt.TransformationMode.SmoothTransformation).save(
            str(preview / "readme-width-880.png")
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
