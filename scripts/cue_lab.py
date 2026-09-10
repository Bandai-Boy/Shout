"""Audition the cue sounds and save the one you like, under a name of its own.

    .venv/Scripts/shoutw.exe scripts/cue_lab.py

`shoutw.exe`, not `pythonw.exe`: uv installed its CONSOLE trampoline under both
names on this machine (PE subsystem 3, byte-identical to python.exe), so
pythonw would open a terminal window beside the lab. See the 8 Sep lab note.

Pick a material, drag a slider, hear it the moment you let go. Nothing is written
until you press Save, and Save only touches the cue keys in config.json — every
other setting is left as it is.

*The six materials are read-only.* Dragging a knob while one of them is selected
renames what you are editing, so Save creates a NEW entry in the list rather than
redefining the material you started from. That matters because the material is
the thing you navigate by: if tuning `marimba` overwrote `marimba`, the list
would slowly stop describing anything, and there would be no way back to the
sound you liked last week. Your own saved voices ARE editable in place — saving
over a name you created updates it, which is what editing your own work should
do — and Delete removes one.

Two things this is deliberately NOT:

*Not its own synth.* It calls `shout.cues.build()`, the same function the app
calls, through the same `Cues` output stream. A lab with its own copy of the
oscillator would let you tune something the app then does not play — the same
shape of bug as a warmup that runs different code from the real path.

*Not a restart loop.* Auditioning by editing constants and restarting Shout costs
about fifteen seconds a try, which is far too slow to converge on taste; here a
slider re-synthesizes as it moves and plays in a few milliseconds once let go.

The knobs, roughly in the order they matter for "less bright":

    Root        the pitch everything is built from. This is the big one.
    Decay       0 is a held beep; higher is struck and ringing out, which is
                most of what separates "shrill" from "pleasant" at equal volume.
    Brightness  how much upper harmonic. 0 is a pure sine, the softest timbre
                there is; raise it for wood or glass character.
    Length      how long the notes ring. Decay makes long notes taper, not drone.
    Spread      how far apart the two notes are. Below 1 the interval narrows.
    Gap         silence between notes; what makes a blip read as two notes.

Save takes effect in the running app within about half a second — Shout watches
config.json — so there is nothing to restart. Then re-run
`harness/probe_cues.py`. That gate asserts the cue does not change your
transcript, and it is a property of the SOUND — every material here is harmonic
because the VAD rejects tones, and a noisy one could break it.

---

## The look

Ember Dusk, the Vendor Vault default: deep purple base, coral fire accent, amber
warmth. Two things about that system do not survive the trip to Qt Widgets, and
one thing about it is wrong on its own terms.

*No backdrop blur, so glass is built the other way round.* VV's tiles are a 6%
white fill over a blurred, blobbed background. Qt has no `backdrop-filter` — a
child widget cannot blur what its parent painted. What it CAN do is translucency,
so the background blobs are painted for real in `Lab.paintEvent` and the cards
are genuinely translucent over them. The colour variation VV gets from blur, this
gets from the blobs showing through. Frosting is the part that is lost.

*No `box-shadow` in QSS.* `QGraphicsDropShadowEffect` is the real equivalent and
is used where VV uses a glow that carries meaning — the accent bars on the stat
tiles, and the CTA. It is not sprayed everywhere: an effect disables some render
paths, and the depth here comes from the translucency, not from shadows.

*Two VV tokens fail WCAG AA and are not used as text.* Measured over every text
token against every background tier this window actually has: `--vv-text-muted`
(#8a6f5a) lands at 2.78-3.91:1 and `--vv-negative` (#ef5350) at 3.72-3.87:1 on
card fills. Labels therefore use `--vv-text-secondary` (5.73-8.05:1) and the
over-ceiling warning uses #ff7b73 (5.15-5.35:1). #8a6f5a survives only as the
disabled-button colour, where a contrast floor does not apply. Faithfulness to a
palette does not outrank being readable; `harness/probe_lab.py` measures it.
"""
from __future__ import annotations

import ctypes
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from shout.config import Config, config_dir  # noqa: E402
from shout.cues import (MAX_GESTURE_S, PRESETS, Cues, Voice,  # noqa: E402
                        build, duration_s, notes)

