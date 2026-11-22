"""Render the P7 watchdog measurements as an editable SVG and a PNG."""

import argparse
import csv
from math import ceil, floor, isfinite
import os
from pathlib import Path
from statistics import median

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

import render_vehicle_motion as draw


ROOT = Path(__file__).resolve().parent
STEM = "12-watchdog-comparison"
WIDTH, HEIGHT = 1100, 420
BACKGROUND = "#f5f8fc"
MODES = ("advanced", "expert")
WATCHDOG_MS = 500
LEFT, RIGHT, AXIS_Y = 185, 825, 343


def read_samples(path: Path) -> dict[str, list[float]]:
    samples = {mode: [] for mode in MODES}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            value = float(row["watchdog_ms"])
            if row["mode"] not in samples or not isfinite(value) or value < 0:
                raise ValueError("invalid watchdog sample")
            samples[row["mode"]].append(value)
    if not all(samples.values()):
        raise ValueError("both modes need sample data")
    return samples


def build_svg(samples: dict[str, list[float]]) -> str:
    values = [WATCHDOG_MS, *samples["advanced"], *samples["expert"]]
    tick = max(10, 10 * ceil((max(values) - min(values)) / 60))
    low = max(0, tick * floor((min(values) - 6) / tick))
    high = tick * ceil((max(values) + 6) / tick)

    def x(value):
        return LEFT + (RIGHT - LEFT) * (value - low) / (high - low)

    draw.parts.clear()
    draw.text_checks.clear()
    draw.cards.clear()
    draw.raw(f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
             f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="watchdog-title">')
    draw.raw('<title id="watchdog-title">高级与专家模式的 UDP 看门狗停车时间</title>')
    draw.rect(0, 0, WIDTH, HEIGHT, BACKGROUND, "none", radius=0)
    draw.rect(24, 22, WIDTH - 48, HEIGHT - 44, "#ffffff", "#d5deeb", radius=16)
    draw.text(53, 54, "图 12   /   P7 机制对照", size=15, bold=True, color=draw.MUTED, max_width=320)
    draw.text(53, 93, "UDP 看门狗停车时间", size=32, bold=True, max_width=850)
    draw.text(54, 121, "每个圆点对应一轮。起点为车端最后接受指令，终点为首次零速并锁存。",
              size=17, color=draw.MUTED, max_width=950)

    for value in range(low, high + 1, tick):
        position = x(value)
        draw.line(((position, 163), (position, AXIS_Y)), color="#dfe6ef", arrow=False, width=1)
        draw.text(position, 369, str(value), anchor="middle", size=14, color=draw.MUTED)
    reference = x(WATCHDOG_MS)
    draw.line(((reference, 163), (reference, AXIS_Y)), color="#75668d", dashed=True, arrow=False, width=2)
    draw.text(reference, 151, "500 ms 配置值", size=14, color="#625477", anchor="middle")
    draw.line(((LEFT, AXIS_Y), (RIGHT, AXIS_Y)), color="#a5b8d0", arrow=False, width=1.5)
    draw.text(RIGHT + 20, 369, "ms", size=14, color=draw.MUTED)

    for mode, label, color, y in (
        ("advanced", "高级模式", "#2563a6", 211),
        ("expert", "专家模式", "#087262", 294),
    ):
        measured = samples[mode]
        middle = median(measured)
        draw.text(55, y + 6, label, size=20, bold=True, color=color, max_width=122)
        draw.line(((x(middle), y - 24), (x(middle), y + 24)), color=color, arrow=False, width=3)
        for index, value in enumerate(measured):
            # Separate coincident samples vertically without altering their time values.
            offset = 34 * ((index + 0.5) / len(measured) - 0.5)
            draw.raw(f'<circle cx="{x(value):.2f}" cy="{y + offset:.2f}" r="5.8" '
                     f'fill="{color}" stroke="#ffffff" stroke-width="1.4"><title>{value:.3f} ms</title></circle>')
        draw.text(854, y - 1, f"{len(measured)} 轮 · 中位数 {middle:.1f} ms", size=15,
                  bold=True, color=color, max_width=202)
        draw.text(854, y + 22, f"{min(measured):.1f}–{max(measured):.1f} ms", size=15,
                  color=draw.MUTED, max_width=200)

    draw.raw("</svg>")
    return "\n".join(draw.parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, default=ROOT / "p7-watchdog-samples.csv")
    parser.add_argument("--output-prefix", type=Path, default=ROOT / STEM)
    args = parser.parse_args()

    application = QGuiApplication.instance() or QGuiApplication([])
    if "Microsoft YaHei" not in QFontDatabase.families():
        fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        for name in ("msyh.ttc", "msyhbd.ttc"):
            if (fonts / name).is_file():
                QFontDatabase.addApplicationFont(str(fonts / name))

    svg = build_svg(read_samples(args.input)).encode("utf-8")
    renderer = QSvgRenderer(svg)
    if not renderer.isValid():
        raise RuntimeError("invalid SVG")
    image = QImage(WIDTH * 2, HEIGHT * 2, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(BACKGROUND))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, image.width(), image.height()))
    painter.end()
    args.output_prefix.with_suffix(".svg").write_bytes(svg)
    if not image.save(str(args.output_prefix.with_suffix(".png"))):
        raise RuntimeError("PNG export failed")


if __name__ == "__main__":
    main()
