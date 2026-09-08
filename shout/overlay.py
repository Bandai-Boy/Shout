"""The state pill — a small always-on-top bubble above the taskbar.

Persistent: it is on screen whenever Shout is running, collapsed to a quiet
lozenge when idle and expanding when you dictate. That is a change from session
2, where it only existed while recording — which made it impossible to judge how
it looked, because you could only ever see it mid-sentence.

Qt rather than Tk, and the four Tk traps recorded in CLAUDE.md do not port:
three of them simply do not exist here, measured 8 Sep 2026 before any of this
was written.

    winId() IS the top-level hwnd          (Tk wrapped it; Qt does not)
    show() does not activate               (WA_ShowWithoutActivating; Tk's
                                            deiconify() called SetForegroundWindow
                                            unconditionally)
    translucency does not rewrite the       (WA_TranslucentBackground is real
    ex-style                                 per-pixel alpha, not Tk's chroma key)
    geometry is not deferred                 (no withdrawn state involved)

The one thing that carries over unchanged is the *reason* for the flags. An
overlay that takes focus breaks dictation with the app's own UI, silently,
because the text then goes nowhere. Qt's flags produce WS_EX_NOACTIVATE,
WS_EX_TOOLWINDOW and WS_EX_TRANSPARENT on their own, but `harness/probe_overlay`
reads the actual bits off the window rather than trusting the mapping, and
measures focus against a positive control that must be seen stealing it.

Per-pixel alpha is what makes this look like a designed object rather than a
chroma-keyed rectangle: Tk's `-transparentcolor` is an exact-match colour test,
so every rounded corner was stair-stepped and a soft shadow was impossible.

Threading: Qt owns the main thread and nothing outside this module touches a
widget. `set_state()` and `set_level()` are attribute rebinds, atomic under the
GIL, and a timer on the GUI thread reads them — the same discipline the Tk
version used, kept because it is simpler than marshalling every level sample
through a queued signal at 60Hz.
"""
from __future__ import annotations

import collections
import ctypes
import logging
import math
import time
from ctypes import wintypes

from PySide6 import QtCore, QtGui, QtWidgets

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

GWL_EXSTYLE = -20
GA_ROOT = 2
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WANTED_EXSTYLE = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT

# SHQueryUserNotificationState — the documented "should I put something on
# screen right now" query. It covers exclusive-fullscreen D3D, which comparing
# window rectangles does not.
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
FULLSCREEN_STATES = (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]

_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t

# -- geometry ----------------------------------------------------------------

HEIGHT = 44
COLLAPSED_W = 78
EXPANDED_W = 268
MARGIN_Y = 14           # gap between the pill and the top of the taskbar
SHADOW = 10             # painted margin around the pill for the drop shadow

# The window is always the largest it can get, so it never resizes while
# animating — only what is painted inside it changes. Resizing a layered
# top-level 60 times a second is visibly janky and makes placement race.
WIN_W = EXPANDED_W + SHADOW * 2
WIN_H = HEIGHT + SHADOW * 2

FRAME_MS = 16           # 60Hz while anything is moving
# Once settled the tick does almost nothing — the repaint is gated behind a
# dirty flag — so this only has to be slow enough to matter and fast enough that
# the pill starts reacting before you notice. At 120ms the expansion visibly
# lagged the chord.
IDLE_FRAME_MS = 33

# -- palette -----------------------------------------------------------------

BG = QtGui.QColor(20, 20, 26, 236)
BORDER = QtGui.QColor(255, 255, 255, 26)
TEXT = QtGui.QColor(240, 240, 244)
TEXT_DIM = QtGui.QColor(240, 240, 244, 130)
METER_BG = QtGui.QColor(255, 255, 255, 28)

ACCENT = {
    "loading": QtGui.QColor(150, 150, 162),
    "idle": QtGui.QColor(96, 126, 168),
    "recording": QtGui.QColor(228, 68, 68),
    "latched": QtGui.QColor(255, 202, 60),
    "working": QtGui.QColor(235, 168, 50),
    "error": QtGui.QColor(200, 64, 64),
}
LABEL = {
    "loading": "Starting",
    "idle": "",
    "recording": "Recording",
    "latched": "Hands-free",
    "working": "Transcribing",
    "error": "Error",
}
# States that expand the pill. Idle stays collapsed; that IS the resting look.
EXPANDED_STATES = ("loading", "recording", "latched", "working", "error")
# States that draw the live level meter. "working" animates instead — the mic is
# closed by then, and a bar still falling implies a microphone that is not open.
METERED_STATES = ("recording", "latched")