# Ember Dusk, from vendor-vault-themes.md. TEXT_MUTED and the raw VV negative are
# deliberately absent from every readable-text role — see the module docstring.
C = {
    "deep": "#1a0f2e", "mid": "#2d1854", "surface": "#3d2266",
    "border": "rgba(255,180,120,0.15)", "border_lit": "rgba(255,180,120,0.30)",
    "fill": "rgba(255,255,255,0.06)", "fill_hover": "rgba(255,255,255,0.10)",
    "inset": "rgba(255,255,255,0.08)",
    "accent": "#ff7043", "warm": "#ffab40", "pop": "#e91e8c", "soft": "#ff8a65",
    "text": "#f5e6d3", "label": "#c4a882", "disabled": "#8a6f5a",
    "negative": "#ff7b73",
    # CTA gradient is hardcoded in VV and does not follow the theme.
    "cta_a": "#6D28D9", "cta_b": "#3B82F6",
}
BLOBS = [                       # (x%, y%, radius%, rgba) — VV's body background
    (0.20, 0.20, 0.50, (255, 112, 67, 0.18)),
    (0.80, 0.10, 0.45, (233, 30, 140, 0.12)),
    (0.50, 0.80, 0.55, (255, 171, 64, 0.10)),
    (0.10, 0.90, 0.40, (100, 60, 180, 0.20)),
]

# (field, label, min, max, decimals) — sliders are integers, so each is scaled.
KNOBS = [
    ("root_hz", "Root", 150.0, 1200.0, 0),
    ("decay", "Decay", 0.0, 1.0, 2),
    ("bright", "Brightness", 0.0, 1.0, 2),
    ("length", "Length", 0.4, 3.0, 2),
    ("spread", "Spread", 0.4, 1.6, 2),
    ("gap_ms", "Gap", 0.0, 60.0, 0),
    ("gain", "Trim", 0.2, 1.0, 2),
]
STEPS = 1000

QSS = f"""
#card {{
    background: {C['fill']};
    border: 1px solid {C['border']};
    border-radius: 16px;
}}
#tile {{
    background: {C['fill']};
    border: 1px solid {C['border']};
    border-radius: 10px;
}}
QLabel {{ color: {C['text']}; background: transparent; }}
#h1 {{ color: {C['text']}; }}
#sub, #section, #knob, #tileLabel {{ color: {C['label']}; }}
#value {{ color: {C['text']}; }}
#status {{ color: {C['label']}; }}

QComboBox, QLineEdit {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    padding: 7px 12px;
    color: {C['text']};
    selection-background-color: {C['accent']};
    selection-color: {C['deep']};
}}
QComboBox:hover, QLineEdit:hover {{ border: 1px solid {C['border_lit']}; }}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {C['accent']}; }}
QLineEdit {{ font-family: Consolas, monospace; }}
QComboBox::drop-down {{ border: none; width: 30px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {C['mid']};
    border: 1px solid {C['border_lit']};
    border-radius: 10px;
    padding: 4px;
    color: {C['text']};
    outline: none;
    selection-background-color: {C['accent']};
    selection-color: {C['deep']};
}}
QComboBox QAbstractItemView::item {{ min-height: 26px; padding-left: 6px;
                                     border-radius: 6px; }}

QPushButton {{
    background: {C['surface']};
    border: 1px solid {C['border']};
    border-radius: 10px;
    color: {C['label']};
    padding: 0 16px;
    min-height: 34px;
    font-weight: 600;
}}
QPushButton:hover {{ background: {C['fill_hover']}; border: 1px solid {C['border_lit']};
                     color: {C['text']}; }}
QPushButton:pressed {{ background: {C['fill']}; }}
QPushButton:focus {{ border: 1px solid {C['accent']}; }}
QPushButton:disabled {{ color: {C['disabled']}; border: 1px solid {C['border']};
                        background: transparent; }}
QPushButton#cta {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 {C['cta_a']}, stop:1 {C['cta_b']});
    border: none; color: #ffffff; padding: 0 20px;
}}
QPushButton#cta:hover {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                stop:0 #7C3AED, stop:1 #4F92F7); }}
QPushButton#cta:focus {{ border: 1px solid {C['warm']}; }}
QPushButton#danger {{ background: #3B0A0A; border: 1px solid #7F1D1D; color: #FCA5A5; }}
QPushButton#danger:hover {{ background: #4C0D0D; border: 1px solid #991B1B; }}
QPushButton#danger:disabled {{ background: transparent; border: 1px solid {C['border']};
                               color: {C['disabled']}; }}

QSlider {{ min-height: 20px; max-height: 20px; }}
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: transparent;
}}
QSlider::add-page:horizontal {{
    height: 4px; border-radius: 2px; background: rgba(255,255,255,0.13);
}}
QSlider::sub-page:horizontal {{
    height: 4px; border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                                stop:0 {C['accent']}, stop:1 {C['warm']});
}}
QSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -6px 0; border-radius: 8px;
    background: {C['text']}; border: 2px solid {C['accent']};
}}
QSlider::handle:horizontal:hover {{ background: #ffffff; border: 2px solid {C['warm']}; }}
/* `QSlider:focus::handle:horizontal` is the form the Qt docs imply and it is
   MISPARSED: the declarations land on the QSlider widget itself and paint a
   border around every slider regardless of focus, which stacks into what looks
   like a grid drawn over the knobs. Bisected 8 Sep by stripping one rule at a
   time. State goes after the subcontrol. */
QSlider::handle:horizontal:focus {{ border: 2px solid {C['warm']};
                                    background: #ffffff; }}
"""


