"""The state pill — a small always-on-top bubble above the taskbar.

Hidden when idle. While recording it shows what mode is live and a moving level
meter, which is what makes "is it actually hearing me" answerable at a glance.

Three constraints shaped every decision here, and all three are measured by
`harness/probe_overlay.py` rather than assumed:

*It must never take focus.* Dictation needs a focused editable field, so an
overlay that activates would break the app with its own UI. `WS_EX_NOACTIVATE`
plus `WS_EX_TOOLWINDOW` (out of alt-tab) plus `WS_EX_TRANSPARENT` (click-through)
are applied with `SetWindowLongPtrW`.

*Tk's own `deiconify()` defeats that.* It calls `SetForegroundWindow`
unconditionally, so it steals focus even with `WS_EX_NOACTIVATE` already applied
— measured, not theorised. So the window is created withdrawn and NEVER
deiconified; visibility is driven by `ShowWindow(SW_SHOWNOACTIVATE / SW_HIDE)`
directly. Tk keeps laying the window out and repainting it perfectly well while
believing it is withdrawn.

*The first `-alpha` or `-transparentcolor` call wipes the ex-style.* Tk rewrites
GWL_EXSTYLE from its own cached copy when it makes the window layered. Both are
therefore set once during construction, BEFORE the flags go on, and never touched
again — `_enforce_exstyle()` re-asserts them anyway, cheaply, on every show.

Threading: Tk is not thread-safe and owns the main thread. Nothing outside this
module ever calls into Tk. `set_state()` and `set_level()` just rebind an
attribute, and a 30Hz `after()` loop on the Tk thread reads them.
"""
from __future__ import annotations

import ctypes
import logging
import math
import threading
import tkinter as tk
from ctypes import wintypes

log = logging.getLogger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)

GWL_EXSTYLE = -20
GA_ROOT = 2
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
MONITOR_DEFAULTTONEAREST = 2

WANTED_EXSTYLE = WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND

# SetWindowLongPtrW only exists on 64-bit; the 32-bit name is the fallback.
_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t

WIDTH, HEIGHT = 246, 46
MARGIN_Y = 14           # gap between the pill and the top of the taskbar
TRANSPARENT_KEY = "#010203"
BG = "#17171c"
BORDER = "#3a3a46"
TEXT = "#f0f0f4"
METER_BG = "#2a2a33"
POLL_MS = 33            # ~30Hz

# state -> (dot colour, label, meter colour). Absent = hidden.
VISIBLE_STATES = {
    "recording": ("#dc3c3c", "Recording", "#dc3c3c"),
    "latched": ("#ffd23c", "Hands-free", "#ffd23c"),
    "working": ("#eba832", "Transcribing", "#eba832"),
}

# Level meter: map dBFS onto the bar. Quiet speech sits near -40dB.
DB_FLOOR, DB_CEIL = -52.0, -12.0
ATTACK, RELEASE = 0.55, 0.16


