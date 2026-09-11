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
WS_EX_TOPMOST = 0x00000008
GW_HWNDPREV = 3
HWND_TOPMOST = wintypes.HWND(-1)
SWP_RESTACK = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
DWMWA_CLOAKED = 14

# SHQueryUserNotificationState — the documented "should I put something on
# screen right now" query. It covers exclusive-fullscreen D3D, which comparing
# window rectangles does not, but it names no monitor, and measured 10 Sep 2026
# it answered ACCEPTS_NOTIFICATIONS for a fullscreen Chrome window on the second
# monitor even while that window had focus. So it only decides the states that
# are global by nature; where a fullscreen window is comes from
# fullscreen_monitors().
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
EVERYWHERE_STATES = (QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE)
GWL_STYLE = -16
WS_CAPTION = 0x00C00000
MONITOR_DEFAULTTONULL = 0
MONITOR_DEFAULTTONEAREST = 2
SM_CMONITORS = 80
DESKTOP_CLASSES = ("Progman", "WorkerW")    # the wallpaper, which spans every monitor


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
user32.MonitorFromPoint.restype = ctypes.c_void_p
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = ctypes.c_void_p
user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
shell32.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]

_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t

dwmapi = ctypes.WinDLL("dwmapi")
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.SetWindowPos.restype = wintypes.BOOL
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p,
                                         wintypes.DWORD]
# Undocumented, but exported by user32 since Windows 8 and stable since. Without
# it every window counts as the pill's band, which only costs the band filter.
_GetWindowBand = getattr(user32, "GetWindowBand", None)
if _GetWindowBand is not None:
    _GetWindowBand.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _GetWindowBand.restype = wintypes.BOOL


def _band(h) -> int:
    b = wintypes.DWORD(0)
    if _GetWindowBand is None or not _GetWindowBand(h, ctypes.byref(b)):
        return 0
    return b.value


def _cloaked(h) -> bool:
    """True for a window on another virtual desktop: 'visible', but not here."""
    c = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(h, DWMWA_CLOAKED, ctypes.byref(c), ctypes.sizeof(c))
    return bool(c.value)


def _class(h) -> str:
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(h, cls, 128)
    return cls.value


def _describe(h: int) -> str:
    """Class and pid, deliberately not the title: titles carry document names,
    and shout.log is the file people paste into bug reports."""
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(h), ctypes.byref(pid))
    return f"{_class(wintypes.HWND(h))!r} (pid {pid.value})"

# -- geometry ----------------------------------------------------------------

HEIGHT = 31
# The idle pill is its own shape, not a narrower expanded one: it is on screen
# all day, so it should be a sliver, while the states you actually look at keep
# their full height. Height grows with the same eased curve as width, from a
# fixed bottom edge, so the sliver swells upward into the recording pill.
COLLAPSED_W = 52
COLLAPSED_H = 10
IDLE_DOT_R = 0.0        # 0 = the idle pill carries no dot at all
MARGIN_Y = 14           # gap between the pill and the top of the taskbar
SHADOW = 10             # painted margin around the pill for the drop shadow

# Expanded width is DERIVED per state rather than being one constant, because
# the states carry different amounts of text and a width sized for the longest
# of them leaves the one you see most — recording, which now carries no label at
# all — padded with dead space. The pieces below are the layout; the width is
# whatever they add up to, so removing a word actually shortens the pill instead
# of just moving the gap around.
DOT_INSET = 20.0        # dot centre from the pill's left edge, fully expanded
LABEL_GAP = 14.0        # dot centre -> start of the label
METER_GAP = 12.0        # end of the label -> start of the bars
METER_W = 122.0         # span of the bar row; unchanged, so waveform density is
RIGHT_INSET = 18.0
MIN_EXPANDED_W = 98.0   # below this, expanding is not legible
MAX_PILL_W = 268.0      # window capacity; derived widths are clamped to it