def rgba(spec: tuple) -> QtGui.QColor:
    r, g, b, a = spec
    return QtGui.QColor(r, g, b, int(a * 255))


def tracked(pt: float, spacing: float = 0.0, bold: bool = False,
            mono: bool = False) -> QtGui.QFont:
    """Qt Style Sheets have no `letter-spacing`, so VV's tracked uppercase labels
    have to be set on the QFont directly."""
    f = QtGui.QFont("Consolas") if mono else QtGui.QFont()
    f.setPointSizeF(pt)
    f.setBold(bold)
    if spacing:
        f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


def animations_wanted() -> bool:
    """Windows' equivalent of `prefers-reduced-motion`: Settings -> Accessibility
    -> Visual effects -> Animation effects. SPI_GETCLIENTAREAANIMATION."""
    try:
        enabled = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value)
    except Exception:
        return True


def section(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text.upper())
    label.setObjectName("section")
    label.setFont(tracked(7.5, 1.6, bold=True))
    return label


class Pill(QtWidgets.QAbstractButton):
    """VV never uses a raw checkbox — it uses an animated pill with a track and a
    dot. Kept a real QAbstractButton so Space still toggles it and it still takes
    a focus ring, which a hand-painted QWidget would quietly lose."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setText(text)
        self.setFont(tracked(8.5))
        self._pos = 0.0
        self._anim = QtCore.QPropertyAnimation(self, b"dot", self)
        self._anim.setDuration(200 if animations_wanted() else 0)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        metrics = QtGui.QFontMetrics(self.font())
        self.setMinimumSize(metrics.horizontalAdvance(text) + 62, 32)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_dot(self) -> float:
        return self._pos

    def set_dot(self, value: float) -> None:
        self._pos = value
        self.update()

    dot = QtCore.Property(float, get_dot, set_dot)

    def setChecked(self, on: bool) -> None:      # noqa: N802 - Qt casing
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0           # no animation on the initial state
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(C["accent"] if on else "#ffffff"),
                            1.0))
        p.setOpacity(0.35 if on else 0.15)
        p.setBrush(QtGui.QColor(255, 112, 67, 26) if on
                   else QtGui.QColor(255, 255, 255, 10))
        p.drawRoundedRect(r, 10, 10)
        p.setOpacity(1.0)
        if self.hasFocus():
            p.setPen(QtGui.QPen(QtGui.QColor(C["warm"]), 1.0))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(-1, -1, 1, 1), 11, 11)
        track = QtCore.QRectF(12, r.center().y() - 7, 24, 14)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(C["warm"]) if on else QtGui.QColor(255, 255, 255, 38))
        p.drawRoundedRect(track, 7, 7)
        x = track.left() + 2 + self._pos * 12
        p.setBrush(QtGui.QColor("#ffffff"))
        p.drawEllipse(QtCore.QRectF(x, track.top() + 2, 10, 10))
        p.setPen(QtGui.QColor(C["warm"] if on else C["label"]))
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(44, 0, self.width() - 50, self.height()),
                   int(QtCore.Qt.AlignmentFlag.AlignVCenter
                       | QtCore.Qt.AlignmentFlag.AlignLeft), self.text())
        p.end()


class Combo(QtWidgets.QComboBox):
    """Qt draws no arrow once `down-arrow` is blanked, and the CSS
    border-triangle trick that would replace it renders as a filled square in
    QSS. Two strokes, painted over the styled box."""

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(C["label"]), 1.6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 19.0, self.height() / 2.0
        p.drawPolyline([QtCore.QPointF(cx - 4, cy - 2),
                        QtCore.QPointF(cx, cy + 2.5),
                        QtCore.QPointF(cx + 4, cy - 2)])
        p.end()


class JumpToClick(QtWidgets.QProxyStyle):
    """Qt jumps a slider to the click only for the buttons this hint names,
    which is the middle button out of the box. The left button pages toward the
    click instead, one pageStep per press: 1% of the range here."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QtWidgets.QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return QtCore.Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


