"""Render the secure rover's function and thread graph from the 74dbee0 baseline."""

import os
from pathlib import Path
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

import render_vehicle_motion as draw


ROOT = Path(__file__).resolve().parent
STEM = "10-advanced-runtime"
WIDTH, HEIGHT = 3000, 2320
PNG_SCALE = 2

PAPER = "#f4f7fb"
WHITE = "#ffffff"
INK = "#192b44"
MUTED = "#4b5f76"
EDGE = "#d3dce8"
BLUE = "#1e62a7"
RUST = "#a65325"
TEAL = "#0b7468"
PURPLE = "#644e95"
RED = "#9a4050"
TINT = {
    INK: "#edf2f8",
    BLUE: "#edf5ff",
    RUST: "#fdf2e9",
    TEAL: "#e9f7f3",
    PURPLE: "#f1edfa",
    RED: "#fbedf0",
}


def text(x, y, value, *, size=21, color=INK, code=False, bold=False,
         width=None, anchor="start"):
    draw.text(x, y, value, size=size, color=color, code=code, bold=bold,
              max_width=width, anchor=anchor)


def line(*points, color=INK, dashed=False, arrow=True, width=3.3):
    draw.line(points, color=color, dashed=dashed, arrow=arrow, width=width)


def node(x, y, w, label, color=INK, *, note="", code=True, size=24, fill=None):
    height = 67 if note else 48
    draw.rect(x, y, w, height, fill or TINT[color], color, radius=9, width=1.9)
    text(x + 13, y + 33, label, code=code, color=color, size=size,
         bold=True, width=w - 26)
    if note:
        text(x + 13, y + 56, note, color=MUTED, size=18, width=w - 26)


def rowset(x, y, w, title, rows, color, *, column_widths=None, size=20):
    columns = len(column_widths) if column_widths else 1
    line_count = (len(rows) + columns - 1) // columns
    height = 49 + line_count * 29
    draw.rect(x, y, w, height, WHITE, color, radius=8, width=1.7)
    draw.rect(x + 1, y + 1, w - 2, 42, TINT[color], "none", radius=8)
    text(x + 13, y + 30, title, color=color, bold=True, size=21, width=w - 26)
    column_widths = column_widths or (w - 26,)
    for index, label in enumerate(rows):
        column, row = index // line_count, index % line_count
        text(x + 13 + sum(column_widths[:column]), y + 65 + row * 29, label,
             color=INK, code=True, size=size, width=column_widths[column] - 9)


def label(x, y, value, color=INK, *, size=18, code=False, width=280):
    text(x, y, value, color=color, size=size, code=code, width=width)


def link(x, y, w, value, anchor, *, size=20):
    text(x, y, value, color=BLUE, size=size, bold=True, width=w)
    url = f"../../docs/runtime-reference.md#{anchor}"
    draw.raw(
        f'<a href="{url}" xlink:href="{url}" aria-label="{value}">'
        f'<rect x="{x-2}" y="{y-size-4}" width="{w}" height="{size+12}" '
        'fill="#ffffff" fill-opacity="0" pointer-events="all"/></a>'
    )


def lane(x, width, process, name, color, anchor):
    draw.rect(x, 166, width, 2070, WHITE, EDGE, radius=14, width=1.6)
    draw.rect(x + 1, 167, width - 2, 65, TINT[color], "none", radius=13)
    label(x + 19, 190, process, MUTED, size=18, width=width - 30)
    text(x + 19, 219, name, color=color, bold=True, size=27, width=width - 30)
    link_label, link_width = ("索引 ↗", 78) if width < 250 else ("调用索引 ↗", 121)
    link(x + width - link_width - 9, 189, link_width, link_label, anchor, size=18)