# The window is always the largest it can get, so it never resizes while
# animating — only what is painted inside it changes. Resizing a layered
# top-level 60 times a second is visibly janky and makes placement race.
WIN_W = int(MAX_PILL_W) + SHADOW * 2
WIN_H = HEIGHT + SHADOW * 2

FRAME_MS = 16           # 60Hz while anything is moving
# Once settled the tick does almost nothing — the repaint is gated behind a
# dirty flag — so this only has to be slow enough to matter and fast enough that
# the pill starts reacting before you notice. At 120ms the expansion visibly
# lagged the chord.
IDLE_FRAME_MS = 33

# -- palette -----------------------------------------------------------------

# Noir: every neutral here is exactly R == G == B. The body used to be
# (20, 20, 26) with a steel-blue idle dot, which read as a blue pill; the state
# colours below are the only hue left, so they are the only thing that speaks.
BG = QtGui.QColor(0, 0, 0, 236)
BORDER = QtGui.QColor(255, 255, 255, 26)
TEXT = QtGui.QColor(255, 255, 255)
TEXT_DIM = QtGui.QColor(255, 255, 255, 130)
METER_BG = QtGui.QColor(255, 255, 255, 28)

ACCENT = {
    "loading": QtGui.QColor(150, 150, 150),
    "idle": QtGui.QColor(210, 210, 210),
    "recording": QtGui.QColor(228, 68, 68),
    "latched": QtGui.QColor(255, 202, 60),
    "working": QtGui.QColor(235, 168, 50),
    "error": QtGui.QColor(200, 64, 64),
}
# Recording deliberately carries NO word. A pulsing red dot is already the
# universal "this is recording", so the label was spending ~90px of the pill on
# something the colour had already said — and that pill is the one on screen
# every time you speak. The states that are NOT self-evident keep their word.
LABEL = {
    "loading": "Starting",
    "idle": "",
    "recording": "",
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
# Both of these are fractions of HEIGHT rather than fixed pixels, so changing
# the pill's thickness keeps its proportions instead of leaving the bars filling
# it edge to edge. 0.59 and 0.13 are what they measured at HEIGHT = 44.
BAR_MAX_H = HEIGHT * 0.59
DOT_R = HEIGHT * 0.13   # a shade over proportional: with the word gone, this
HALO_R = HEIGHT * 0.11  # dot is the entire "you are recording" signal

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


def expanded_width(state: str, label_w: float) -> float:
    """How wide the pill has to be to hold this state, from the parts it draws.

    `label_w` is MEASURED off the real font by the widget rather than estimated:
    it picks the pill's width, and a wrong guess would silently clip a word or
    leave a gap with nothing to trace it back to."""
    w = DOT_INSET + LABEL_GAP + label_w
    if state in METERED_STATES or state == "working":
        w += (METER_GAP if label_w else 0.0) + METER_W
    w += RIGHT_INSET
    return min(MAX_PILL_W, max(MIN_EXPANDED_W, w))


def notification_state() -> int:
    """SHQueryUserNotificationState's answer, or 0 if it could not give one."""
    state = ctypes.c_int(0)
    if shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return 0
    return state.value


def cursor_monitor() -> int:
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):     # fails on the secure desktop
        return 0
    return user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST) or 0


def _monitor_rect(m) -> tuple[int, int, int, int]:
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(mi)
    user32.GetMonitorInfoW(m, ctypes.byref(mi))
    r = mi.rcMonitor
    return r.left, r.top, r.right, r.bottom


