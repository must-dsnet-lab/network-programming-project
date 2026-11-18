"""Render the TLS management diagram as editable SVG and high-resolution PNG."""
import os
from pathlib import Path
import sys

import render_vehicle_motion as draw
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent
STEM = "08-tls-management"
WIDTH, HEIGHT = 1400, 1096
PNG_SCALE = 3

DESKTOP = ("#2563b8", "#eef5ff", "#f8fbff")
WORKER = ("#08796a", "#e9f7f3", "#f7fbfa")
SIMULATION = ("#65469b", "#f2edfa", "#fcfaff")
BORDER = "#c8d6e5"
LANE_X = (32, 483, 934)
LANE_WIDTH = 434
CARD_WIDTH = 386
CARD_X = tuple(x + 24 for x in LANE_X)


def label(x, y, value, *, size=21, bold=False, color=draw.INK,
          code=False, width=1336):
    draw.text(x, y, value, size=size, bold=bold, color=color, code=code, max_width=width)


def lane(x, title, subtitle, palette):
    color, tint, background = palette
    draw.rect(x, 209, LANE_WIDTH, 841, background, BORDER, radius=14)
    draw.rect(x, 209, LANE_WIDTH, 70, tint, BORDER, radius=14)
    label(x + 22, 239, title, size=25, bold=True, color=color, width=390)
    label(x + 22, 264, subtitle, size=18, color=draw.MUTED, width=390)


def card(column, y, height, title, rows, palette, *, code_title=False):
    x = CARD_X[column]
    color, tint, _ = palette
    draw.rect(x, y, CARD_WIDTH, height, "#ffffff", BORDER, radius=11)
    draw.rect(x + 1, y + 10, 4, height - 20, tint, color, radius=2, width=1)
    label(x + 20, y + 32, title, size=22, bold=True, color=color,
          code=code_title, width=CARD_WIDTH - 40)
    for index, (value, code) in enumerate(rows):
        label(x + 20, y + 62 + index * 27, value, size=19,
              color=draw.MUTED, code=code, width=CARD_WIDTH - 40)


def link(left, y, color, *, reverse=False, dashed=False):
    points = ((CARD_X[left] + CARD_WIDTH, y), (CARD_X[left + 1], y))
    draw.line(points[::-1] if reverse else points, color=color, dashed=dashed, width=2.7)


def down(column, start, end, color):
    x = CARD_X[column] + CARD_WIDTH / 2
    draw.line(((x, start), (x, end)), color=color, width=2)


def startup():
    draw.rect(32, 114, 1336, 74, "#f5f8fc", BORDER, radius=12)
    label(53, 156, "启动", size=23, bold=True, width=85)
    draw.line(((136, 129), (136, 173)), color=BORDER, arrow=False, width=1.4)
    label(156, 144, "worker.start() → run_worker()", code=True, color=WORKER[0], width=445)
    label(156, 171, "TLS 工作线程开始等待连接", size=18, color=draw.MUTED, width=445)
    draw.line(((622, 129), (622, 173)), color=BORDER, arrow=False, width=1.4)
    label(643, 144, "yield process_one", code=True, color=SIMULATION[0], width=680)
    label(643, 171, "app.py 的 with 将它绑定为 process_management", size=18,
          color=draw.MUTED, width=680)


def handshake_and_auth():
    card(0, 300, 80, "TLS 握手", (("信任实验 CA，校验 localhost", False),), DESKTOP)
    card(1, 300, 80, "接受连接并握手", (("使用已载入的证书与私钥", False),), WORKER)
    card(2, 300, 80, "车端状态由仿真持有", (("guard", True),), SIMULATION)
    link(0, 340, DESKTOP[0])

    down(0, 380, 408, DESKTOP[0])
    down(1, 380, 408, WORKER[0])
    card(0, 408, 130, "认证请求 6", (("AuthenticateRequest", True),
         ("核对回复编号 6 和 accepted", False), ("通过后继续申请会话", False)), DESKTOP)
    card(1, 408, 130, "检查客户端凭据", (("verify_client_credential()", True),
         ("AuthenticationResult", True), ("通过后保存已验证的客户端身份", False)), WORKER)
    link(0, 451, DESKTOP[0])
    link(0, 508, WORKER[0], reverse=True)


