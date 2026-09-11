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
It moves the real mouse pointer to each screen for ~0.2s and puts it back.
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import shout.overlay as overlay_mod  # noqa: E402
from shout.overlay import (BAR_MAX_H, COLLAPSED_H, COLLAPSED_W, DOT_INSET,  # noqa: E402
                           GA_ROOT, GW_HWNDPREV, GWL_EXSTYLE, HEIGHT, LABEL_GAP,
                           MAX_PILL_W, METER_GAP, METER_W, MARGIN_Y, RIGHT_INSET,
                           SHADOW, SWP_RESTACK, WANTED_EXSTYLE, WIN_H, WIN_W,
                           WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST,
                           WS_EX_TRANSPARENT, Overlay, _get_long, expanded_width,
                           fullscreen_app_running, user32)

user32.WindowFromPoint.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
HWND_TOP = wintypes.HWND(0)
HWND_NOTOPMOST = wintypes.HWND(-2)


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


def stacked_above(upper: int, lower: int) -> bool:
    """True if `upper` is anywhere above `lower` in the z-order."""
    h = user32.GetWindow(wintypes.HWND(lower), GW_HWNDPREV)
    while h:
        if int(h) == upper:
            return True
        h = user32.GetWindow(h, GW_HWNDPREV)
    return False


GREY = QtGui.QColor(128, 128, 128)


def render(ov: Overlay) -> QtGui.QImage:
    """The window as the painter draws it, over opaque neutral grey. Grey can
    carry no hue of its own, so any tint read off the result is the pill's."""
    img = QtGui.QImage(WIN_W, WIN_H, QtGui.QImage.Format.Format_ARGB32)
    img.fill(GREY)
    ov.widget.render(img, QtCore.QPoint(0, 0), QtGui.QRegion(),
                     QtWidgets.QWidget.RenderFlag.DrawChildren)
    return img


def body_rows(img: QtGui.QImage) -> list[int]:
    """Rows of the centre column the pill covers, measured off the pixels. The
    shadow darkens the grey by ~15 at most; the body, dot and bars all differ
    from it by far more than 40."""
    x = WIN_W // 2
    out = []
    for y in range(WIN_H):
        c = img.pixelColor(x, y)
        if max(abs(c.red() - 128), abs(c.green() - 128), abs(c.blue() - 128)) > 40:
            out.append(y)
    return out