class Overlay:
    """Owns the main thread. Everything else talks to it through attributes."""

    def __init__(self, enabled: bool = True, level_source=None) -> None:
        self.enabled = enabled
        self._level_source = level_source
        self._state = "idle"          # written by any thread
        self._level = 0.0             # written by the audio callback
        self._shown = False
        self._smoothed = 0.0
        self._stopping = threading.Event()
        self.root: tk.Tk | None = None
        self._win: tk.Toplevel | None = None
        self._hwnd: int = 0
        self._items: dict = {}
        self._placed: tuple[int, int] | None = None
        self.focus_steals = 0         # read by the probe

    # -- called from other threads (attribute rebinds only) -----------------

    def set_state(self, state: str) -> None:
        self._state = state

    def set_level(self, rms: float) -> None:
        self._level = rms

    def stop(self) -> None:
        self._stopping.set()

    # -- construction (Tk thread only) --------------------------------------

    def _build(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

        win = tk.Toplevel(self.root)
        win.withdraw()                      # never deiconified; see module docstring
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        # Both of these make the window layered and rewrite GWL_EXSTYLE from Tk's
        # cache, so they must happen before the flags go on.
        win.attributes("-transparentcolor", TRANSPARENT_KEY)
        win.attributes("-alpha", 0.96)
        win.configure(bg=TRANSPARENT_KEY)

        canvas = tk.Canvas(win, width=WIDTH, height=HEIGHT, highlightthickness=0,
                           bg=TRANSPARENT_KEY, bd=0)
        canvas.pack()
        self._draw_pill(canvas)

        win.geometry(f"{WIDTH}x{HEIGHT}+0+0")
        win.update_idletasks()
        self._win, self._canvas = win, canvas
        self._hwnd = int(user32.GetAncestor(wintypes.HWND(win.winfo_id()), GA_ROOT))
        self._enforce_exstyle()
        log.info("overlay ready (hwnd=%d)", self._hwnd)

    def _draw_pill(self, c: tk.Canvas) -> None:
        r = HEIGHT // 2
        pad = 2
        # A rounded pill from two circles and a bar; Tk has no rounded rectangle.
        for shape in (
            lambda **kw: c.create_oval(pad, pad, pad + 2 * (r - pad),
                                       HEIGHT - pad, **kw),
            lambda **kw: c.create_oval(WIDTH - pad - 2 * (r - pad), pad,
                                       WIDTH - pad, HEIGHT - pad, **kw),
            lambda **kw: c.create_rectangle(r, pad, WIDTH - r, HEIGHT - pad, **kw),
        ):
            shape(fill=BG, outline=BG)
        # A hairline top edge reads as a border without boxing the pill in.
        c.create_line(r, pad, WIDTH - r, pad, fill=BORDER)
        c.create_line(r, HEIGHT - pad, WIDTH - r, HEIGHT - pad, fill=BORDER)

        self._items["dot"] = c.create_oval(20, 18, 30, 28, fill="#dc3c3c",
                                           outline="")
        self._items["label"] = c.create_text(40, 23, text="Recording", anchor="w",
                                             fill=TEXT, font=("Segoe UI", 10))
        mx0, mx1, my = 146, WIDTH - 22, 23
        c.create_rectangle(mx0, my - 4, mx1, my + 4, fill=METER_BG, outline="")
        self._items["meter"] = c.create_rectangle(mx0, my - 4, mx0, my + 4,
                                                  fill="#dc3c3c", outline="")
        self._meter_span = (mx0, mx1, my)

    def _enforce_exstyle(self) -> None:
        """Re-assert the flags. Cheap, and the only defence against a Tk call
        silently rewriting GWL_EXSTYLE from its own cached copy."""
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

    def _work_area(self) -> tuple[int, int, int, int]:
        """The work area of the monitor holding the focused window — not the
        primary monitor's, and not the full screen: rcWork already excludes the
        taskbar wherever the user keeps it."""
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        mon = user32.MonitorFromWindow(user32.GetForegroundWindow(),
                                       MONITOR_DEFAULTTONEAREST)
        if mon and user32.GetMonitorInfoW(mon, ctypes.byref(info)):
            w = info.rcWork
            return w.left, w.top, w.right, w.bottom
        return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()

    def _reposition(self) -> None:
        left, _top, right, bottom = self._work_area()
        x = left + (right - left - WIDTH) // 2
        y = bottom - HEIGHT - MARGIN_Y
        if (x, y) == self._placed:
            return
        self._win.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")
        # Tk defers a geometry request on a window it believes is withdrawn, and
        # this one is permanently withdrawn by design, so the move only reaches
        # the real window once idle tasks run.
        self._win.update_idletasks()
        self._placed = (x, y)

    # -- the Tk-thread loop --------------------------------------------------

    def _show(self) -> None:
        self._reposition()
        user32.ShowWindow(wintypes.HWND(self._hwnd), SW_SHOWNOACTIVATE)
        self._enforce_exstyle()
        self._shown = True

    def _hide(self) -> None:
        user32.ShowWindow(wintypes.HWND(self._hwnd), SW_HIDE)
        self._shown = False

    def _tick(self) -> None:
        if self._stopping.is_set():
            self._hide()
            self.root.quit()
            return
        try:
            self._update()
        except Exception:
            log.exception("overlay tick")
        self.root.after(POLL_MS, self._tick)

    def _update(self) -> None:
        style = VISIBLE_STATES.get(self._state)
        if style is None:
            if self._shown:
                self._hide()
            self._smoothed = 0.0
            return

        if not self._shown:
            self._smoothed = 0.0
            self._show()
        else:
            self._reposition()   # follow the focused window across monitors

        colour, label, meter_colour = style
        c = self._canvas
        c.itemconfigure(self._items["dot"], fill=colour)
        c.itemconfigure(self._items["label"], text=label)

        if self._state == "working":
            # Nothing is being heard while transcribing, so let the meter read
            # empty immediately rather than decaying from the last speech level
            # — a bar still falling implies a live microphone that is closed.
            self._smoothed = 0.0
        else:
            level = self._level_source() if self._level_source else self._level
            target = self._to_bar(level)
            rate = ATTACK if target > self._smoothed else RELEASE
            self._smoothed += (target - self._smoothed) * rate
        mx0, mx1, my = self._meter_span
        c.coords(self._items["meter"], mx0, my - 4,
                 mx0 + (mx1 - mx0) * self._smoothed, my + 4)
        c.itemconfigure(self._items["meter"], fill=meter_colour)

    @staticmethod
    def _to_bar(rms: float) -> float:
        if rms <= 1e-7:
            return 0.0
        db = 20.0 * math.log10(rms)
        return min(1.0, max(0.0, (db - DB_FLOOR) / (DB_CEIL - DB_FLOOR)))

    # -- lifecycle -----------------------------------------------------------

    def run(self) -> None:
        """Blocks on the Tk mainloop. Must be called on the main thread."""
        if not self.enabled:
            self._stopping.wait()
            return
        self._build()
        self.root.after(POLL_MS, self._tick)
        self.root.mainloop()
        try:
            self.root.destroy()
        except Exception:
            pass
