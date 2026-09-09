"""Gate: the pill is visible, correctly placed, animated, and never takes focus.

The focus assertion is the one that matters — a focus-stealing overlay breaks
dictation with the app's own UI, silently, because the text then simply goes
nowhere. So it is measured against a POSITIVE CONTROL: an identically-flagged Qt
window with only the focus-preventing attributes removed, which must be seen
stealing focus. If the control cannot demonstrate theft then the detector proves
nothing about the subject, and this gate reports INCONCLUSIVE rather than
passing.

Qt produces WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW and WS_EX_TRANSPARENT from its
own window flags, so these rows look redundant. They are not: they read the bits
off the real window, so a future Qt version quietly changing that mapping fails
here instead of failing as dictation that goes nowhere.

Run with Shout quit — two pills on screen make the placement rows meaningless.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from shout.overlay import (BAR_MAX_H, COLLAPSED_W, DOT_INSET, GA_ROOT,  # noqa: E402
                           GWL_EXSTYLE, HEIGHT, LABEL_GAP, MAX_PILL_W,
                           METER_GAP, METER_W, MARGIN_Y, RIGHT_INSET, SHADOW,
                           WANTED_EXSTYLE, WIN_H, WIN_W, WS_EX_NOACTIVATE,
                           WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, Overlay,
                           _get_long, expanded_width, fullscreen_app_running,
                           user32)

user32.WindowFromPoint.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


user32.WindowFromPoint.argtypes = [POINT]

rows: list[tuple[bool, str, str]] = []
app: QtWidgets.QApplication


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def spin(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        QtCore.QThread.msleep(3)


def rect(hwnd: int) -> wintypes.RECT:
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def control_steals_focus() -> bool:
    """Identically flagged — frameless, tool, topmost, translucent — with ONLY
    the three focus-preventing settings removed. That difference is exactly the
    thing under test, so the control isolates it."""
    w = QtWidgets.QWidget()
    w.setWindowFlags(
        QtCore.Qt.WindowType.FramelessWindowHint
        | QtCore.Qt.WindowType.Tool
        | QtCore.Qt.WindowType.WindowStaysOnTopHint
    )
    w.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
    w.setFixedSize(WIN_W, WIN_H)
    w.move(400, 400)
    w.show()
    w.raise_()
    w.activateWindow()
    spin(0.5)
    h = int(w.winId())
    stole = int(user32.GetForegroundWindow()) == h
    w.hide()
    w.deleteLater()
    spin(0.3)
    return stole


def main() -> int:
    global app
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    # The control runs FIRST, while the process still reliably holds the
    # foreground rights Windows grants a child of the foreground process.
    control = control_steals_focus()

    level = {"v": 0.0}
    ov = Overlay(enabled=True, level_source=lambda: level["v"])
    ov.build()
    hwnd = ov._hwnd
    check(hwnd != 0, "window created", f"hwnd={hwnd}")
    check(hwnd == int(user32.GetAncestor(wintypes.HWND(hwnd), GA_ROOT)),
          "winId() is the top-level hwnd (no Tk-style wrapper)")

    ex = _get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
    check(ex & WS_EX_NOACTIVATE, "WS_EX_NOACTIVATE set")
    check(ex & WS_EX_TOOLWINDOW, "WS_EX_TOOLWINDOW set (out of alt-tab)")
    check(ex & WS_EX_TRANSPARENT, "WS_EX_TRANSPARENT set (click-through)")

    # -- focus: the subject, against the control ---------------------------
    before = int(user32.GetForegroundWindow())
    ov.set_state("recording")
    spin(0.5)
    after = int(user32.GetForegroundWindow())
    check(after != hwnd, "pill did not take focus", f"foreground {before} -> {after}")
    check(after == before, "foreground window unchanged")

    # -- click-through, functionally rather than by flag -------------------
    r = rect(hwnd)
    mid = POINT(x=(r.left + r.right) // 2, y=(r.top + r.bottom) // 2)
    under = int(user32.WindowFromPoint(mid))
    check(under != hwnd, "clicks pass through to the window beneath",
          f"WindowFromPoint={under}")

    # -- placement ----------------------------------------------------------
    a = ov.work_area()
    check(visible(hwnd), "visible while recording")
    # The window carries a painted shadow margin, so the PILL's bottom edge is
    # SHADOW px above the window's. Asserting the window rect would silently
    # bake the shadow into the gap and drift the pill on any shadow change.
    pill_bottom = r.bottom - SHADOW
    check(pill_bottom <= a.bottom() + 1, "sits inside the work area",
          f"pill bottom={pill_bottom} work bottom={a.bottom()}")
    check(abs((a.bottom() + 1 - pill_bottom) - MARGIN_Y) <= 1,
          "correct gap above the taskbar",
          f"gap={a.bottom() + 1 - pill_bottom}px want={MARGIN_Y}px")
    check(r.top >= a.top(), "does not run off the top of the work area")
    expected_x = a.left() + (a.width() - WIN_W) // 2
    check(abs(r.left - expected_x) <= 1, "horizontally centred",
          f"left={r.left} expected={expected_x}")
    check((r.right - r.left, r.bottom - r.top) == (WIN_W, WIN_H),
          "window size is as declared", f"{r.right - r.left}x{r.bottom - r.top}")

    # -- persistence: the change from session 2 -----------------------------
    for state, want in (("idle", True), ("loading", True), ("latched", True),
                        ("working", True), ("recording", True)):
        ov.set_state(state)
        spin(0.12)
        check(visible(hwnd) == want, f"state {state!r} -> visible={want}",
              "persistent" if want else "")

    # -- expansion ----------------------------------------------------------
    ov.set_state("idle")
    spin(0.9)
    check(abs(ov.pill_width - COLLAPSED_W) < 1.0, "idle collapses to the lozenge",
          f"width={ov.pill_width:.1f} want={COLLAPSED_W}")
    rec_w = ov.expanded_width_for("recording")
    ov.set_state("recording")
    widths = []
    for _ in range(14):
        spin(0.02)
        widths.append(ov.pill_width)
    spin(0.9)
    check(abs(ov.pill_width - rec_w) < 1.0, "recording expands to its state width",
          f"width={ov.pill_width:.1f} want={rec_w:.0f}")
    tweens = [w for w in widths if COLLAPSED_W + 2 < w < rec_w - 2]
    # Without this the pill could hard-cut between two widths and every other
    # width row above would still pass.
    check(len(tweens) >= 3, "expansion is animated, not a hard cut",
          f"{len(tweens)} intermediate widths sampled")

    # -- per-state width ----------------------------------------------------
    # Width is derived from the parts each state draws, so these assert the
    # LAYOUT rather than restating a constant: dropping the word from recording
    # has to actually buy back the pixels it used, and the bar row has to still
    # get its full span in every state that draws one.
    work_w = ov.expanded_width_for("working")
    label_w = ov.widget.label_width("working")
    check(rec_w < work_w - 40.0, "the unlabelled recording pill is the short one",
          f"recording={rec_w:.0f} vs working={work_w:.0f} ('Transcribing')")
    check(abs((rec_w - DOT_INSET - LABEL_GAP - RIGHT_INSET) - METER_W) < 0.5,
          "recording spends its whole width on the bars",
          f"bar span={rec_w - DOT_INSET - LABEL_GAP - RIGHT_INSET:.0f}px want={METER_W:.0f}")
    check(abs((work_w - DOT_INSET - LABEL_GAP - label_w - METER_GAP
               - RIGHT_INSET) - METER_W) < 0.5,
          "a labelled state still gets the same bar span, not a squeezed one",
          f"label={label_w:.0f}px + bars={METER_W:.0f}px")
    check(ov.widget.label_width("recording") == 0.0,
          "recording carries no word (the red dot is the message)")
    for state in ("loading", "recording", "latched", "working", "error"):
        w = ov.expanded_width_for(state)
        check(COLLAPSED_W < w <= MAX_PILL_W,
              f"{state}: width is expanded and fits the window", f"{w:.0f}px")
    check(BAR_MAX_H < HEIGHT, "the bars fit inside the pill",
          f"bars={BAR_MAX_H:.1f}px in {HEIGHT}px")

    # A state change between two EXPANDED widths must glide, not snap — the
    # recording -> transcribing handoff happens on every single dictation.
    ov.set_state("working")
    glide = []
    for _ in range(14):
        spin(0.02)
        glide.append(ov.pill_width)
    spin(0.9)
    check(abs(ov.pill_width - work_w) < 1.0, "working settles at its own width",
          f"width={ov.pill_width:.1f} want={work_w:.0f}")
    check(len([w for w in glide if rec_w + 2 < w < work_w - 2]) >= 3,
          "recording -> working widens smoothly rather than snapping",
          f"{len([w for w in glide if rec_w + 2 < w < work_w - 2])} intermediate widths")

    # -- fullscreen policy --------------------------------------------------
    # Exercised directly: waiting for a real game to be running is not a gate.
    ov._fullscreen = True
    check(ov._visible_for("idle") is False, "hides when idle under a fullscreen app")
    check(ov._visible_for("recording") is True,
          "still shows while RECORDING under a fullscreen app",
          "feedback matters most exactly when something is covering the screen")
    check(ov._visible_for("working") is True, "still shows while transcribing")
    ov._fullscreen = False
    check(isinstance(fullscreen_app_running(), bool),
          "SHQueryUserNotificationState answers")

    # -- the ex-style survives a full cycle of real use --------------------
    check(ov.has_exstyle(), "flags intact after show/hide/expand cycles")

    # -- level meter mapping ------------------------------------------------
    check(ov._to_bar(0.0) == 0.0, "silence maps to an empty meter")
    check(ov._to_bar(1.0) == 1.0, "full scale maps to a full meter")
    quiet, loud = ov._to_bar(0.004), ov._to_bar(0.08)
    check(0.0 < quiet < loud < 1.0, "speech levels land strictly inside the bar",
          f"quiet={quiet:.2f} loud={loud:.2f}")

    # -- the waveform carries history, not one repeated number --------------
    ov.set_state("recording")
    spin(0.1)
    for v in (0.001, 0.06, 0.001, 0.06, 0.001):
        level["v"] = v
        spin(0.09)
    hist = list(ov.history)
    check(len(hist) == ov.history.maxlen, "history buffer is full",
          f"{len(hist)} samples")
    check(max(hist) - min(hist) > 0.15,
          "history holds a varying envelope, not one repeated level",
          f"min={min(hist):.2f} max={max(hist):.2f}")

    ov.stop()
    spin(0.1)

    # -- report -------------------------------------------------------------
    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"  [{'ok' if control else 'FAIL'}] CONTROL: an activating window was "
          f"seen stealing focus")

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