def tint(img: QtGui.QImage, x: int, y: int) -> int:
    c = img.pixelColor(x, y)
    return max(c.red(), c.green(), c.blue()) - min(c.red(), c.green(), c.blue())


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

    # -- follows the mouse, not focus ---------------------------------------
    # The real cursor visits the centre of every screen and is put back. The
    # expected spot comes from that screen's own geometry, never from
    # work_area(), which is the code under test. Only a screen NOT holding the
    # focused window can tell mouse-following from focus-following, so those
    # are counted, and one monitor reports SKIP rather than a pass.
    ov.set_state("idle")
    fr = rect(int(user32.GetForegroundWindow()))
    focus_scr = QtGui.QGuiApplication.screenAt(
        QtCore.QPoint((fr.left + fr.right) // 2, (fr.top + fr.bottom) // 2))
    focus_name = focus_scr.name() if focus_scr is not None else None
    home = QtGui.QCursor.pos()
    exercised = 0
    try:
        for i, scr in enumerate(QtGui.QGuiApplication.screens()):
            QtGui.QCursor.setPos(scr.geometry().center())
            spin(0.2)
            under = QtGui.QGuiApplication.screenAt(QtGui.QCursor.pos())
            if under is None or under.name() != scr.name():
                check(False, f"screen {i}: the cursor could be moved there",
                      f"cursor at {QtGui.QCursor.pos().toTuple()}")
                continue
            a, r = scr.availableGeometry(), rect(hwnd)
            want = (a.left() + (a.width() - WIN_W) // 2,
                    a.bottom() + 1 - MARGIN_Y + SHADOW - WIN_H)
            away = scr.name() != focus_name
            exercised += away
            check(abs(r.left - want[0]) <= 1 and abs(r.top - want[1]) <= 1,
                  f"screen {i}: the pill follows the mouse there",
                  f"at ({r.left},{r.top}) want {want}"
                  + ("; focus is on another screen" if away else "; focus is here too"))
    finally:
        QtGui.QCursor.setPos(home)
        spin(0.2)
    follow_skip = "" if exercised else (
        "SKIP: every screen holds the focused window (one monitor?), so the "
        "rows above cannot tell mouse-following from focus-following")

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
    # Height is read off the painted pixels rather than off pill_height: a
    # painter that ignored the attribute would still pass an attribute check.
    idle_img = render(ov)
    idle_rows = body_rows(idle_img)
    check(abs(len(idle_rows) - COLLAPSED_H) <= 1,
          "idle is painted as the thin sliver, not at full height",
          f"measured {len(idle_rows)}px want {COLLAPSED_H}px (expanded is {HEIGHT}px)")

    # -- noir: the resting pill carries no hue ------------------------------
    mid_y = idle_rows[len(idle_rows) // 2] if idle_rows else 0
    body_x = WIN_W // 2 + COLLAPSED_W // 4      # clear of any dot and the end caps
    check(tint(idle_img, body_x, mid_y) <= 1, "idle body is neutral black",
          f"channel spread {tint(idle_img, body_x, mid_y)} over neutral grey")
    check(tint(idle_img, WIN_W // 2, mid_y) <= 1, "idle centre is neutral (dot or body)",
          f"channel spread {tint(idle_img, WIN_W // 2, mid_y)}")
    # The same measurement must see the pre-noir body, or it proves nothing.
    noir = overlay_mod.BG
    overlay_mod.BG = QtGui.QColor(20, 20, 26, 236)
    old_spread = tint(render(ov), body_x, mid_y)
    overlay_mod.BG = noir
    check(old_spread >= 3, "CONTROL: the old blue-black body reads as tinted here",
          f"channel spread {old_spread}")

    rec_w = ov.expanded_width_for("recording")
    ov.set_state("recording")
    widths, heights = [], []
    for _ in range(14):
        spin(0.02)
        widths.append(ov.pill_width)
        heights.append(ov.pill_height)
    spin(0.9)
    check(abs(ov.pill_width - rec_w) < 1.0, "recording expands to its state width",
          f"width={ov.pill_width:.1f} want={rec_w:.0f}")
    rec_rows = body_rows(render(ov))
    check(abs(len(rec_rows) - HEIGHT) <= 1, "recording is painted at full height",
          f"measured {len(rec_rows)}px want {HEIGHT}px")
    check(bool(idle_rows and rec_rows) and idle_rows[-1] == rec_rows[-1],
          "the bottom edge stays put; the pill grows upward",
          f"bottom row idle={idle_rows[-1] if idle_rows else None} "
          f"recording={rec_rows[-1] if rec_rows else None}")
    tweens = [w for w in widths if COLLAPSED_W + 2 < w < rec_w - 2]
    # Without this the pill could hard-cut between two widths and every other
    # width row above would still pass.
    check(len(tweens) >= 3, "expansion is animated, not a hard cut",
          f"{len(tweens)} intermediate widths sampled")
    h_tweens = [h for h in heights if COLLAPSED_H + 1 < h < HEIGHT - 1]
    check(len(h_tweens) >= 3, "height swells with the width rather than snapping",
          f"{len(h_tweens)} intermediate heights sampled")

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

    # -- stays on top when something buries it ------------------------------
    # The 10 Sep 2026 incident, as closely as a probe can stage it: the pill is
    # dropped into the normal band and a plain window is stacked on top of it.
    # The live pill kept its topmost bit while buried, which no public call
    # reproduces, so the walk row asserts the burial is found by z-order rather
    # than by that bit.
    ov.set_state("recording")
    spin(0.1)
    cover = QtWidgets.QWidget()
    cover.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
    cover.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
    r = rect(hwnd)
    cover.setGeometry(r.left, r.top, r.right - r.left, r.bottom - r.top)
    cover.show()
    spin(0.2)
    cover_h = int(cover.winId())
    ov._keep_on_top = lambda: None          # CONTROL: recovery switched off
    user32.SetWindowPos(wintypes.HWND(hwnd), HWND_NOTOPMOST, 0, 0, 0, 0, SWP_RESTACK)
    user32.SetWindowPos(wintypes.HWND(cover_h), HWND_TOP, 0, 0, 0, 0, SWP_RESTACK)
    spin(1.2)
    check(stacked_above(cover_h, hwnd),
          "CONTROL: with recovery off, a buried pill stays buried",
          "proves the recovery rows below measure something")
    found = ov.buried_under()
    check(found == cover_h, "the z-order walk names the window on top of the pill",
          f"found {found} want {cover_h}")
    fg = int(user32.GetForegroundWindow())
    del ov._keep_on_top
    spin(1.2)
    check(stacked_above(hwnd, cover_h), "a buried pill restacks itself on top",
          "within two half-second checks")
    check(_get_long(wintypes.HWND(hwnd), GWL_EXSTYLE) & WS_EX_TOPMOST,
          "and is topmost again")
    check(ov.buried_under() == 0, "nothing normal is left above it")
    check(int(user32.GetForegroundWindow()) == fg, "restacking did not move focus")
    cover.hide()
    cover.deleteLater()
    spin(0.1)

    ov.stop()
    spin(0.1)

    # -- report -------------------------------------------------------------
    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    if follow_skip:
        print(f"  [skip] {follow_skip}")
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