def open_session():
    down(0, 538, 571, DESKTOP[0])
    down(1, 538, 571, WORKER[0])
    card(0, 571, 137, "依次发送申请 7 和 8", (("OpenSessionRequest", True),
         ("收到 7 的回复，再发 8", False)), DESKTOP)
    card(1, 571, 137, "submit(operation)", (("pending.put_nowait(...)", True),
         ("入队 (operation, Future)", False), ("工作线程等待 Future 结果", False)),
         WORKER, code_title=True)
    card(2, 571, 137, "process_one(guard)", (("pending.get_nowait()", True),
         ("每轮最多取一项，空队列立即返回", False)), SIMULATION, code_title=True)
    link(0, 617, DESKTOP[0])
    link(1, 654, SIMULATION[0])

    down(2, 708, 735, SIMULATION[0])
    card(0, 735, 154, "两次回复对应同一会话", (("request_id 分别为 7 和 8", False),
         ("session_id 相同", False)), DESKTOP)
    card(1, 735, 154, "取得连接上下文与回复", (("保存本连接的 session_id", False),
         ("发送 SessionOpened", False)), WORKER)
    card(2, 735, 154, "operation(guard)", (("handle_open_session()", True),
         ("登记或沿用原 ID，保持未使能", False),
         ("future.set_result(reply)", True), ("app.py 保存返回的 next_guard", False)),
         SIMULATION, code_title=True)
    link(1, 816, SIMULATION[0], reverse=True, dashed=True)
    link(0, 816, WORKER[0], reverse=True)


def close_session():
    down(0, 889, 921, DESKTOP[0])
    down(1, 889, 921, WORKER[0])
    down(2, 889, 921, SIMULATION[0])
    card(0, 921, 103, "结束这条 TLS 连接", (), DESKTOP)
    card(1, 921, 103, "EOF 触发关闭回调", (("on_close_connection()", True),
         ("通过同一队列提交关闭操作", False)), WORKER)
    card(2, 921, 103, "handle_connection_closed()", (("匹配连接绑定 ID，撤销车端登记", False),),
         SIMULATION, code_title=True)
    link(0, 975, DESKTOP[0])
    link(1, 975, SIMULATION[0])


def build_svg():
    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title description">')
    draw.raw('<title id="title">TLS 管理连接怎样绑定控制会话</title>')
    draw.raw('<desc id="description">本机回环实验。桌面校验证书后通过 TLS 发起认证与会话申请。'
             '车端 TLS 工作线程将操作和 Future 放入队列，仿真主线程每轮至多执行一项、更新 guard，'
             '并交还连接上下文与回复。请求 7、8 复用同一会话 ID；连接关闭时按绑定 ID 撤销。</desc>')
    draw.rect(0, 0, WIDTH, HEIGHT, "#ffffff", "#ffffff", radius=0, width=0)
    label(32, 55, "TLS 管理连接怎样绑定控制会话？", size=36, bold=True)
    label(32, 92, "本机回环实验 · 从证书校验、认证到登记和撤销", size=23, color=draw.MUTED)
    startup()
    lane(LANE_X[0], "桌面端", "发送请求、核对回复", DESKTOP)
    lane(LANE_X[1], "车端 TLS 工作线程", "读取连接、提交管理操作", WORKER)
    lane(LANE_X[2], "车端仿真主线程", "app.py 持有并更新 guard", SIMULATION)
    handshake_and_auth()
    open_session()
    close_session()
    label(32, 1080, "虚线表示 Future 将执行结果通知工作线程。",
          size=18, color=draw.MUTED)
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
    if len(sys.argv) > 1:
        preview = png.scaledToWidth(880, Qt.TransformationMode.SmoothTransformation)
        if not preview.save(sys.argv[1]):
            raise RuntimeError("Preview export failed")
    print(f"Rendered {len(draw.text_checks)} checked labels at {WIDTH}×{HEIGHT}")


if __name__ == "__main__":
    main()