class Slider(QtWidgets.QSlider):
    """Two things a stock QSlider gets wrong for auditioning.

    A click on the track steps toward the click instead of going there. With
    the proxy style it jumps, and the same press carries on as a drag, which is
    Qt's own path for the middle button.

    And a drag emits valueChanged for every pixel. Each one restarted the cue,
    which `Cues.play()` does by design, so a drag stacked dozens of restarts
    into a screech. `held` says a mouse gesture is in progress, and `settled`
    fires once when it ends, if the value moved."""

    settled = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__(QtCore.Qt.Orientation.Horizontal)
        # Per slider rather than app-wide, so the behaviour belongs to the widget
        # whoever built the QApplication (probe_lab builds its own). The proxy
        # wraps a fresh copy of the app's style, so the look does not change;
        # setStyle does not take ownership, hence the attribute.
        self._style = JumpToClick(QtWidgets.QApplication.style().name())
        self.setStyle(self._style)
        self.held = False
        self._from = 0

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        # Set BEFORE Qt handles the press: the jump emits valueChanged from
        # inside it, ahead of sliderPressed, so isSliderDown() is still False.
        self.held = e.button() == QtCore.Qt.MouseButton.LeftButton
        self._from = self.value()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(e)
        if self.held:
            self.held = False
            if self.value() != self._from:
                self.settled.emit()


class StatTile(QtWidgets.QFrame):
    """VV's stat tile: a small card with a MANDATORY glowing left accent bar. The
    glow is three widening translucent strokes rather than a blur — cheaper than
    a QGraphicsEffect per tile, and indistinguishable at 3px."""

    def __init__(self, caption: str, accent: str) -> None:
        super().__init__()
        self.setObjectName("tile")
        self._accent = QtGui.QColor(accent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 12, 10)
        lay.setSpacing(3)
        self.caption = QtWidgets.QLabel(caption.upper())
        self.caption.setObjectName("tileLabel")
        self.caption.setFont(tracked(7, 1.4, bold=True))
        self.value = QtWidgets.QLabel("--")
        self.value.setObjectName("value")
        self.value.setFont(tracked(10.5, 0.0, bold=True, mono=True))
        lay.addWidget(self.caption)
        lay.addWidget(self.value)

    def set_value(self, text: str, accent: str | None = None) -> None:
        self.value.setText(text)
        if accent:
            self._accent = QtGui.QColor(accent)
            self.value.setStyleSheet(f"color: {accent};")
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)                # QSS paints fill + border
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        top, bottom = self.height() * 0.22, self.height() * 0.78
        for grow, alpha in ((3.0, 30), (1.5, 70), (0.0, 255)):
            colour = QtGui.QColor(self._accent)
            colour.setAlpha(alpha)
            p.setBrush(colour)
            p.drawRoundedRect(QtCore.QRectF(0, top - grow, 3 + grow * 2,
                                            (bottom - top) + grow * 2), 2, 2)
        p.end()