def fullscreen_monitors() -> set[int]:
    """The monitors whose top window is fullscreen content: it covers the whole
    monitor, taskbar included, and has no title bar. Top means first in z-order
    among windows a person could be looking at, so tool, click-through and
    no-activate windows are passed over (the taskbar, this pill, Chrome's "Press
    F11" hint), and so is the wallpaper.

    It ignores focus on purpose: a video left fullscreen on one monitor while
    you work on the other still hides the pill when the mouse goes back over
    it. The title-bar test keeps out a maximized window on a monitor with no
    taskbar, whose rect overhangs the monitor by its frame. Measured 10 Sep
    2026: a fullscreen Chrome window is exactly the monitor rect, with neither
    WS_CAPTION nor WS_THICKFRAME, while maximized Chrome and VS Code keep both
    and stop at the taskbar."""
    decided: dict[int, bool] = {}
    wanted = user32.GetSystemMetrics(SM_CMONITORS)

    def visit(h, _lparam):
        if len(decided) >= wanted:
            return False
        if not user32.IsWindowVisible(h) or user32.IsIconic(h):
            return True
        if _get_long(h, GWL_EXSTYLE) & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
                                        | WS_EX_NOACTIVATE):
            return True
        m = user32.MonitorFromWindow(h, MONITOR_DEFAULTTONULL)
        if not m or m in decided or _cloaked(h) or _class(h) in DESKTOP_CLASSES:
            return True
        r = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.right <= r.left or r.bottom <= r.top:
            return True
        left, top, right, bottom = _monitor_rect(m)
        decided[m] = (r.left <= left and r.top <= top and r.right >= right
                      and r.bottom >= bottom
                      and (_get_long(h, GWL_STYLE) & WS_CAPTION) != WS_CAPTION)
        return True

    user32.EnumWindows(_WNDENUMPROC(visit), 0)
    return {m for m, full in decided.items() if full}


