"""Render assets/pill-states.png for the README from the real pill painter.

    .venv/Scripts/python.exe scripts/make_readme_image.py

Drawn by shout.overlay's own PillWidget, so the picture cannot drift from the
app: change the pill and re-run this. No window is ever shown. The level
history in the recording states is a fixed, speech-like shape, not a capture.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from shout.overlay import (BARS, COLLAPSED_H, COLLAPSED_W, HEIGHT, WIN_H,  # noqa: E402
                           WIN_W, Overlay, PillWidget)

OUT = ROOT / "assets" / "pill-states.png"
SCALE = 2                      # rendered at 2x so it stays crisp on high-DPI screens
PAD_X, PAD_TOP, CAPTION_H, GAP = 28, 26, 30, 10

STATES = [
    ("idle", "Idle: a sliver above the taskbar"),
    ("recording", "Recording: hold Ctrl+Win"),
    ("latched", "Hands-free: after a double-tap"),
    ("working", "Transcribing"),
]


def envelope() -> list[float]:
    return [max(0.05, min(0.95, 0.18 + 0.62 * abs(math.sin(i * 0.72))
                          * (0.62 + 0.38 * math.sin(i * 0.23 + 1.0))))
            for i in range(BARS)]


def pose(ov: Overlay, state: str) -> None:
    ov.state = state
    if state == "idle":
        ov.expand, ov.pill_width, ov.pill_height, ov.meter = 0.0, COLLAPSED_W, COLLAPSED_H, 0.0
        ov.history.extend([0.0] * BARS)
    else:
        ov.expand, ov.pill_height = 1.0, HEIGHT
        ov.pill_width = ov.widget.width_for(state)
        ov.meter = 0.55
        ov.history.extend(envelope())


def main() -> int:
    app = QtWidgets.QApplication(sys.argv[:1])  # noqa: F841  (widgets need it)
    ov = Overlay(enabled=True)
    ov.widget = PillWidget(ov)                 # never shown

    cols, rows = 2, 2
    cell_w, cell_h = WIN_W + GAP, WIN_H + CAPTION_H
    w = PAD_X * 2 + cols * cell_w - GAP
    h = PAD_TOP + rows * cell_h + 14
    img = QtGui.QImage(w * SCALE, h * SCALE, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.scale(SCALE, SCALE)

    # A wallpaper-ish ground: the pill is black, so a black README background
    # (GitHub dark mode) would swallow it. The image brings its own.
    grad = QtGui.QLinearGradient(0, 0, w, h)
    grad.setColorAt(0.0, QtGui.QColor(98, 118, 146))
    grad.setColorAt(1.0, QtGui.QColor(52, 64, 86))
    p.fillRect(QtCore.QRectF(0, 0, w, h), grad)

    font = QtGui.QFont("Segoe UI", 10)
    for i, (state, caption) in enumerate(STATES):
        x = PAD_X + (i % cols) * cell_w
        y = PAD_TOP + (i // cols) * cell_h
        pose(ov, state)
        ov.widget.render(p, QtCore.QPoint(x, y), QtGui.QRegion(),
                         QtWidgets.QWidget.RenderFlag.DrawChildren)
        p.setFont(font)
        p.setPen(QtGui.QColor(255, 255, 255, 225))
        p.drawText(QtCore.QRectF(x, y + WIN_H - 4, WIN_W, CAPTION_H),
                   int(QtCore.Qt.AlignmentFlag.AlignHCenter
                       | QtCore.Qt.AlignmentFlag.AlignTop), caption)
    p.end()
    img.save(str(OUT))
    print(f"wrote {OUT} ({img.width()}x{img.height()}, {OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