class Wave(QtWidgets.QWidget):
    """The gesture drawn end to end. Decay in particular is far easier to
    understand as a shape than as a number."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(82)
        self._audio = np.zeros(1, dtype=np.float32)

    def set_audio(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 180, 120, 38), 1.0))
        p.setBrush(QtGui.QColor(12, 6, 22, 150))
        p.drawRoundedRect(r, 10, 10)
        cy = r.center().y()
        p.setPen(QtGui.QPen(QtGui.QColor(255, 180, 120, 30), 1.0))
        p.drawLine(QtCore.QPointF(r.left() + 10, cy), QtCore.QPointF(r.right() - 10, cy))
        a = self._audio
        if len(a) < 2:
            p.end()
            return
        peak = max(1e-6, float(np.max(np.abs(a))))
        left, width = int(r.left()) + 10, max(1, int(r.width()) - 20)
        edges = np.linspace(0, len(a), width + 1).astype(int)
        grad = QtGui.QLinearGradient(0, r.top(), 0, r.bottom())
        grad.setColorAt(0.0, QtGui.QColor(C["warm"]))
        grad.setColorAt(0.5, QtGui.QColor(C["accent"]))
        grad.setColorAt(1.0, QtGui.QColor(C["warm"]))
        p.setPen(QtGui.QPen(QtGui.QBrush(grad), 1.0))
        half = r.height() / 2.0 - 10.0
        for i in range(width):
            seg = a[edges[i]:max(edges[i] + 1, edges[i + 1])]
            h = float(np.max(np.abs(seg))) / peak * half
            x = left + i
            p.drawLine(QtCore.QPointF(x, cy - h), QtCore.QPointF(x, cy + h))
        p.end()


class Card(QtWidgets.QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("card")
        self.box = QtWidgets.QVBoxLayout(self)
        self.box.setContentsMargins(18, 14, 18, 16)
        self.box.setSpacing(14)


class Lab(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shout — cue lab")
        self.cfg = Config.load()
        self.saved: dict[str, dict] = dict(self.cfg.cue_presets)
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice,
                                   self.saved)
        self.volume = float(self.cfg.cue_volume)
        self.cues = Cues(enabled=True, volume=self.volume,
                         device=self.cfg.output_device, voice=self.voice)
        self._loading = False
        self.setStyleSheet(QSS)
        self._build_ui()
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play=None)
        self._greet()

    def _greet(self) -> None:
        """A voice carried as overrides on a built-in predates named saving, and
        reselecting that built-in would silently discard it. Say so on open
        rather than letting the list quietly disagree with what is playing."""
        n = len(Voice._clean(self.cfg.cue_voice))
        if n and self.cfg.cue_preset in PRESETS:
            self.name.setText(self._free_name(self.cfg.cue_preset))
            self.status.setText(
                f"Playing {self.cfg.cue_preset!r} with {n} tweak(s) that are not "
                f"saved under a name — Save to keep them as "
                f"{self.name.text()!r}, or rename first.")

    # -- background ----------------------------------------------------------

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        """VV's body: a deep base plus four radial blobs. Painted rather than
        set as a flat colour because the cards above it are translucent — with a
        flat base they read as grey boxes, and the blobs ARE the depth that
        `backdrop-filter` would otherwise be providing."""
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QtGui.QColor(C["deep"]))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for fx, fy, radius, colour in BLOBS:
            grad = QtGui.QRadialGradient(0.0, 0.0, radius * max(w, h))
            grad.setColorAt(0.0, rgba(colour))
            grad.setColorAt(1.0, QtGui.QColor(colour[0], colour[1], colour[2], 0))
            p.save()
            p.translate(w * fx, h * fy)
            p.scale(1.35, 1.0)                   # CSS `ellipse at ...`
            p.setBrush(QtGui.QBrush(grad))
            p.drawEllipse(QtCore.QPointF(0, 0), radius * max(w, h),
                          radius * max(w, h))
            p.restore()
        p.end()

    # -- ui ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(12)

        heading = QtWidgets.QLabel("Cue sounds")
        heading.setObjectName("h1")
        heading.setFont(tracked(15, 0.0, bold=True))
        subtitle = QtWidgets.QLabel("Three gestures, one voice. Nothing is saved "
                                    "until you press Save.")
        subtitle.setObjectName("sub")
        subtitle.setFont(tracked(8.5))
        outer.addWidget(heading)
        outer.addWidget(subtitle)

        # -- material, waveform, readout --
        top_card = Card()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        material = section("Material")
        self.preset = Combo()
        self.preset.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.preset.setAccessibleName("Material")
        self.preset.currentTextChanged.connect(self._on_preset)
        self.auto = Pill("Play on change")
        self.auto.setChecked(True)
        row.addWidget(material)
        row.addWidget(self.preset, 1)
        row.addWidget(self.auto)
        top_card.box.addLayout(row)

        self.wave = Wave()
        top_card.box.addWidget(self.wave)

        self.tiles = {
            "notes": StatTile("Notes", C["accent"]),
            "longest": StatTile("Longest gesture", C["warm"]),
            "peak": StatTile("Peak", C["pop"]),
        }
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(10)
        for tile in self.tiles.values():
            tiles.addWidget(tile, 1)
        top_card.box.addLayout(tiles)
        outer.addWidget(top_card)

        # -- knobs --
        voice_card = Card()
        voice_card.box.addWidget(section("Voice"))
        grid = QtWidgets.QGridLayout()
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(14)
        self.sliders: dict[str, Slider] = {}
        self.values: dict[str, QtWidgets.QLabel] = {}
        rows = KNOBS + [("_volume", "Volume", 0.0, 1.0, 2)]
        for row_i, (fieldname, label, lo, hi, dp) in enumerate(rows):
            caption = QtWidgets.QLabel(label)
            caption.setObjectName("knob")
            caption.setFont(tracked(8.5))
            grid.addWidget(caption, row_i, 0)
            s = Slider()
            s.setRange(0, STEPS)
            s.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            s.setAccessibleName(label)
            s.valueChanged.connect(self._on_slider)
            s.settled.connect(self._on_settled)
            grid.addWidget(s, row_i, 1)
            v = QtWidgets.QLabel()
            v.setObjectName("value")
            v.setFont(tracked(9, 0.0, mono=True))
            v.setMinimumWidth(52)
            v.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                           | QtCore.Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(v, row_i, 2)
            self.sliders[fieldname] = s
            self.values[fieldname] = v
        grid.setColumnStretch(1, 1)
        voice_card.box.addLayout(grid)
        outer.addWidget(voice_card)

        # -- audition + save --
        save_card = Card()
        save_card.box.addWidget(section("Audition"))
        play = QtWidgets.QHBoxLayout()
        play.setSpacing(8)
        for name, text in (("start", "Start"), ("stop", "Stop"),
                           ("latch", "Latch")):
            b = QtWidgets.QPushButton(text)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self._play(n))
            play.addWidget(b, 1)
        seq = QtWidgets.QPushButton("All three")
        seq.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        seq.clicked.connect(self._play_all)
        play.addWidget(seq, 1)
        save_card.box.addLayout(play)

        save_card.box.addSpacing(4)
        save_card.box.addWidget(section("Save as"))
        named = QtWidgets.QHBoxLayout()
        named.setSpacing(8)
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("a name for this voice")
        self.name.setAccessibleName("Voice name")
        self.name.returnPressed.connect(self._save)
        named.addWidget(self.name, 1)
        self.delete = QtWidgets.QPushButton("Delete")
        self.delete.setObjectName("danger")
        self.delete.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.delete.clicked.connect(self._delete)
        named.addWidget(self.delete)
        revert = QtWidgets.QPushButton("Revert")
        revert.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        revert.clicked.connect(self._revert)
        named.addWidget(revert)
        save = QtWidgets.QPushButton("Save to config")
        save.setObjectName("cta")
        save.setDefault(True)
        save.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        save.clicked.connect(self._save)
        glow = QtWidgets.QGraphicsDropShadowEffect(self)
        glow.setBlurRadius(28)
        glow.setOffset(0, 4)
        glow.setColor(QtGui.QColor(109, 40, 217, 150))
        save.setGraphicsEffect(glow)
        named.addWidget(save)
        save_card.box.addLayout(named)

        self.status = QtWidgets.QLabel("")
        self.status.setObjectName("status")
        self.status.setFont(tracked(8.5))
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(30)
        save_card.box.addWidget(self.status)
        outer.addWidget(save_card)
        outer.addStretch(1)

        # Sized FROM the layout's own minimum, not from a number that looked
        # right: at 736 every label was rendering 4-5px under its minimumSizeHint
        # -- legible at this DPI, clipped at another -- and a hand-picked
        # minimumSize of 700 let the window be dragged further into that. Qt
        # enforces the layout minimum on its own once nothing overrides it.
        self.resize(600, self.sizeHint().height())

    def _rebuild_list(self) -> None:
        """Materials first, then your own voices under a separator. Rebuilt
        rather than appended to, so a delete cannot leave a stale row."""
        self._loading = True
        try:
            self.preset.clear()
            self.preset.addItems(list(PRESETS))
            if self.saved:
                self.preset.insertSeparator(self.preset.count())
                self.preset.addItems(sorted(self.saved))
        finally:
            self._loading = False

    # -- slider <-> voice ----------------------------------------------------

    @staticmethod
    def _to_slider(value: float, lo: float, hi: float) -> int:
        return int(round((value - lo) / (hi - lo) * STEPS))

    @staticmethod
    def _from_slider(pos: int, lo: float, hi: float) -> float:
        return lo + (hi - lo) * pos / STEPS

    def _sync_widgets(self) -> None:
        """Push the current voice into the widgets without re-triggering."""
        self._loading = True
        try:
            i = self.preset.findText(self.voice.name)
            self.preset.setCurrentIndex(i if i >= 0 else 0)
            self.name.setText(self.voice.name)
            for fieldname, _, lo, hi, _dp in KNOBS:
                value = getattr(self.voice, fieldname)
                self.sliders[fieldname].setValue(self._to_slider(value, lo, hi))
            self.sliders["_volume"].setValue(self._to_slider(self.volume, 0.0, 1.0))
        finally:
            self._loading = False
        self._label_values()
        self.delete.setEnabled(self.voice.name in self.saved)

    def _label_values(self) -> None:
        for fieldname, _, _lo, _hi, dp in KNOBS:
            self.values[fieldname].setText(f"{getattr(self.voice, fieldname):.{dp}f}")
        self.values["_volume"].setText(f"{self.volume:.2f}")

    def _read_widgets(self) -> None:
        fields = {f: self._from_slider(self.sliders[f].value(), lo, hi)
                  for f, _, lo, hi, _dp in KNOBS}
        self.voice = replace(self.voice, **fields)
        self.volume = self._from_slider(self.sliders["_volume"].value(), 0.0, 1.0)

    def _differs_from(self, base: Voice) -> bool:
        """A knob counts as moved only if it moved by more than one slider step.
        Reading a slider back gives a quantized value, so an exact comparison
        calls knobs you never dragged moved."""
        for fieldname, _, lo, hi, _dp in KNOBS:
            if abs(getattr(self.voice, fieldname) - getattr(base, fieldname)) \
                    > (hi - lo) / STEPS:
                return True
        return self.voice.partials != base.partials

    def _free_name(self, base: str) -> str:
        taken = set(PRESETS) | set(self.saved)
        for n in range(2, 100):
            if (candidate := f"{base} {n}") not in taken:
                return candidate
        return f"{base} copy"

    # -- events --------------------------------------------------------------

    def _load_named(self, name: str) -> Voice:
        return (Voice.from_saved(name, self.saved[name]) if name in self.saved
                and name not in PRESETS else PRESETS[name])

    def _on_preset(self, name: str) -> None:
        if self._loading or not name:
            return
        self.voice = self._load_named(name)
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText("")

    def _on_slider(self) -> None:
        if self._loading:
            return
        self._read_widgets()
        self._label_values()
        # Editing a material renames what you are editing, so Save adds an entry
        # instead of redefining a shipped sound. Only while the box still holds
        # the material's own name — once it is your name, it stays yours.
        if self.name.text().strip() in PRESETS and self._differs_from(
                PRESETS[self.name.text().strip()]):
            self.name.setText(self._free_name(self.name.text().strip()))
        # Mid-drag the wave, the readouts and the synth all follow the knob; only
        # the audition waits for the release. Keys still play on every step.
        dragging = any(s.held for s in self.sliders.values())
        self._apply(play="start" if self.auto.isChecked() and not dragging
                    else None)

    def _on_settled(self) -> None:
        if self.auto.isChecked():
            self._play("start")

    def _apply(self, play: str | None) -> None:
        self.cues.set_volume(self.volume)
        self.cues.set_voice(self.voice)
        self.wave.set_audio(build("latch", self.cues.rate, self.volume, self.voice))
        longest = max(duration_s(n, self.voice) for n in ("start", "stop", "latch"))
        peak = float(np.max(np.abs(build("start", self.cues.rate, self.volume,
                                         self.voice))))
        over = longest > MAX_GESTURE_S
        self.tiles["notes"].set_value(
            "  ".join(f"{f:.0f}" for f, _ in notes("latch", self.voice)) + " Hz")
        self.tiles["longest"].set_value(
            f"{longest * 1000:.0f}ms" + ("  OVER" if over else ""),
            C["negative"] if over else C["text"])
        self.tiles["longest"].caption.setText(
            "OVER THE BLIP CEILING" if over else "LONGEST GESTURE")
        self.tiles["peak"].set_value(f"{peak:.3f}")
        if play:
            self._play(play)

    def _play(self, name: str) -> None:
        self.cues.play(name)

    def _play_all(self) -> None:
        """start, stop, then latch, spaced so they do not cut each other off —
        `play()` replaces whatever is sounding."""
        delay = 0.0
        for name in ("start", "stop", "latch"):
            QtCore.QTimer.singleShot(int(delay * 1000),
                                     lambda n=name: self.cues.play(n))
            delay += duration_s(name, self.voice) + 0.25

    def _revert(self) -> None:
        self.cfg = Config.load()
        self.saved = dict(self.cfg.cue_presets)
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice,
                                   self.saved)
        self.volume = float(self.cfg.cue_volume)
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText("Reverted to what is saved.")

    # -- persistence ---------------------------------------------------------

    def _read(self) -> dict:
        """The file, raw. `self.cfg` is a snapshot from construction and goes
        stale the moment Save writes — so any question about what is CURRENTLY
        configured has to be asked of the file. Caught by probe_lab on its first
        run: Delete read the active preset off the snapshot and left config.json
        naming a voice it had just removed."""
        path = config_dir() / "config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, keys: dict) -> Path:
        """Merge into config.json rather than rewriting it — the file may hold
        settings this window knows nothing about, and Config.save() would write
        this process's defaults over every one of them."""
        path = config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = self._read()
        raw.update(keys)
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return path

    def _voice_dict(self) -> dict:
        """The whole voice, not a diff against a material. `partials` is in here
        and is not a slider: a voice tuned out of marimba needs marimba's
        partials or it will not sound like the thing you tuned."""
        data = {k: v for k, v in asdict(self.voice).items() if k != "name"}
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in data.items()}

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.status.setText("Give the voice a name first.")
            return
        if name in PRESETS:
            if self._differs_from(PRESETS[name]):
                self.name.setText(self._free_name(name))
                self.status.setText(
                    f"{name!r} is a built-in material and is not redefinable. "
                    f"Renamed to {self.name.text()!r} — press Save again.")
                return
            keys = {"cue_preset": name, "cue_voice": {},
                    "cue_volume": round(self.volume, 3)}
            what = f"material {name!r}"
        else:
            existed = name in self.saved
            self.saved[name] = self._voice_dict()
            keys = {"cue_preset": name, "cue_voice": {},
                    "cue_presets": self.saved,
                    "cue_volume": round(self.volume, 3)}
            what = f"{name!r} ({'updated' if existed else 'new'})"
        self._write(keys)
        self.voice = replace(self.voice, name=name)
        self._rebuild_list()
        self._sync_widgets()
        # No path in here: a long one wraps this to a third line, which the
        # window does not grow for, and the slider card pays the 15px.
        self.status.setText(
            f"Saved {what}. Shout plays it from your next dictation, no restart "
            f"needed. Then re-run harness/probe_cues.py.")

    def _delete(self) -> None:
        name = self.voice.name
        if name not in self.saved:
            self.status.setText("Only your own saved voices can be deleted.")
            return
        del self.saved[name]
        keys = {"cue_presets": self.saved}
        # Not `self.voice.name`: deleting a voice you are merely LOOKING at must
        # leave the active one alone, and not `self.cfg` either — see _read.
        if self._read().get("cue_preset") == name:
            keys |= {"cue_preset": "blip", "cue_voice": {}}
        self._write(keys)
        self.cfg = Config.load()
        self.voice = PRESETS["blip"]
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText(f"Deleted {name!r}. Now on 'blip'.")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.cues.close()
        super().closeEvent(event)


def application(argv: list[str]) -> QtWidgets.QApplication:
    """The QApplication as the lab runs it, and as probe_lab builds it too. The
    style decides how a slider takes a click: Windows 11's jumps to it, Fusion's
    steps toward it. So a gate left on the default style passed a jump the lab
    never made."""
    app = QtWidgets.QApplication(argv)
    app.setStyle("Fusion")
    return app


def main() -> int:
    app = application(sys.argv)
    lab = Lab()
    lab.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