def fullscreen_state() -> tuple[bool, set[int]]:
    """(fullscreen everywhere, the monitors something is fullscreen on). The
    pill hides while idle when the first is true or the mouse's monitor is in
    the second."""
    state = notification_state()
    on = fullscreen_monitors()
    if state == QUNS_BUSY and not on:
        # The shell sees something fullscreen that the rect test does not
        # recognise; the focused window is the best guess at where.
        fg = user32.GetForegroundWindow()
        m = user32.MonitorFromWindow(fg, MONITOR_DEFAULTTONULL) if fg else None
        if m:
            on.add(m)
    return state in EVERYWHERE_STATES, on


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
        fm = QtGui.QFontMetricsF(self._font)
        self._label_w = {state: (fm.horizontalAdvance(text) if text else 0.0)
                         for state, text in LABEL.items()}

    def label_width(self, state: str) -> float:
        return self._label_w.get(state, 0.0)

    def width_for(self, state: str) -> float:
        return expanded_width(state, self.label_width(state))

    def paintEvent(self, _event: QtGui.QPaintEvent) -> None:
        ov = self._ov
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

        width = ov.pill_width
        height = ov.pill_height
        x = (WIN_W - width) / 2.0
        y = float(SHADOW + HEIGHT) - height     # bottom edge never moves
        r = height / 2.0
        body = QtCore.QRectF(x, y, width, height)

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
        cy = y + height / 2.0
        grown = _ease(ov.expand)

        # Centred while collapsed, sliding to a fixed inset as the pill grows.
        # Pinning it to the left edge outright leaves it visibly off-centre in
        # the resting lozenge, which is the shape that is on screen all day.
        dot_x = x + _lerp(COLLAPSED_W / 2.0, DOT_INSET, grown)
        # The dot grows in from IDLE_DOT_R, which may be nothing at all.
        dot_r = _lerp(IDLE_DOT_R, DOT_R, grown)
        if ov.state in METERED_STATES:
            # A halo scaled by input level: visible confirmation that it is
            # hearing you, which survives being seen out of the corner of an eye.
            # With the word "Recording" gone this is also the whole message, so
            # it is deliberately a level pulse and not a blink — it says heard
            # you, which a fixed-period blink does not.
            halo = dot_r + HALO_R * ov.meter
            p.setBrush(QtGui.QColor(accent.red(), accent.green(), accent.blue(), 60))
            p.drawEllipse(QtCore.QPointF(dot_x, cy), halo, halo)
        if dot_r >= 0.3:
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
        label_w = self.label_width(ov.state)
        text_x = dot_x + LABEL_GAP
        if label:
            p.setFont(self._font)
            p.setPen(TEXT if ov.state != "loading" else TEXT_DIM)
            p.drawText(QtCore.QRectF(text_x, y, label_w + 2.0, height),
                       int(QtCore.Qt.AlignmentFlag.AlignVCenter
                           | QtCore.Qt.AlignmentFlag.AlignLeft), label)

        # The bars start after whatever the label actually took, so a state with
        # no word gives that space to the waveform rather than to padding.
        meter_left = text_x + (label_w + METER_GAP if label_w else 0.0)
        meter_right = x + width - RIGHT_INSET
        # Bars scale with the pill's current height, so they never stand proud
        # of a body that is still swelling up from the idle sliver.
        bar_max = BAR_MAX_H * height / HEIGHT
        if meter_right - meter_left > 20.0:
            if ov.state in METERED_STATES:
                self._draw_bars(p, meter_left, meter_right, cy, accent, bar_max)
            elif ov.state == "working":
                self._draw_working(p, meter_left, meter_right, cy, accent, bar_max)
        p.end()

    def _draw_bars(self, p: QtGui.QPainter, left: float, right: float,
                   cy: float, accent: QtGui.QColor, bar_max: float) -> None:
        """Mirrored bars, oldest at the left. A scrolling history rather than a
        single number is what makes it read as a waveform — one bar rising and
        falling reads as a VU meter, which says far less about whether the words
        you just said were heard."""
        ov = self._ov
        span = right - left
        step = span / BARS
        w = min(BAR_W, step - BAR_GAP)
        half = bar_max / 2.0
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
                      cy: float, accent: QtGui.QColor, bar_max: float) -> None:
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
            h = max(BAR_MIN, bar_max * (0.18 + 0.55 * v * v))
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
        self.pill_height = float(COLLAPSED_H)
        # The width the pill expands TO, which is per-state now. It glides so a
        # change of state between two expanded widths animates instead of
        # snapping; 0.0 means "not yet known", and the first expansion snaps.
        self._expanded_w = 0.0
        self.history: collections.deque[float] = collections.deque(
            [0.0] * BARS, maxlen=BARS)
        self.widget: PillWidget | None = None
        self._timer: QtCore.QTimer | None = None
        self._hwnd = 0
        self._placed: tuple[int, int] | None = None
        self._shown = False
        self._dirty = True
        self._last_fullscreen_check = 0.0
        self._fullscreen = False            # on the monitor the mouse is on
        self._fullscreen_on: set[int] = set()
        self._fullscreen_everywhere = False
        self._buried = False
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

    # -- z-order ------------------------------------------------------------

    def buried_under(self) -> int:
        """The first window stacked above the pill that has no business being
        there, or 0: visible, on this virtual desktop, in the pill's own z-band,
        and NOT topmost. Windows keeps every topmost window above every normal
        one, so on a healthy stack this finds nothing.

        The band test is there because the Start menu and the other shell
        surfaces live in higher bands, where they are legitimately above the
        pill, and SetWindowPos cannot move a window across bands: counting them
        would restack every half second for as long as Start stayed open."""
        me = wintypes.HWND(self._hwnd)
        band = _band(me)
        h = user32.GetWindow(me, GW_HWNDPREV)
        while h:
            if (user32.IsWindowVisible(h)
                    and not (_get_long(h, GWL_EXSTYLE) & WS_EX_TOPMOST)
                    and not _cloaked(h) and _band(h) == band):
                return int(h)
            h = user32.GetWindow(h, GW_HWNDPREV)
        return 0

    def _keep_on_top(self) -> None:
        """Put the pill back on top if something has stacked it under a normal
        window. Measured 10 Sep 2026, an hour after launch: the pill sat at z=35
        below two VS Code windows with WS_EX_TOPMOST still set, and it was not
        alone. 22 topmost windows from four processes, the second monitor's own
        taskbar among them, had been pushed below normal windows as one block.
        Another process reordered the stack, and Shout cannot stop that. It can
        notice and recover: SetWindowPos(HWND_TOPMOST) on that live window moved
        it from z=35 to z=6 without touching the foreground.

        Acts only when something is actually wrong, so the pill never fights
        other topmost windows (taskbar thumbnails, tooltips) for the top slot.
        The bit is not enough to restore on its own: WS_EX_TOPMOST set through
        SetWindowLong changes nothing, which is why _enforce_exstyle skips it."""
        if not self._hwnd:
            return
        me = wintypes.HWND(self._hwnd)
        above = self.buried_under()
        lost = not (_get_long(me, GWL_EXSTYLE) & WS_EX_TOPMOST)
        if not (above or lost):
            self._buried = False
            return
        if not self._buried:            # once per burial, not every half second
            log.warning("pill was stacked below %s; restacking it on top",
                        _describe(above) if above else "normal windows (topmost lost)")
        self._buried = True
        user32.SetWindowPos(me, HWND_TOPMOST, 0, 0, 0, 0, SWP_RESTACK)

    # -- placement ----------------------------------------------------------

    def _screen(self) -> QtGui.QScreen:
        """The screen the mouse is on, not the one holding the focused window:
        moving the mouse to the other monitor brings the pill with it before
        anything is clicked there. Qt's availableGeometry already excludes the
        taskbar wherever it lives, and was measured to agree exactly with
        Win32's rcWork on this setup."""
        scr = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
        return scr if scr is not None else QtGui.QGuiApplication.primaryScreen()

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
        """Persistent, with one exception: a fullscreen app or a game gets its
        screen to itself while Shout is merely idle, so the pill hides while
        the mouse is on that screen and shows on any other. It never hides while
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
            self._fullscreen_everywhere, self._fullscreen_on = fullscreen_state()
            if self._shown:
                self._keep_on_top()
        # Every frame rather than every poll: the mouse crossing onto a
        # fullscreen monitor must hide the pill before it is drawn there.
        fullscreen = (self._fullscreen_everywhere
                      or cursor_monitor() in self._fullscreen_on)
        if fullscreen != self._fullscreen:
            self._fullscreen = fullscreen
            self._dirty = True

        state = self._state
        if state != self.state:
            self.state = state
            self._dirty = True

        want_visible = self._visible_for(state)
        if want_visible:
            # Placed before it is shown, never after, so it cannot appear for a
            # frame on the screen it is leaving.
            before = self._placed
            self._reposition()
            if self._placed != before:
                self._dirty = True
        if want_visible and not self._shown:
            self.widget.show()
            self._enforce_exstyle()
            self._keep_on_top()
            self._shown = True
            self._dirty = True
        elif not want_visible and self._shown:
            self.widget.hide()
            self._shown = False

        # -- expansion ------------------------------------------------------
        target = 1.0 if state in EXPANDED_STATES else 0.0
        if abs(self.expand - target) > 0.001:
            self.expand += (target - self.expand) * EXPAND_RATE
            if abs(self.expand - target) <= 0.001:
                self.expand = target
            self._dirty = True
        if state in EXPANDED_STATES:
            want_w = self.expanded_width_for(state)
            if self._expanded_w <= 0.0:
                self._expanded_w = want_w
            elif abs(self._expanded_w - want_w) > 0.01:
                self._expanded_w += (want_w - self._expanded_w) * EXPAND_RATE
                self._dirty = True
        elif self._expanded_w <= 0.0:
            self._expanded_w = MIN_EXPANDED_W
        width = _lerp(COLLAPSED_W, self._expanded_w, _ease(self.expand))
        height = _lerp(COLLAPSED_H, HEIGHT, _ease(self.expand))
        if abs(width - self.pill_width) > 0.01 or abs(height - self.pill_height) > 0.01:
            self.pill_width = width
            self.pill_height = height
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

    def expanded_width_for(self, state: str) -> float:
        """Width this state expands to. The widget owns it because only it has
        the font, and the label's measured advance is what sets the width."""
        if self.widget is not None:
            return self.widget.width_for(state)
        return expanded_width(state, 0.0)

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
