from PySide6.QtGui import QFont, QFontDatabase


def desktop_font(pixels: int, *, bold=False, numeric=False) -> QFont:
    family = "Cascadia Mono" if numeric else "Microsoft YaHei UI"
    font = QFont(family) if QFontDatabase.hasFamily(family) else QFont()
    if numeric:
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(pixels)
    font.setBold(bold)
    return font


STATUS_COLORS = {"live": "#176756", "alert": "#8a481a", "quiet": "#5c6b64"}


CONSOLE_STYLE = """
    QWidget#console { background: #f1f3f0; color: #24312c; }
    QWidget#sidebar { background: #fbfcfa; border: 1px solid #d7dfd9; }
    QFrame#mapFrame { background: #fbfcfa; border: 1px solid #cdd8d0; }
    QFrame#divider { background: #e1e6e0; min-height: 1px; max-height: 1px; }
    QLabel { color: #24312c; }
    QLabel#controlStatus {
        color: #34443b; background: #eff4ef; padding: 8px;
        border-left: 2px solid #adc1b3; min-height: 24px;
    }
    QLabel[role="muted"] { color: #5c6b64; }
    QPushButton {
        background: #ffffff; color: #26352e; border: 1px solid #cbd6cf;
        border-radius: 4px; padding: 5px 9px; min-height: 19px;
        selection-color: #ffffff; selection-background-color: #286958;
    }
    QPushButton:hover { background: #eaf1ec; border-color: #99b5a6; }
    QPushButton:pressed { background: #dce8e0; }
    QPushButton:focus {
        border-color: #287d67;
    }
    QPushButton:disabled {
        background: #f2f4f1; color: #68766d; border-color: #d9e0da;
    }
    QPushButton[role="primary"]:enabled {
        background: #246553; color: #ffffff; border-color: #246553;
    }
    QPushButton[role="primary"]:enabled:hover { background: #1b5344; }
    QPushButton[role="primary"]:enabled:focus { border-color: #17392f; }
    QPushButton[role="danger"]:enabled {
        background: #fff4f0; color: #932f2d; border-color: #ddb7ad;
    }
    QPushButton[role="danger"]:enabled:hover { background: #f8e1da; }
    QPlainTextEdit {
        background: #f5f7f3; color: #34443b; padding: 5px;
        border: 1px solid #d8e0d9; border-radius: 3px;
        selection-color: #ffffff; selection-background-color: #286958;
    }
"""
