"""Render the secure desktop management request map as SVG and PNG."""

import os
from pathlib import Path
import sys

import render_vehicle_motion as draw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent
STEM = "09-management-request-flow"
WIDTH, HEIGHT = 1640, 1536
PNG_SCALE = 3

INK = "#162942"
MUTED = "#485e74"
EDGE = "#c9d7e5"
BLUE = "#235ca9"
TEAL = "#08786a"
PURPLE = "#624799"
RUST = "#8e4720"
WHITE = "#ffffff"

COLORS = (
    (BLUE, "#eff5ff"),
    (BLUE, "#eff5ff"),
    (TEAL, "#edf8f5"),
    (PURPLE, "#f4effa"),
)

COLUMNS = (40, 437, 834, 1231)
COL_W = 369
REQUEST_Y, REPLY_Y, STATION_H = 344, 540, 152


def text(x, y, value, *, size=16, bold=False, color=INK, code=False,
         width=1560, anchor="start"):
    draw.text(x, y, value, size=size, bold=bold, color=color, code=code,
              max_width=width, anchor=anchor)


def stroke(points, color, *, arrow=True, dashed=False, width=2.2):
    draw.line(points, color=color, arrow=arrow, dashed=dashed, width=width)


def band(x, y, w, h, fill, color=EDGE, radius=13):
    draw.rect(x, y, w, h, fill, color, radius=radius, width=1.35)


def station(index, y, title, rows):
    x = COLUMNS[index]
    color, tint = COLORS[index]
    band(x, y, COL_W, STATION_H, WHITE)
    band(x, y, COL_W, 42, tint)
    text(x + 16, y + 29, title, size=19, bold=True, color=color,
         width=COL_W - 32)
    for row, (value, code) in enumerate(rows):
        text(x + 16, y + 65 + row * 22, value, size=15 if code else 16,
             color=MUTED, code=code, width=COL_W - 32)


def authenticate_band():
    band(40, 138, 560, 135, "#eff5ff")
    band(665, 138, 935, 135, "#edf8f5")
    stroke([(600, 204), (665, 204)], BLUE)

    text(59, 169, "先接入 · TCP 连接与 TLS 证书校验", size=19, bold=True,
         color=BLUE, width=522)
    text(59, 196, "connect_management() → wrap_socket()", code=True, size=17,
         width=522)
    text(59, 223, "桌面依据 CA 和主机名验证车端。", color=MUTED, width=522)
    text(59, 249, "校验通过后，在同一条 TLS 连接内发送账号凭据。",
         color=MUTED, width=522)

    text(684, 169, "再认证 · 车端连接线程核对账号", size=19, bold=True,
         color=TEAL, width=896)
    text(684, 195, "authenticate() → decode_authenticate_request() → verify_client_credential()",
         code=True, size=15, width=896)
    text(684, 221, "encode_authenticate_request() · encode_authentication_result() · decode_authentication_result()",
         code=True, size=14, width=896)
    text(684, 247, "认证通过则建立连接身份；认证被拒绝时结束连接。",
         color=MUTED, width=896)

    entry_x = COLUMNS[2] + COL_W / 2
    stroke([(entry_x, 273), (entry_x, REQUEST_Y)], TEAL)
    text(entry_x - 15, 312, "认证通过，开始受理会话请求", color=TEAL,
         width=440, anchor="end")