BARS = 27
BAR_W = 3.0
BAR_GAP = 2.0
BAR_MIN = 2.0

# Level meter: map dBFS onto the bar. Quiet speech sits near -40dB.
DB_FLOOR, DB_CEIL = -52.0, -12.0
ATTACK, RELEASE = 0.55, 0.16
EXPAND_RATE = 0.22      # per frame, toward the target width


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _ease(t: float) -> float:
    """Smoothstep. The pill decelerating into place is most of what makes the
    expansion read as a physical object rather than a resize."""
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def fullscreen_app_running() -> bool:
    state = ctypes.c_int(0)
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return False
    return state.value in FULLSCREEN_STATES


class PillWidget(QtWidgets.QWidget):
    """Paints the pill. Owns no state of its own beyond what it is told to draw."""

    def __init__(self, overlay: "Overlay") -> None:
        super().__init__(None)
        self._ov = overlay
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.Tool                      # out of alt-tab
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.WindowTransparentForInput  # click-through
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(WIN_W, WIN_H)
        self._font = QtGui.QFont("Segoe UI", 9)
        self._font.setWeight(QtGui.QFont.Weight.Medium)

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        ov = self._ov
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

        width = ov.pill_width
        x = (WIN_W - width) / 2.0
        y = float(SHADOW)
        r = HEIGHT / 2.0
        body = QtCore.QRectF(x, y, width, HEIGHT)

        # Soft shadow: a few progressively larger, fainter rounded rects. Cheaper
        # per frame than a QGraphicsDropShadowEffect, which re-renders the widget
        # into an offscreen buffer on every repaint.
        for i in range(SHADOW, 0, -2):
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(0, 0, 0, 6))
            p.drawRoundedRect(body.adjusted(-i, -i * 0.6, i, i * 0.6),
                              r + i, r + i * 0.6)

        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(BG)
        p.drawRoundedRect(body, r, r)
        p.setPen(QtGui.QPen(BORDER, 1.0))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(body.adjusted(0.5, 0.5, -0.5, -0.5), r, r)

        accent = ACCENT.get(ov.state, ACCENT["idle"])
        cy = y + HEIGHT / 2.0

        # Centred while collapsed, sliding to a fixed inset as the pill grows.
        # Pinning it to the left edge outright leaves it visibly off-centre in
        # the resting lozenge, which is the shape that is on screen all day.
        dot_x = x + _lerp(COLLAPSED_W / 2.0, 20.0, _ease(self._ov.expand))
        dot_r = 4.5
        if ov.state in METERED_STATES:
            # A halo scaled by input level: visible confirmation that it is
            # hearing you, which survives being seen out of the corner of an eye.
            halo = dot_r + 5.0 * ov.meter
            p.setBrush(QtGui.QColor(accent.red(), accent.green(), accent.blue(), 60))
            p.drawEllipse(QtCore.QPointF(dot_x, cy), halo, halo)
        p.setBrush(accent)
        p.drawEllipse(QtCore.QPointF(dot_x, cy), dot_r, dot_r)

        if ov.expand <= 0.02:
            p.end()
            return

        # Everything past the dot fades in over the second half of the expansion,
        # so text never appears crushed against the pill's edge mid-animation.
        reveal = _ease(max(0.0, (ov.expand - 0.35) / 0.65))
        if reveal <= 0.01:
            p.end()
            return
        p.setOpacity(reveal)

        label = LABEL.get(ov.state, "")
        text_x = dot_x + 14.0
        if label:
            p.setFont(self._font)
            p.setPen(TEXT if ov.state != "loading" else TEXT_DIM)
            p.drawText(QtCore.QRectF(text_x, y, 120, HEIGHT),
                       int(QtCore.Qt.AlignmentFlag.AlignVCenter
                           | QtCore.Qt.AlignmentFlag.AlignLeft), label)

        meter_left = x + 128.0
        meter_right = x + width - 18.0
        if meter_right - meter_left > 20.0:
            if ov.state in METERED_STATES:
                self._draw_bars(p, meter_left, meter_right, cy, accent)
            elif ov.state == "working":
                self._draw_working(p, meter_left, meter_right, cy, accent)
        p.end()

    def _draw_bars(self, p: QtGui.QPainter, left: float, right: float,
                   cy: float, accent: QtGui.QColor) -> None:
        """Mirrored bars, oldest at the left. A scrolling history rather than a
        single number is what makes it read as a waveform — one bar rising and
        falling reads as a VU meter, which says far less about whether the words
        you just said were heard."""
        ov = self._ov
        span = right - left
        step = span / BARS
        w = min(BAR_W, step - BAR_GAP)
        half = HEIGHT / 2.0 - 9.0
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        hist = ov.history
        for i in range(BARS):
            v = hist[i] if i < len(hist) else 0.0
            h = max(BAR_MIN, half * 2.0 * v)
            # Newer bars are brighter, which gives the row a direction of travel.
            a = 90 + int(140 * (i / max(1, BARS - 1)))
            p.setBrush(QtGui.QColor(accent.red(), accent.green(), accent.blue(), a))
            p.drawRoundedRect(QtCore.QRectF(left + i * step, cy - h / 2.0, w, h),
                              w / 2.0, w / 2.0)

    def _draw_working(self, p: QtGui.QPainter, left: float, right: float,
                      cy: float, accent: QtGui.QColor) -> None:
        """An indeterminate travelling wave. Nothing is being heard while the
        model runs, so showing a level here would be a lie about the microphone."""
        span = right - left
        step = span / BARS
        w = min(BAR_W, step - BAR_GAP)
        phase = time.perf_counter() * 3.0
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        for i in range(BARS):
            k = math.sin(phase - i * 0.38)
            v = 0.5 + 0.5 * k
            h = max(BAR_MIN, (HEIGHT / 2.0 - 9.0) * 2.0 * (0.18 + 0.55 * v * v))
            p.setBrush(QtGui.QColor(accent.red(), accent.green(), accent.blue(),
                                    70 + int(120 * v)))
            p.drawRoundedRect(QtCore.QRectF(left + i * step, cy - h / 2.0, w, h),
                              w / 2.0, w / 2.0)


