"""Gate: the overlay is visible, correctly placed, and never takes focus.

The focus assertion is the one that matters — a focus-stealing overlay breaks
dictation with its own UI, silently, because the text simply goes nowhere. So it
is measured against a POSITIVE CONTROL: an identically-configured window shown
with Tk's own `deiconify()`, which must be seen stealing focus. If the control
cannot demonstrate focus theft then the detector proves nothing about the
subject, and this gate reports INCONCLUSIVE rather than passing.

Run with Shout quit — two overlays on screen make the placement rows meaningless.
"""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shout.overlay import (GA_ROOT, GWL_EXSTYLE, HEIGHT, MARGIN_Y,
                           SW_SHOWNOACTIVATE, WANTED_EXSTYLE, WIDTH,
                           WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW,
                           WS_EX_TRANSPARENT, Overlay, _get_long, user32)

user32.WindowFromPoint.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def rect(hwnd: int) -> wintypes.RECT:
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def control_steals_focus(root: tk.Tk) -> bool:
    """Identically configured — overrideredirect, topmost, layered, and the very
    same ex-style flags. The ONLY difference is that it is shown with Tk's
    deiconify() instead of ShowWindow(SW_SHOWNOACTIVATE)."""
    w = tk.Toplevel(root)
    w.withdraw()
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.attributes("-alpha", 0.96)
    tk.Label(w, text="control", bg="#17171c", fg="#f0f0f4").pack()
    w.geometry(f"{WIDTH}x{HEIGHT}+400+400")
    w.update_idletasks()
    h = int(user32.GetAncestor(wintypes.HWND(w.winfo_id()), GA_ROOT))
    from shout.overlay import _set_long
    _set_long(wintypes.HWND(h), GWL_EXSTYLE,
              _get_long(wintypes.HWND(h), GWL_EXSTYLE) | WANTED_EXSTYLE)
    w.deiconify()
    w.update()
    time.sleep(0.45)
    root.update()
    stole = int(user32.GetForegroundWindow()) == h
    w.destroy()
    root.update()
    time.sleep(0.25)
    return stole


def main() -> int:
    # The control runs FIRST, while the process still reliably holds the
    # foreground rights Windows grants a child of the foreground process.
    probe_root = tk.Tk()
    probe_root.withdraw()
    control = control_steals_focus(probe_root)
    probe_root.destroy()

    ov = Overlay(enabled=True, level_source=lambda: 0.05)
    ov._build()
    hwnd = ov._hwnd
    check(hwnd != 0, "window created", f"hwnd={hwnd}")

    ex = _get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
    check(ex & WS_EX_NOACTIVATE, "WS_EX_NOACTIVATE set")
    check(ex & WS_EX_TOOLWINDOW, "WS_EX_TOOLWINDOW set (out of alt-tab)")
    check(ex & WS_EX_TRANSPARENT, "WS_EX_TRANSPARENT set (click-through)")

    # -- focus: the subject, against the control ---------------------------
    before = int(user32.GetForegroundWindow())
    ov.set_state("recording")
    ov._update()
    time.sleep(0.45)
    after = int(user32.GetForegroundWindow())
    subject_stole = after == hwnd
    check(not subject_stole, "overlay did not take focus",
          f"foreground {before} -> {after}")
    check(after == before, "foreground window unchanged")

    # -- click-through, functionally rather than by flag -------------------
    r = rect(hwnd)
    mid = POINT(x=(r.left + r.right) // 2, y=(r.top + r.bottom) // 2)
    under = int(user32.WindowFromPoint(mid))
    check(under != hwnd, "clicks pass through to the window beneath",
          f"WindowFromPoint={under}")

    # -- placement ----------------------------------------------------------
    left, top, right, bottom = ov._work_area()
    check(visible(hwnd), "visible while recording")
    check(r.bottom <= bottom, "sits inside the work area, above the taskbar",
          f"pill bottom={r.bottom} work bottom={bottom}")
    check(r.bottom == bottom - MARGIN_Y, "correct gap above the taskbar",
          f"gap={bottom - r.bottom}px")
    check(r.top >= top, "does not run off the top of the work area")
    expected_x = left + (right - left - WIDTH) // 2
    check(abs(r.left - expected_x) <= 1, "horizontally centred",
          f"left={r.left} expected={expected_x}")
    check((r.right - r.left, r.bottom - r.top) == (WIDTH, HEIGHT), "size is as declared",
          f"{r.right - r.left}x{r.bottom - r.top}")

    # -- state drives visibility -------------------------------------------
    for state, want in (("latched", True), ("working", True),
                        ("idle", False), ("loading", False), ("recording", True)):
        ov.set_state(state)
        ov._update()
        ov.root.update()
        check(visible(hwnd) == want, f"state {state!r} -> visible={want}")

    # -- the ex-style survives a full cycle of real use --------------------
    check(ov.has_exstyle(), "flags intact after show/hide/reposition cycles")

    # -- level meter mapping ------------------------------------------------
    check(ov._to_bar(0.0) == 0.0, "silence maps to an empty meter")
    check(ov._to_bar(1.0) == 1.0, "full scale maps to a full meter")
    quiet, loud = ov._to_bar(0.004), ov._to_bar(0.08)
    check(0.0 < quiet < loud < 1.0, "speech levels land strictly inside the bar",
          f"quiet={quiet:.2f} loud={loud:.2f}")

    ov.stop()
    try:
        ov.root.destroy()
    except Exception:
        pass

    # -- report -------------------------------------------------------------
    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"  [{'ok' if control else 'FAIL'}] CONTROL: deiconify() was seen stealing focus")

    if not control:
        print(f"INCONCLUSIVE - the control could not demonstrate focus theft, so "
              f"the {passed}/{len(rows)} subject rows prove nothing about focus")
        return 1
    if passed != len(rows):
        print(f"FAIL {passed}/{len(rows)} overlay assertions (control ok)")
        return 1
    print(f"PASS {passed}/{len(rows)} overlay assertions, control confirmed focus theft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