def common_flow():
    text(40, 315, "会话请求主干", size=27, bold=True)
    stroke([(1210, 306), (1260, 306)], BLUE)
    text(1271, 312, "请求", color=BLUE, width=65)
    stroke([(1390, 306), (1340, 306)], TEAL, dashed=True)
    text(1404, 312, "回复／结果", color=TEAL, width=170)

    requests = (
        ("桌面界面 · 提交动作", (
            ("控件提交意图，后台可自行续窗", False),
            ("apply_target()", True),
            ("request_emergency_stop()", True),
            ("request_reset()", True),
        )),
        ("桌面后台 · 组织请求", (
            ("_run_loop() / _perform_action()", True),
            ("ManagementClient._exchange()", True),
            ("分配 request_id，调用编码函数", False),
            ("send_record() 写入 TLS", True),
        )),
        ("车端 TLS 线程 · 接收与分派", (
            ("serve_management_connection()", True),
            ("iter_records() 拼出记录", True),
            ("decode_session_request()", True),
            ("on_*() → submit(operation)", True),
        )),
        ("车端仿真主线程 · 执行", (
            ("队列取出 operation + Future", False),
            ("process_one(guard)", True),
            ("每轮最多执行一项 handle_*", False),
            ("计算新 guard 和本次回复", False),
        )),
    )
    replies = (
        ("桌面界面 · 读取状态", (
            ("poll_secure_status()", True),
            ("定时读取后台状态和结果", False),
            ("界面据此启用或禁用按钮", False),
            ("遥测由独立 UDP 通道更新", False),
        )),
        ("桌面后台 · 核对回复", (
            ("iter_records() → 回复解码", True),
            ("核对回复类型与 request_id", False),
            ("使用 accepted / enabled / granted", True),
            ("适用时更新后台状态", False),
        )),
        ("车端 TLS 线程 · 返回回复", (
            ("Future 唤醒等待的连接线程", False),
            ("开户／复位时保存新连接上下文", False),
            ("encode_*() 编码业务回复", True),
            ("send_record() 经同一 TLS 回发", True),
        )),
        ("车端仿真主线程 · 交还结果", (
            ("future.set_result(reply)", True),
            ("把结果交还给等待的线程", False),
            ("app.py 保存返回的新 guard", False),
            ("仿真按 guard 继续推进", False),
        )),
    )
    for index, (title, rows) in enumerate(requests):
        station(index, REQUEST_Y, title, rows)
    for index, (title, rows) in enumerate(replies):
        station(index, REPLY_Y, title, rows)

    for index in range(3):
        right = COLUMNS[index] + COL_W
        next_left = COLUMNS[index + 1]
        req_y, rep_y = REQUEST_Y + 93, REPLY_Y + 93
        stroke([(right, req_y), (next_left, req_y)], BLUE)
        stroke([(next_left, rep_y), (right, rep_y)], TEAL, dashed=True)

    def connector_label(x, value, color):
        text(x, 520, value, size=15, color=color, width=260, anchor="middle")

    connector_label(COLUMNS[0] + COL_W + 14, "意图／状态", BLUE)
    connector_label(COLUMNS[1] + COL_W + 14, "TLS · TCP 记录", BLUE)
    connector_label(COLUMNS[2] + COL_W + 14, "Queue / Future", PURPLE)
    x = COLUMNS[3] + COL_W / 2
    stroke([(x, REQUEST_Y + STATION_H), (x, REPLY_Y)], PURPLE)

    text(40, 726, "同一条已认证的 TLS 连接可反复往返请求和回复。消息用长度前缀分帧；车端线程用队列和 Future 交接。",
         size=16, color=MUTED)


BRANCHES = (
    (
        "打开或复用会话",
        ("首次登记保持未使能，ID 和 32 字节密钥经 TLS 返回。",
         "同一连接重复申请复用 ID 和密钥，可用于空闲保活。"),
        (
            ("桌面", "open_session()"),
            ("车端", "handle_open_session()"),
            ("发请求", "encode_open_session_request()"),
            ("编码回复", "encode_session_opened()"),
            ("读回复", "decode_session_opened()"),
        ),
        BLUE,
    ),
    (
        "使能",
        ("当前连接须持有会话，且车辆未锁存故障。",
         "首次使能等 0.5 秒收首包；重复使能不刷新期限。"),
        (
            ("桌面", "enable_session()"),
            ("车端", "handle_enable_session()"),
            ("发请求", "encode_enable_session_request()"),
            ("编码回复", "encode_session_enable_result()"),
            ("读回复", "decode_session_enable_result()"),
        ),
        BLUE,
    ),
    (
        "签发和续发窗口",
        ("车端签发短期窗口，当前有效期为 0.25 秒。",
         "续窗替换窗口 ID；同一会话的 UDP 序号继续递增。"),
        (
            ("桌面", "issue_control_window()"),
            ("车端", "handle_issue_control_window()"),
            ("发请求", "encode_issue_control_window_request()"),
            ("编码回复", "encode_control_window_result()"),
            ("读回复", "decode_control_window_result()"),
        ),
        BLUE,
    ),
    (
        "主动急停",
        ("桌面先取消待发指令。车端接受后清目标、",
         "取消使能、清窗口并锁存；当前会话仍可请求复位。"),
        (
            ("桌面", "emergency_stop()"),
            ("车端", "handle_emergency_stop()"),
            ("发请求", "encode_emergency_stop_request()"),
            ("编码回复", "encode_emergency_stop_result()"),
            ("读回复", "decode_emergency_stop_result()"),
        ),
        RUST,
    ),
    (
        "显式复位",
        ("车端要求归属匹配、已锁存、已停用且无控制目标。",
         "原 TLS 重开会话，序号归零；等手动应用。"),
        (
            ("桌面", "reset_control() / _clear_session()"),
            ("车端", "handle_reset_control()"),
            ("发请求", "encode_reset_control_request()"),
            ("编码回复", "encode_reset_control_result()"),
            ("读回复", "decode_reset_control_result()"),
        ),
        RUST,
    ),
    (
        "断开与会话撤销",
        ("关窗、EOF 或连接错误使车端进入清理。",
         "仅撤销本连接仍持有的会话，保留已有故障锁存。"),
        (
            ("桌面", "closeEvent() → worker.close()"),
            ("回调", "on_close_connection() → submit()"),
            ("车端", "handle_connection_closed()"),
            ("清理", "close_control_session()"),
            ("触发", "连接退出事件，无请求编号和业务回复"),
        ),
        TEAL,
    ),
)