def header():
    draw.rect(0, 0, WIDTH, HEIGHT, PAPER, "none", radius=0)
    text(48, 49, "图 10   /   虚拟小车 · 高级版", size=22, bold=True, color=MUTED)
    text(48, 111, "核心函数调用与交接", size=51, bold=True)
    label(662, 109, "74dbee0  /  --secure", MUTED, code=True, size=20, width=260)
    for x, color, name in (
        (1165, BLUE, "TLS 管理"),
        (1383, RUST, "UDP 控制"),
        (1601, TEAL, "UDP 遥测"),
        (1819, PURPLE, "车端单步"),
        (2037, RED, "停车与复位"),
    ):
        line((x, 84), (x + 39, 84), color=color)
        label(x + 52, 90, name, color, size=19, width=150)
    line((2352, 61), (2403, 61), color=MUTED)
    label(2415, 67, "调用、数据或本轮执行方向", MUTED, width=390)
    line((2352, 97), (2403, 97), color=MUTED, dashed=True)
    label(2415, 103, "调度、返回或下一轮", MUTED, width=382)
    link(2821, 67, 128, "项目目录 ↗", "project-tree", size=20)
    link(2821, 103, 128, "实验工具 ↗", "experiment-tools", size=20)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" '
             'xmlns:xlink="http://www.w3.org/1999/xlink" '
             f'width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
             'role="img" aria-labelledby="title desc">')
    draw.raw('<title id="title">图 10 · 高级版核心函数调用与交接</title>')
    draw.raw('<desc id="desc">桌面 Qt 主线程、桌面控制线程、TLS 与 UDP 网络、'
             '车端 TLS 线程和车端仿真线程之间的函数调用和数据流。</desc>')
    header()
    lane(48, 460, "桌面进程", "Qt 主线程", INK, "desktop-entry")
    lane(532, 584, "桌面进程", "安全控制线程", INK, "udp-control")
    lane(1140, 204, "数据网", "TCP / UDP", BLUE, "udp-telemetry")
    lane(1368, 676, "车端进程", "management-tls 线程", BLUE, "tls-management")
    lane(2068, 884, "车端进程", "仿真主线程", PURPLE, "vehicle-tick")
    draw.rect(1368, 1851, 676, 385, TINT[TEAL], TEAL, radius=14, width=1.7)
    label(1392, 1892, "独立观察进程  /  UDP", TEAL, size=19, width=360)
    link(1882, 1892, 135, "调用索引 ↗", "telemetry-observer")

    # Startup, event dispatch and thread entry.
    line((273, 300), (273, 328))
    line((273, 376), (273, 409))
    line((497, 352), (568, 352))
    line((825, 376), (825, 409), dashed=True)
    line((825, 457), (825, 513), color=BLUE)
    line((568, 433), (548, 433), (548, 1164), (568, 1164), dashed=True)
    line((81, 433), (65, 433), (65, 1078), (81, 1078), dashed=True)
    line((65, 1078), (65, 1837), (81, 1837), dashed=True)
    line((65, 599), (81, 599), dashed=True)
    line((65, 779), (81, 779), dashed=True)
    line((2449, 300), (2449, 328), color=PURPLE)
    line((2200, 352), (1988, 352), color=BLUE)
    label(2003, 338, "with management", BLUE, code=True, width=185)
    line((1626, 376), (1626, 409), color=BLUE, dashed=True)
    line((1626, 457), (1626, 513), color=BLUE)
    line((2425, 376), (2425, 452), color=PURPLE)
    line((2425, 500), (2425, 1078), color=PURPLE)

    node(81, 252, 416, "rover.desktop.app.main()")
    node(81, 328, 416, "DesktopConsole.__init__()")
    node(81, 409, 416, "QApplication.exec()")
    node(81, 575, 416, "DesktopConsole.poll_secure_status()", size=18,
         note="worker.status() → 按钮、操作员与事件")
    node(81, 755, 416, "DesktopConsole.closeEvent()",
         note="worker.close()  /  wait_closed(0)")
    node(568, 328, 520, "SecureControlWorker.start()")
    node(568, 409, 520, "SecureControlWorker._run()")
    node(1392, 328, 596, "management_listener()", BLUE)
    node(1392, 409, 596, "run_worker()", BLUE, note="accept()  →  wrap_socket(server_side=True)")
    node(2200, 252, 498, "rover.vehicle.app.cli()", PURPLE)
    node(2200, 328, 498, "rover.vehicle.app.main()", PURPLE)
    node(2225, 452, 404, "while True", PURPLE, size=24)
    label(2455, 532, "起始时间 · monotonic()", PURPLE, width=420)

    # Both TLS endpoints share the framing and one request/reply transport path.
    line((825, 580), (825, 606), color=BLUE)
    line((825, 818), (825, 850), color=BLUE)
    line((1088, 936), (1131, 936), (1131, 874), (1349, 874),
         (1349, 537), (1392, 537), color=BLUE)
    line((1088, 630), (1098, 630), (1098, 860), (1088, 860), color=BLUE)
    line((1680, 561), (1680, 592), color=BLUE)
    line((1680, 659), (1680, 689), color=BLUE)
    line((1680, 825), (1680, 853), color=BLUE)
    line((1680, 901), (1680, 927), color=BLUE)
    line((1680, 1063), (1680, 1078), color=BLUE)
    line((1988, 1102), (2200, 1102), color=BLUE, dashed=True)
    line((1459, 1126), (1459, 1208), color=BLUE, dashed=True)
    line((1392, 1232), (1270, 1232), (1270, 982), (1088, 982),
         color=BLUE, dashed=True)
    line((825, 898), (825, 911), color=BLUE)
    line((825, 960), (825, 974), color=BLUE, dashed=True)
    line((1988, 746), (2019, 746), (2019, 1232), (1988, 1232), color=BLUE)
    line((2480, 1102), (2499, 1102), (2499, 1095), (2518, 1095), color=PURPLE)
    line((2717, 1071), (2717, 1036), color=PURPLE)
    line((2717, 1119), (2717, 1151), color=PURPLE)
    line((2518, 1175), (1988, 1175), (1988, 1115), color=BLUE, dashed=True)

    node(568, 513, 520, "connect_management()", BLUE,
         note="create_connection() → wrap_socket()")
    node(568, 606, 520, "ManagementClient.authenticate()", BLUE)
    rowset(568, 682, 520, "SecureControlSession.open() 等入口", (
        "open_session()", "issue_control_window()", "enable_session()",
        "emergency_stop()", "reset_control()",
    ), BLUE, column_widths=(286, 208), size=19)
    node(568, 850, 520, "ManagementClient._exchange()", BLUE)
    node(568, 912, 520, "send_record()  →  encode_record()", BLUE, size=22)
    node(568, 974, 520, "iter_records()  →  decode(reply)", BLUE, size=22,
         note="校对响应 request_id")
    node(1392, 513, 596, "serve_management_connection()", BLUE)
    node(1392, 592, 596, "iter_records()", BLUE,
         note="recv()  →  TcpRecordBuffer.feed()")
    rowset(1392, 689, 596, "第一条记录", (
        "decode_authenticate_request()", "verify_client_credential()",
        "encode_authentication_result()",
    ), BLUE)
    label(1697, 847, "凭据通过", BLUE)
    node(1392, 853, 596, "decode_session_request()", BLUE)
    rowset(1392, 927, 614, "按请求选回调  /  finally 关闭", (
        "on_open_session()", "on_enable_session()", "on_issue_control_window()",
        "on_emergency_stop()", "on_reset_control()", "on_close_connection()",
    ), BLUE, column_widths=(325, 263), size=19)
    node(1392, 1078, 596, "submit(operation)  →  pending.put_nowait()", BLUE, size=22)
    node(1392, 1208, 596, "send_record()  ←  encode_*_result()", BLUE,
         note="管理回复也包含 encode_session_opened()", size=22)
    node(2200, 1078, 280, "process_one(guard)", PURPLE, size=22)
    rowset(2476, 813, 445, "车端会话处理函数", (
        "handle_open_session()", "handle_enable_session()", "handle_issue_control_window()",
        "handle_emergency_stop()", "handle_reset_control()", "handle_connection_closed()",
    ), PURPLE, size=22)
    node(2518, 1071, 403, "operation(guard)", PURPLE)
    node(2518, 1151, 403, "future.set_result(reply)", PURPLE, size=22)
    label(2150, 1086, "Queue", BLUE, code=True, width=71)
    label(2033, 1166, "result.result()", BLUE, code=True, width=187)
    label(1157, 860, "TLS / TCP 19177", BLUE, code=True, width=190)
    label(1157, 966, "TLS 回复", BLUE, width=100)
    label(1535, 1174, "取出已完成的 Future", BLUE, width=300)

    # Control publication, stop operations and the single-owner application loop.
    line((497, 1078), (568, 1078), color=RUST)
    line((825, 1102), (825, 1140), color=RUST, dashed=True)
    line((810, 1188), (810, 1215), (693, 1215), (693, 1255), color=RUST)
    line((834, 1188), (834, 1215), (951, 1215), (951, 1255), color=RUST)
    line((693, 1303), (693, 1316), color=RUST)
    line((831, 1445), (834, 1445), (834, 1279), (841, 1279), color=RUST)
    line((951, 1303), (951, 1452), color=RUST)
    line((809, 1500), (809, 1524), (705, 1524), (705, 1542), color=RUST)
    line((705, 1590), (705, 1621), color=RUST)
    line((919, 1566), (952, 1566), color=RUST, dashed=True)
    line((1088, 1566), (2200, 1566), color=RUST)
    line((568, 1380), (564, 1380), (564, 770), (568, 770), color=BLUE)
    line((1075, 1270), (1110, 1270), (1110, 790), (1088, 790), color=BLUE)
    line((497, 1208), (514, 1208), (514, 1090), (568, 1090), color=RED)
    line((497, 1395), (507, 1395), (507, 1557), (497, 1557), color=RED)
    line((497, 1478), (507, 1478), arrow=False, color=RED)
    line((497, 1568), (555, 1568), (555, 1200), (568, 1200), color=RED, dashed=True)
    line((1088, 1164), (1121, 1164), (1121, 813), (1088, 813), color=RED)

    node(81, 1054, 416, "DesktopConsole.start_control()", RUST, size=22)
    node(568, 1054, 520, "SecureControlWorker.apply_target()", RUST)
    node(568, 1140, 520, "SecureControlWorker._run_loop()", RUST,
         note="读取目标与 generation，调用 _perform_action()")
    label(834, 1130, "Condition.notify_all()", RUST, code=True, width=273)
    label(600, 1241, "首次发送", RUST, width=110)
    label(953, 1241, "周期发送", RUST, width=110)
    node(568, 1255, 253, "session.start()", RUST)
    node(841, 1255, 234, "session.send()", RUST)
    rowset(568, 1316, 263, "首次发包的调用顺序", (
        "open()", "_renew_window()", "enable_session()",
    ), RUST, size=22)
    label(961, 1393, "按需续窗", RUST, size=17, width=114)
    label(961, 1419, "_send_gate()", RUST, code=True, size=17, width=135)
    node(568, 1452, 520, "send_windowed_control_command()", RUST)
    node(568, 1542, 351, "encode_windowed_command()", RUST, size=22)
    node(568, 1621, 351, "compute_control_mac()", RUST, size=22)
    node(952, 1542, 136, "sendto()", RUST, size=22)
    label(1157, 1549, "UDP 9999", RUST, code=True, size=22)
    label(1434, 1549, "ControlCommand v2", RUST, code=True, size=20, width=350)
    node(81, 1184, 416, "stop_motion()", RED, note="apply_target()  ·  speed = 0")
    node(81, 1277, 416, "stop_control()", RED, note="worker.stop_sending()")
    node(81, 1371, 416, "request_emergency_stop()", RED)
    node(81, 1454, 416, "request_reset()", RED)
    node(81, 1533, 416, "SecureControlWorker._queue_action()", RED, size=18)
    link(81, 1620, 154, "停车与恢复 ↗", "fault-and-recovery")

    # Explicit per-tick order on one thread. Codec calls branch away from the tick.
    line((2425, 1126), (2425, 1326), color=PURPLE)
    line((2425, 1393), (2425, 1542), color=PURPLE)
    line((2425, 1590), (2425, 1730), color=PURPLE)
    line((2745, 1566), (2801, 1566), (2801, 1601), color=RUST)
    line((2801, 1649), (2801, 1663), color=RUST)
    line((2518, 1687), (2425, 1687), color=RUST, dashed=True)
    label(2433, 1676, "校验成功", RUST, size=16, width=76)
    label(2199, 1652, "收包后再检查超时", PURPLE, size=17, width=203)
    line((2425, 1797), (2425, 1824), color=PURPLE)
    line((2505, 1848), (2522, 1848), color=PURPLE)
    line((2425, 1872), (2425, 1909), color=PURPLE)
    line((2200, 1848), (2110, 1848), (2110, 2095), (2200, 2095),
         color=PURPLE, dashed=True)
    line((2425, 1957), (2425, 2003), color=TEAL)
    line((2535, 2027), (2551, 2027), color=TEAL)
    line((2425, 2051), (2425, 2071), color=PURPLE)
    line((2425, 2119), (2425, 2171), color=PURPLE)
    line((2650, 2195), (2931, 2195), (2931, 476), (2629, 476),
         color=PURPLE, dashed=True)

    node(2200, 1326, 450, "update_control_guard()", PURPLE,
         note="超时锁存 / 撤销使能及当前控制目标")
    label(2189, 1308, "看门狗检查", PURPLE, width=230)
    node(2200, 1542, 545, "try_receive_windowed_control_command()", RUST, size=22)
    node(2518, 1601, 403, "decode_windowed_command()", RUST, size=22)
    node(2518, 1663, 403, "verify_control_mac()", RUST, size=22)
    node(2200, 1730, 715, "update_windowed_command_guard_with_reason()", PURPLE,
         note="control_window_rejection_reason() → _apply_session_command()")
    label(2444, 1815, "guard.last_accepted", PURPLE, code=True, size=17, width=230)
    node(2200, 1824, 305, "step_vehicle()", PURPLE)
    node(2200, 1909, 315, "TelemetrySnapshot", TEAL)
    rowset(2522, 1824, 400, "step_vehicle() 的内部调用顺序", (
        "target_for_tick()", "apply_target()", "advance()",
    ), PURPLE)
    label(2228, 1897, "达到遥测发送时刻", TEAL, size=18, width=350)
    label(2125, 2079, "未到时", PURPLE, size=17, width=90)
    node(2200, 2003, 335, "send_telemetry()", TEAL)
    node(2551, 2003, 370, "encode_telemetry()", TEAL)
    node(2200, 2071, 450, "record()  /  print()", PURPLE,
         note="可选 CSV 记录  /  状态输出")
    node(2200, 2171, 450, "sleep(max(0.0, dt_s - work_s))", PURPLE, size=22)
    label(2669, 2183, "下一轮", PURPLE, width=230)

    # Telemetry data leaves the same send target loop for two receivers.
    line((2200, 2027), (1509, 2027), (1509, 1905), (497, 1905), color=TEAL)
    line((1509, 2027), (1509, 2071), color=TEAL)
    line((275, 1861), (275, 1881), color=TEAL)
    line((275, 1929), (275, 1955), color=TEAL)
    line((275, 2003), (275, 2030), color=TEAL)
    line((275, 2078), (275, 2107), color=TEAL, dashed=True)
    line((1679, 2119), (1679, 2151), color=TEAL)

    node(81, 1813, 416, "DesktopConsole.poll_telemetry()", TEAL, size=20)
    node(81, 1881, 416, "try_receive_telemetry()", TEAL)
    node(81, 1955, 416, "decode_telemetry()", TEAL)
    node(81, 2030, 416, "VehicleMap.set_state()", TEAL)
    node(81, 2107, 416, "VehicleMap.paintEvent()", TEAL)
    node(1392, 2071, 596, "demo_udp_telemetry_receiver.main()", TEAL,
         size=22)
    node(1392, 2151, 596, "try_receive_telemetry()  →  print()", TEAL, size=22)
    label(1158, 1889, "UDP 10000", TEAL, code=True, size=22)
    label(1528, 2056, "UDP 10001 · 可选观察端", TEAL, width=380)
    label(1718, 2012, "sendto(packet, address)", TEAL, code=True, size=20, width=390)
    draw.raw('<circle cx="1509" cy="2027" r="5" fill="#0b7468"/>')

    label(48, 2278, "端口为默认示例值。SVG 的调用索引可跳转到运行参考。", MUTED,
          size=18, width=770)
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
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
    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE,
                 QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor(PAPER))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, png.width(), png.height()))
    painter.end()
    (ROOT / f"{STEM}.svg").write_bytes(svg.encode("utf-8"))
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")
    if len(sys.argv) > 1:
        preview = png.scaledToWidth(1600, Qt.TransformationMode.SmoothTransformation)
        if not preview.save(sys.argv[1]):
            raise RuntimeError("Preview export failed")
    print(f"Rendered {len(draw.text_checks)} width-checked labels at {WIDTH} x {HEIGHT}")


if __name__ == "__main__":
    main()