class Overlay:
    """Public surface. Everything outside this module talks to it by attribute."""

    def __init__(self, enabled: bool = True, level_source=None) -> None:
        self.enabled = enabled
        self._level_source = level_source
        self._state = "loading"       # written by any thread
        self._level = 0.0             # written by the audio callback
        self.state = "loading"        # GUI-thread copy, read by the painter
        self.meter = 0.0
        self.expand = 0.0
        self.pill_width = float(COLLAPSED_W)
        self.history: collections.deque[float] = collections.deque(
            [0.0] * BARS, maxlen=BARS)
        self.widget: PillWidget | None = None
        self._timer: QtCore.QTimer | None = None
        self._hwnd = 0
        self._placed: tuple[int, int] | None = None
        self._shown = False
        self._dirty = True
        self._last_fullscreen_check = 0.0
        self._fullscreen = False
        self._stopped = False

    # -- called from other threads (attribute rebinds only) -----------------

    def set_state(self, state: str) -> None:
        self._state = state

    def set_level(self, rms: float) -> None:
        self._level = rms

    # -- construction (GUI thread only) -------------------------------------

    def build(self) -> None:
        if not self.enabled:
            return
        self.widget = PillWidget(self)
        # winId() realises the window. Measured: for a Qt top-level this IS the
        # top-level hwnd, so no GetAncestor walk is needed the way Tk required.
        self._hwnd = int(self.widget.winId())
        self._enforce_exstyle()
        self._reposition(force=True)
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)
        log.info("overlay ready (hwnd=%d)", self._hwnd)

    def _enforce_exstyle(self) -> None:
        """Qt's flags already produce all three bits — measured before this was
        written. Re-asserting them is cheap insurance against a future Qt or
        style change silently dropping one, which would be invisible until
        dictation started going nowhere."""
        if not self._hwnd:
            return
        h = wintypes.HWND(self._hwnd)
        current = _get_long(h, GWL_EXSTYLE)
        if (current & WANTED_EXSTYLE) != WANTED_EXSTYLE:
            _set_long(h, GWL_EXSTYLE, current | WANTED_EXSTYLE)

    def has_exstyle(self) -> bool:
        if not self._hwnd:
            return False
        got = _get_long(wintypes.HWND(self._hwnd), GWL_EXSTYLE)
        return (got & WANTED_EXSTYLE) == WANTED_EXSTYLE

    # -- placement ----------------------------------------------------------

    def _screen(self) -> QtGui.QScreen:
        """The screen holding the focused window — not the primary one. Qt's
        availableGeometry already excludes the taskbar wherever it lives, and
        was measured to agree exactly with Win32's rcWork on this setup."""
        fg = user32.GetForegroundWindow()
        if fg:
            r = wintypes.RECT()
            if user32.GetWindowRect(fg, ctypes.byref(r)):
                mid = QtCore.QPoint((r.left + r.right) // 2,
                                    (r.top + r.bottom) // 2)
                scr = QtGui.QGuiApplication.screenAt(mid)
                if scr is not None:
                    return scr
        return QtGui.QGuiApplication.primaryScreen()

    def work_area(self) -> QtCore.QRect:
        return self._screen().availableGeometry()

    def _reposition(self, force: bool = False) -> None:
        a = self.work_area()
        x = a.left() + (a.width() - WIN_W) // 2
        y = a.top() + a.height() - WIN_H - MARGIN_Y + SHADOW
        if not force and (x, y) == self._placed:
            return
        self.widget.move(x, y)
        self._placed = (x, y)

    # -- the GUI-thread loop -------------------------------------------------

    def _visible_for(self, state: str) -> bool:
        """Persistent, with one exception: a fullscreen app or a game gets the
        screen to itself while Shout is merely idle. It never hides while
        dictating — that is precisely when the confirmation matters, and a
        surprise-free overlay is worth less than a truthful one."""
        if state in ("recording", "latched", "working"):
            return True
        return not self._fullscreen

    def _tick(self) -> None:
        if self._stopped:
            return
        try:
            self._update()
        except Exception:
            log.exception("overlay tick")

    def _update(self) -> None:
        now = time.perf_counter()
        if now - self._last_fullscreen_check > 0.5:
            self._last_fullscreen_check = now
            was = self._fullscreen
            self._fullscreen = fullscreen_app_running()
            if was != self._fullscreen:
                self._dirty = True

        state = self._state
        if state != self.state:
            self.state = state
            self._dirty = True

        want_visible = self._visible_for(state)
        if want_visible and not self._shown:
            self.widget.show()
            self._enforce_exstyle()
            self._shown = True
            self._dirty = True
        elif not want_visible and self._shown:
            self.widget.hide()
            self._shown = False
        if self._shown:
            before = self._placed
            self._reposition()
            if self._placed != before:
                self._dirty = True

        # -- expansion ------------------------------------------------------
        target = 1.0 if state in EXPANDED_STATES else 0.0
        if abs(self.expand - target) > 0.001:
            self.expand += (target - self.expand) * EXPAND_RATE
            if abs(self.expand - target) <= 0.001:
                self.expand = target
            self._dirty = True
        width = _lerp(COLLAPSED_W, EXPANDED_W, _ease(self.expand))
        if abs(width - self.pill_width) > 0.01:
            self.pill_width = width
            self._dirty = True

        # -- level ----------------------------------------------------------
        if state in METERED_STATES:
            raw = self._level_source() if self._level_source else self._level
            t = self._to_bar(raw)
            rate = ATTACK if t > self.meter else RELEASE
            self.meter += (t - self.meter) * rate
            self.history.append(self.meter)
            self._dirty = True
        elif state == "working":
            self.meter = 0.0
            self._dirty = True          # the travelling wave animates on its own
        elif self.meter != 0.0 or any(self.history):
            self.meter = 0.0
            self.history.extend([0.0] * BARS)
            self._dirty = True

        # Repaint only when something moved. A persistent overlay that repaints
        # 60 times a second while idle is a background CPU cost for the whole
        # time the machine is on, which is most of why the pill was hidden before.
        if self._dirty and self._shown:
            self.widget.update()
            self._dirty = False
        settled = (not self._dirty and self.expand in (0.0, 1.0)
                   and state not in METERED_STATES and state != "working")
        want_interval = IDLE_FRAME_MS if settled else FRAME_MS
        if self._timer.interval() != want_interval:
            self._timer.setInterval(want_interval)

    @staticmethod
    def _to_bar(rms: float) -> float:
        if rms <= 1e-7:
            return 0.0
        db = 20.0 * math.log10(rms)
        return min(1.0, max(0.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))

    # -- lifecycle -----------------------------------------------------------

    def stop(self) -> None:
        """Callable from any thread: sets a flag and nothing else.

        Quitting is triggered from the tray menu, which is the GUI thread, but
        the probe drives it from a worker and a future settings window or a
        signal handler could too. A QTimer or a QWidget touched from the wrong
        thread does not raise — it corrupts quietly — so the actual teardown
        waits for `teardown()`, which runs after the event loop returns."""
        self._stopped = True

    def teardown(self) -> None:
        """GUI thread only, after app.exec() has returned."""
        if self._timer is not None:
            self._timer.stop()
        if self.widget is not None:
            self.widget.hide()
            self.widget.deleteLater()
            self._shown = False