def branches():
    text(40, 775, "业务分支", size=27, bold=True)
    text(184, 775, "仿真主线程执行对应分支。各卡列出业务规则、发起方法和编解码入口。",
         color=MUTED, width=1000)

    for index, (title, rules, functions, color) in enumerate(BRANCHES):
        row, col = divmod(index, 3)
        x, y, w = 40 + col * 526, 797 + row * 258, 508
        band(x, y, w, 238, WHITE)
        draw.rect(x + 15, y + 15, 5, 25, color, color, radius=2, width=0)
        text(x + 30, y + 35, title, size=22, bold=True, color=color, width=w - 48)
        for j, rule in enumerate(rules):
            text(x + 19, y + 64 + j * 23, rule, size=16, color=MUTED, width=w - 38)
        stroke([(x + 19, y + 100), (x + w - 19, y + 100)], EDGE,
               arrow=False, width=1.0)
        for j, (label, func) in enumerate(functions):
            baseline = y + 123 + j * 23
            text(x + 19, baseline, label, size=13, color=color, width=76)
            text(x + 101, baseline, func, size=14, color=INK,
                 code=label != "触发", width=w - 120)


def udp_and_stop():
    band(40, 1315, 1560, 166, "#f7fafd")
    stroke([(913, 1334), (913, 1462)], EDGE, arrow=False, width=1.3)

    text(60, 1348, "授权就绪后 · 控制数据走独立 UDP", size=20, bold=True,
         color=BLUE, width=828)
    text(60, 1376, "窗口、会话 ID 和密钥用于签名及核对，真实车况仍通过遥测返回。",
         color=MUTED, width=828)
    text(60, 1402, "send_windowed_control_command() → try_receive_windowed_control_command()",
         code=True, size=15, width=828)
    text(60, 1428, "update_windowed_command_guard() → step_vehicle()",
         code=True, size=15, width=828)
    text(60, 1454, "send_telemetry() → poll_telemetry()", code=True, size=15, width=828)

    text(937, 1348, "同样会停车，触发路径各不相同", size=20, bold=True,
         color=TEAL, width=642)
    for j, (label, value) in enumerate((
        ("正常停车", "继续发 UDP 零速目标，保持控制。"),
        ("停止发包", "距上次有效包约 0.5 秒，看门狗锁存。"),
        ("急　　停", "TLS 请求获接受后，车端清目标并锁存。"),
    )):
        y = 1381 + j * 30
        text(937, y, label, size=16, bold=True, color=INK, width=106)
        text(1056, y, value, size=16, color=MUTED, width=518)

    text(40, 1514, "编号对照   request_id 从 1 递增配对回复     session_id 标记会话     seq 从 0 排列 UDP 指令     window_id 标记窗口     generation 作废桌面旧意图",
         size=16, color=MUTED)


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">')
    draw.raw('<title id="title">图9 管理请求怎样运行</title>')
    draw.raw('<desc id="desc">先建立TLS连接并校验证书，再认证账号。认证通过后，箭头指向车端接收与分派的入口，'
             '下方展示同一连接上的管理请求和回复主干。'
             '下部列出会话、使能、窗口、急停、复位、断开撤销的规则和函数，'
             '底部对照独立UDP控制和三种停车方式。</desc>')
    draw.rect(0, 0, WIDTH, HEIGHT, WHITE, WHITE, radius=0, width=0)
    text(40, 36, "图 9   安全桌面端", size=17, bold=True, color=BLUE)
    text(40, 82, "管理请求怎样运行？", size=39, bold=True)
    text(40, 115, "从一次界面操作到车端裁决，再回到桌面；不同功能共用同一条请求主干。",
         size=20, color=MUTED)

    authenticate_band()
    common_flow()
    branches()
    udp_and_stop()
    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main():
    app = QGuiApplication.instance() or QGuiApplication([])
    families = QFontDatabase.families()
    if "Microsoft YaHei" not in families or "Consolas" not in families:
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for filename in ("msyh.ttc", "msyhbd.ttc", "consola.ttf", "consolab.ttf"):
            QFontDatabase.addApplicationFont(str(fonts / filename))
    svg = build_svg()
    renderer = QSvgRenderer(svg.encode("utf-8"))
    if not renderer.isValid():
        raise RuntimeError("Invalid SVG")

    png = QImage(WIDTH * PNG_SCALE, HEIGHT * PNG_SCALE,
                 QImage.Format.Format_ARGB32_Premultiplied)
    png.fill(QColor(WHITE))
    painter = QPainter(png)
    renderer.render(painter, QRectF(0, 0, png.width(), png.height()))
    painter.end()

    (ROOT / f"{STEM}.svg").write_bytes(svg.encode("utf-8"))
    if not png.save(str(ROOT / f"{STEM}.png")):
        raise RuntimeError("PNG export failed")
    if len(sys.argv) > 1:
        preview = png.scaledToWidth(1050, Qt.TransformationMode.SmoothTransformation)
        if not preview.save(sys.argv[1]):
            raise RuntimeError("Preview export failed")
    print(f"Rendered {len(draw.text_checks)} checked labels at {WIDTH} x {HEIGHT}")


if __name__ == "__main__":
    main()
