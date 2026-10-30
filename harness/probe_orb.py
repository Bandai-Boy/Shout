"""Gate: the orb look behaves like the pill's window and moves the way it claims.

Window behaviour first, because it is what breaks dictation: the orb is a
different widget in the same window, so the flags, the no-focus rule and the
placement are re-read off the real orb window rather than assumed from the pill.
The focus row reuses probe_overlay's positive control, an activating window that
must be seen stealing focus, or the gate is INCONCLUSIVE.

Then the look, measured off rendered pixels:
  - at rest it is a small GLASS capsule: a see-through body inside an opaque rim
    (control: expanded, the centre is a hole)
  - at rest the rim's colour circles while the shape holds still, drawn at the
    idle tick rather than the full rate (control: expanded, the shape moves)
  - its bottom edge stays put as it inflates
  - holding and hands-free are two different waters, even in silence: they carry
    the same crests, and hands-free carries an octave of ripples on top that the
    holding swell has nothing at. Ordinary speech visibly raises both
  - no turbulence setting puts a corner on either ring: the crests never stand
    out of the troughs the way spikes do (control: the pow-sharpened crest this
    replaced on 17 Sep, which does)
  - transcribing has no crests and one bright comet, and ignores the level
    (control: the same renderer does see level while recording)
  - a failed OpenGL start, or a render that fails later, falls back to the pill

Then the tune, which the Orb page edits: the default tune is the look that
shipped, a tuned wave count is what the ring shows, the window
the tune derives holds its loudest frame (control: the same frame in a smaller
window is cut), a hand-edited `orb_tune` cannot take the orb out of range, and a
running overlay takes a new tune in place or rebuilds in a window of the new
size, still placed and flagged like the pill.

Crests are read off the ring itself: for each of 360 angles, the radius where
the ray from the centre is most opaque, then the strongest wave in that profile. Motion rows step `orb.Choreography` on a
fixed 60Hz clock, so they measure the real stepping code without depending on
how fast this machine ticks.

Run with Shout quit, like probe_overlay: a second overlay on screen makes the
placement rows meaningless.
"""
from __future__ import annotations

import math
import statistics
import sys
import time
from ctypes import wintypes
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

import probe_overlay  # noqa: E402
import shout.orb as orb  # noqa: E402
from shout.overlay import (GWL_EXSTYLE, IDLE_FRAME_MS, MARGIN_Y, WS_EX_NOACTIVATE,  # noqa: E402
                           WS_EX_TOOLWINDOW, WS_EX_TRANSPARENT, OrbWidget, Overlay,
                           PillWidget, _get_long, user32)
from shout.window import application  # noqa: E402

rows: list[tuple[bool, str, str]] = []
app: QtWidgets.QApplication
QUIET = 0.15            # the overlay meter for ordinary speech at arm's length


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def spin(seconds: float) -> None:
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        app.processEvents()
        QtCore.QThread.msleep(3)


def pixels(img: QtGui.QImage) -> np.ndarray:
    img = img.convertToFormat(QtGui.QImage.Format.Format_RGBA8888_Premultiplied)
    return np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(
        img.height(), img.width(), 4).astype(np.int16)


def shot(ov: Overlay) -> np.ndarray:
    """The orb window as its paintEvent draws it, onto nothing."""
    side = ov.widget.width()
    img = QtGui.QImage(side, side, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    ov.widget.render(img, QtCore.QPoint(0, 0), QtGui.QRegion(),
                     QtWidgets.QWidget.RenderFlag.DrawChildren)
    return pixels(img)


def body(px: np.ndarray, floor: int = 128) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of pixels at least `floor` opaque, inclusive."""
    ys, xs = np.nonzero(px[:, :, 3] >= floor)
    if len(xs) == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def diff(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a - b).mean())


def pose(renderer: orb.Renderer, state: str, level: float, seconds: float = 2.0,
         tune: orb.Tune | None = None) -> tuple[np.ndarray, dict]:
    """A fresh Choreography stepped at 60Hz for `seconds`, fully expanded:
    the frame at render resolution, and the uniforms that drew it."""
    c = orb.Choreography(tune)
    for _ in range(int(round(seconds * 60))):
        c.step(1 / 60, state, level)
    u = c.uniforms(1.0, 1.0)
    return pixels(renderer.render(u, c.tune.win * orb.SUPERSAMPLE)), u


def ring(px: np.ndarray, u: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per degree, the ring's radius in logical px and its opacity there."""
    size = px.shape[0]
    cx, cy = u["center"].x(), size - u["center"].y()
    radii = np.arange(0.5 * u["scale"], 1.3 * u["scale"], 0.5)
    th = np.radians(np.arange(360))
    xs = np.clip(np.rint(cx + np.outer(np.cos(th), radii)).astype(int), 0, size - 1)
    ys = np.clip(np.rint(cy - np.outer(np.sin(th), radii)).astype(int), 0, size - 1)
    a = px[:, :, 3][ys, xs]
    return radii[a.argmax(axis=1)] / orb.SUPERSAMPLE, a.max(axis=1).astype(float)


def crests(radius: np.ndarray) -> tuple[int, float]:
    """(how many waves go round the ring, their height in px): the strongest
    component of the radius between 3 and 20 waves per turn. A frequency, not a
    count of bumps, because the comet's bright head also shifts the radius read
    off opacity, in one place, which a bump count would take for a crest."""
    spectrum = np.abs(np.fft.rfft(radius - radius.mean())) * 2 / len(radius)
    k = 3 + int(spectrum[3:21].argmax())
    return k, float(spectrum[k])


def fine(radius: np.ndarray) -> float:
    """The strongest wave between three and five times the crest count, against
    the crests themselves. That band holds `ripples`' fine octave and nothing
    else: the holding swell's highest term is capped at twice the count, so it
    has no term up there at all. It is the whole of what tells the two states
    apart now that they carry the same number of crests.

    Twice the count will not do as the discriminator -- the swell's own ripple
    term lands there too, and it separated them by only 1.25x in silence.

    Assumes the fine octave is not capped, which holds up to 10 crests: past
    that `ripples` clamps it to RIPPLE_K*2+1 and it drops out of this band. The
    row below reads it at the shipped 9, so that is not in play."""
    spectrum = np.abs(np.fft.rfft(radius - radius.mean())) * 2 / len(radius)
    k = 3 + int(spectrum[3:21].argmax())
    band = spectrum[min(3 * k, len(spectrum) - 1):min(5 * k + 1, len(spectrum))]
    return float(band.max() / spectrum[k])


def main() -> int:
    global app
    app = application(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)

    probe_overlay.app = app
    control = probe_overlay.control_steals_focus()

    level = {"v": 0.0}
    ov = Overlay(enabled=True, level_source=lambda: level["v"], style="orb")
    ov.build()
    # Fullscreen detection is probe_overlay's to test. Pinned off here: an idle
    # overlay correctly hides on a monitor showing something fullscreen, an F11
    # editor included, and these rows need the resting orb on screen.
    ov._last_fullscreen_check = float("inf")
    hwnd = ov._hwnd
    check(ov.style == "orb" and isinstance(ov.widget, OrbWidget),
          "the orb started (OpenGL available)", f"style={ov.style}")
    gl = ov.widget.renderer._gl
    check(hwnd != 0, "orb window created", f"hwnd={hwnd} on {gl.glGetString(0x1F01)}")

    ex = _get_long(wintypes.HWND(hwnd), GWL_EXSTYLE)
    check(ex & WS_EX_NOACTIVATE, "orb: WS_EX_NOACTIVATE set")
    check(ex & WS_EX_TOOLWINDOW, "orb: WS_EX_TOOLWINDOW set (out of alt-tab)")
    check(ex & WS_EX_TRANSPARENT, "orb: WS_EX_TRANSPARENT set (click-through)")

    # -- at rest ------------------------------------------------------------
    ov.set_state("idle")
    spin(2.5)
    check(user32.IsWindowVisible(wintypes.HWND(hwnd)), "the resting orb is on screen")
    rest = shot(ov)
    box = body(rest)
    if box is None:
        check(False, "the resting capsule is painted", "no opaque pixels at all")
        box = (0, 0, 0, 0)
    w, h = box[2] - box[0] + 1, box[3] - box[1] + 1
    shipped = orb.Tune()
    check(abs(w - shipped.rest_w) <= 2 and abs(h - shipped.rest_h) <= 2,
          "at rest it is the small capsule",
          f"{w}x{h}px want {shipped.rest_w:.0f}x{shipped.rest_h:.0f}")
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    rim_a = int(rest[box[1], cx, 3])
    check(50 <= rest[cy, cx, 3] <= 170 and rim_a >= 200,
          "the resting capsule is glass: see-through inside an opaque rim",
          f"centre alpha {rest[cy, cx, 3]}, rim alpha {rim_a}")
    check(ov._timer.interval() == IDLE_FRAME_MS, "at rest the overlay settles to its idle tick",
          f"interval {ov._timer.interval()}ms")
    spin(0.6)
    later = shot(ov)
    still = np.array_equal(rest[:, :, 3], later[:, :, 3])
    check(diff(rest[:, :, :3], later[:, :, :3]) > 0.0 and still,
          "at rest the rim's colour circles while the shape holds still",
          f"colour diff {diff(rest[:, :, :3], later[:, :, :3]):.3f} over 0.6s, "
          f"alpha {'identical' if still else 'CHANGED'}")
    frames = {"n": 0}
    real_prepare = ov.widget.prepare

    def counted() -> None:
        frames["n"] += 1
        real_prepare()

    ov.widget.prepare = counted
    spin(5.0)
    drawn = frames["n"]
    ov.widget.prepare = real_prepare

    r = probe_overlay.rect(hwnd)
    a = ov.work_area()
    check(abs((r.left + r.right) // 2 - (a.left() + a.width() // 2)) <= 1,
          "orb window is centred on the work area")
    bottom = r.top + box[3] + 1
    check(abs((a.bottom() + 1 - bottom) - MARGIN_Y) <= 1,
          "the capsule sits the pill's gap above the taskbar",
          f"gap={a.bottom() + 1 - bottom}px want={MARGIN_Y}px")

    # -- focus: the subject, against the control ----------------------------
    before = int(user32.GetForegroundWindow())
    ov.set_state("recording")
    spin(0.8)
    after = int(user32.GetForegroundWindow())
    check(after != hwnd and after == before, "expanding the orb did not take focus",
          f"foreground {before} -> {after}")

    # -- expanded -------------------------------------------------------------
    big = shot(ov)
    ebox = body(big) or (0, 0, 0, 0)
    ew = ebox[2] - ebox[0] + 1
    ring_d = shipped.size * orb.RING
    check(abs(ew - ring_d) <= 10, "recording inflates it to the orb",
          f"{ew}px wide want ~{ring_d:.0f}")
    ecx, ecy = (ebox[0] + ebox[2]) // 2, (ebox[1] + ebox[3]) // 2
    check(big[ecy, ecx, 3] <= 20, "CONTROL: expanded, the centre is a hole",
          f"centre alpha {big[ecy, ecx, 3]}; proves the glass row can fail")
    # By design the ring's glow, wander and crests reach a few px below the
    # resting body. Growing from the centre instead would move the bottom ~25px,
    # which is the failure this row is for.
    lows = []
    for _ in range(8):
        spin(0.07)
        lows.append((body(shot(ov)) or (0, 0, 0, 0))[3])
    check(max(abs(y - box[3]) for y in lows) <= 6, "its bottom edge stays put as it inflates",
          f"bottom row {box[3]} at rest, {min(lows)}-{max(lows)} expanded")
    ov.widget.prepare = counted
    frames["n"] = 0
    spin(5.0)
    busy_drawn = frames["n"]
    ov.widget.prepare = real_prepare
    # Frames, not CPU share: this process's own CPU time is mostly the probe
    # (numpy, Qt, the control window), and read 9% at rest against 7% expanded
    # in one run. The real app's cost is measured on the running process.
    check(60 <= drawn <= 0.6 * busy_drawn,
          "at rest it draws its circling rim at the idle tick, not the full rate",
          f"{drawn} frames in 5s at rest, {busy_drawn} expanded")
    moved = shot(ov)
    spin(0.1)
    check(not np.array_equal(moved[:, :, 3], shot(ov)[:, :, 3]),
          "CONTROL: expanded, the shape itself moves", "proves the holds-still row can fail")
    level["v"] = 0.05
    spin(0.3)
    check(ov.widget.motion.drive > 0.0, "the mic level reaches the orb while recording",
          f"drive {ov.widget.motion.drive:.2f} from meter {ov.meter:.2f}")
    level["v"] = 0.0

    # -- the resting shape does not carry the last dictation -------------------
    ov.set_state("idle")
    spin(2.5)
    again = shot(ov)[:, :, 3]
    check(np.array_equal(rest[:, :, 3], again),
          "back at rest it is the same capsule",
          f"alpha differs from the first rest in {int((rest[:, :, 3] != again).sum())} pixels")

    # -- crests, voice and the comet, on a fixed clock --------------------------
    renderer = ov.widget.renderer
    shape = {}
    for state in ("recording", "latched", "working"):
        for lv in (0.0, QUIET, 0.8):
            px, u = pose(renderer, state, lv)
            radius, opacity = ring(px, u)
            shape[state, lv] = (px, crests(radius), opacity, radius)
    hold_n, hold_h = shape["recording", 0.0][1]
    latch_n, latch_h = shape["latched", 0.0][1]
    # These two floors are fractions of the tune's own `height`, where they used
    # to be 0.70px and 0.35px. They were re-derived on 17 Sep and that needs
    # saying: `wave` now divides by the sum of its terms instead of 0.6 of it,
    # so the SAME tune draws a crest 23% shorter than it did, by construction.
    # A px floor measured on the old scale would fail a ring that is exactly as
    # wavy as before. The fractions are measured: 0.49x height in silence and
    # +0.28x at arm's length. What stops them passing on a smooth ring is that
    # transcribing, which has no crests at all, reads 0.30x height in this same
    # run -- below the silence floor and pinned by its own row further down.
    SILENT = 0.40 * shipped.height
    RAISE = 0.20 * shipped.height
    check(hold_h >= SILENT and latch_h >= SILENT,
          "holding and hands-free show waves even in silence",
          f"wave height hold {hold_h:.2f}px, hands-free {latch_h:.2f}px, "
          f"floor {SILENT:.2f}px ({hold_h / shipped.height:.2f}x and "
          f"{latch_h / shipped.height:.2f}x the tune's {shipped.height}px crest)")
    for state, name in (("recording", "holding"), ("latched", "hands-free")):
        silent_h = shape[state, 0.0][1][1]
        quiet_n, quiet_h = shape[state, QUIET][1]
        # Measured 17 Sep: 0.68 -> 1.07px holding, 0.88 -> 1.65px hands-free.
        # The voice roughens the water as well as raising it, so less of it
        # arrives as height than the pre-water look put there -- and the curve
        # in orb.VOICE_CURVE is what decides how much of it lands here rather
        # than at a shout. Flattening the crests without moving that curve took
        # this to 1.36x, which is the failure this row is for.
        check(quiet_h >= 1.4 * silent_h and quiet_h - silent_h >= RAISE,
              f"{name}: ordinary speech visibly raises the waves",
              f"wave height {silent_h:.2f}px silent -> {quiet_h:.2f}px at meter "
              f"{QUIET} ({quiet_h / silent_h:.2f}x, +{quiet_h - silent_h:.2f}px, "
              f"floor +{RAISE:.2f}px)")
        check(quiet_n == shape[state, 0.0][1][0],
              f"{name}: and keeps its wave count while you speak",
              f"{shape[state, 0.0][1][0]} silent, {quiet_n} speaking")

    work_px, (work_n, work_h), work_op, _ = shape["working", 0.0]
    check(work_h < 0.5, "transcribing has no crests", f"strongest wave {work_h:.2f}px")
    comet = float(work_op.max() / np.median(work_op))
    rec_op = shape["recording", 0.0][2]
    held = float(rec_op.max() / np.median(rec_op))
    check(comet >= 1.25 * held, "transcribing shows one bright comet on a dimmer ring",
          f"brightest/median opacity round the ring {comet:.2f}, against {held:.2f} "
          f"for holding, which has only the component's own highlight")
    check(diff(shape["recording", 0.0][0], shape["recording", 0.8][0]) > 1.0,
          "CONTROL: holding reacts to the voice level",
          f"mean diff {diff(shape['recording', 0.0][0], shape['recording', 0.8][0]):.2f}")
    check(diff(work_px, shape["working", 0.8][0]) == 0.0,
          "transcribing ignores the level (the mic is closed)",
          f"mean diff {diff(work_px, shape['working', 0.8][0]):.2f}")

    times = []
    u = orb.Choreography().uniforms(1.0, 1.0)
    for _ in range(120):
        t0 = time.perf_counter()
        renderer.render(u, shipped.win * 2)
        times.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(times)
    check(med < 2.0, "a frame renders in under 2ms", f"median {med:.3f}ms")

    # -- the tune ------------------------------------------------------------------
    # The shipped numbers, written out as the code had them before there was a
    # Tune, so a refactor of how they are derived cannot move the look.
    c = orb.Choreography()
    for _ in range(60):
        c.step(1 / 60, "recording", 0.0)
    held = c.uniforms(1.0, 1.0)
    c = orb.Choreography()
    for _ in range(60):
        c.step(1 / 60, "latched", 0.0)
    got = {
        "hold amp": held["holdAmp"], "hold phase/s": held["holdPhase"],
        "latch phase/s": c.latch_phase, "hold turbulence": held["holdTurb"],
        "latch turbulence": c.uniforms(1.0, 1.0)["latchTurb"],
        "comet": c.target("working")["orbit"],
        "tail": math.radians(shipped.comet_tail), "rim": c.target("idle")["sweep"],
        "voice at arm's length": orb.voice_drive(QUIET),
    }
    want = {
        # The ring turns at the speed it always has -- 2.2 rad/s of phase over 7
        # crests, the component's numbers -- and phase advances once per crest,
        # so 9 crests carry it 9/7 as fast. Change a count without meaning to and
        # this row says so. Hands-free turns the other way at its own speed, and
        # since 17 Sep carries the same 9 crests as holding.
        "hold amp": 1.4 / 36.0, "hold phase/s": 2.2 * 9 / 7,
        "latch phase/s": -1.6 * 9 / 12,
        "hold turbulence": 0.8, "latch turbulence": 0.8, "comet": -3.2,
        "tail": 1 / 0.7, "rim": 1.3,
        # The curve that decides how much of the voice lands at arm's length.
        # A square root, as it was until 17 Sep, reads 0.339 here.
        "voice at arm's length": (0.11 / 0.96) ** 0.40,
    }
    wrong = [k for k in want if not np.allclose(got[k], want[k], atol=1e-9)]
    check(not wrong and shipped.hold_crests == 9 and shipped.latch_crests == 9,
          "the default tune is the look that shipped",
          f"differs: {[(k, got[k], want[k]) for k in wrong]}" if wrong else
          "a 1.4px crest on a 72px orb, wave speeds, turbulence, the voice "
          "curve, comet, tail and rim all match")

    calm = dict(wobble=0.0)
    tuned_n = crests(ring(*pose(renderer, "recording", 0.0,
                                tune=orb.Tune(hold_crests=10, **calm)))[0])[0]
    tuned_l = crests(ring(*pose(renderer, "latched", 0.0,
                                tune=orb.Tune(latch_crests=5, **calm)))[0])[0]
    check(tuned_n == 10 and tuned_l == 5, "a tuned wave count is what the ring shows",
          f"asked for 10 holding and 5 hands-free, counted {tuned_n} and {tuned_l}; "
          f"the shipped counts read {hold_n} and {latch_n} above")

    # -- round at every setting ------------------------------------------------
    # The point of the water wave: turbulence adds company to the swell, never
    # corners. Measured big and tall, because at the shipped 1.4px the profile
    # is a handful of pixels of radius and quantises into steps that swamp the
    # shape. A curvature metric was tried first and measured the pixel grid: a
    # pure sine scored the same as a 6th-power spike.
    SHAPE = dict(size=96.0, height=6.0, push=0.0, wobble=0.0, swell=0.0)

    def skew(renderer: orb.Renderer, tune: orb.Tune, level: float,
             state: str = "recording") -> float:
        """How far the tips stand past the ring's median against how deep the
        troughs sit. A sine is symmetric, so ~1; a spike stands out of a shallow
        trough."""
        radius = ring(*pose(renderer, state, level, tune=tune))[0]
        mid = float(np.median(radius))
        return (radius.max() - mid) / max(1e-6, mid - radius.min())

    ROUND = 1.6                 # measured 0.67-1.22 across the range, 17 Sep
    # Both waters, because they are different functions: `wave` for holding and
    # `ripples` for hands-free. A sweep of one says nothing about the other.
    worst = max(((skew(renderer, orb.Tune(**{turb: t}, **SHAPE), lv, state), state, t, lv)
                 for state, turb in (("recording", "hold_turb"), ("latched", "latch_turb"))
                 for t in (0.0, 0.5, 1.0) for lv in (0.0, 0.8)), key=lambda r: r[0])
    check(worst[0] <= ROUND, "no turbulence setting puts a corner on either ring",
          f"worst tip/trough {worst[0]:.2f} ({worst[1]}, turbulence {worst[2]:g}, "
          f"level {worst[3]:g}), ceiling {ROUND}; a sine measures 0.93 here")

    # -- two waters, not two crest counts -------------------------------------
    # Since 17 Sep the states carry the SAME number of crests, on Gabe's call:
    # hands-free is the holding water with ripples riding on it, and the ripples
    # are the whole of what tells them apart. So the row measures the ripples.
    # Read on SHAPE for the same reason the roundness rows are: at the shipped
    # 1.4px crest the radius profile quantises into steps that swamp the fine
    # octave, and the two states separate by 2.2x instead of 3.1x. Turbulence
    # stays at the shipped 0.8 -- at 0 there are no ripples in either water by
    # definition, which is what the Turbulence slider means.
    FINE_LO, FINE_HI = 0.10, 0.07       # measured 0.154-0.309 against 0.012-0.049
    hold_fine = max(fine(ring(*pose(renderer, "recording", lv,
                                    tune=orb.Tune(**SHAPE)))[0]) for lv in (0.0, 0.8))
    latch_fine = min(fine(ring(*pose(renderer, "latched", lv,
                                     tune=orb.Tune(**SHAPE)))[0]) for lv in (0.0, 0.8))
    check(latch_fine >= FINE_LO and hold_fine <= FINE_HI and hold_n == latch_n,
          "holding and hands-free are two waters: only hands-free carries ripples",
          f"the fine octave, as a share of the crests: hands-free {latch_fine:.3f} "
          f"at worst against holding {hold_fine:.3f} at worst, "
          f"{latch_fine / max(hold_fine, 1e-6):.1f}x apart; both carry "
          f"{hold_n} crests, which is why this row exists")

    # Its control: hands-free drawn by the holding swell, which is what it was
    # until 17 Sep. Without it the row could be met by a metric that cannot see
    # the ripples at all -- the share at TWICE the count could not.
    plain_src = orb.FRAG.replace("ripples(latchK", "wave(latchK")
    real_frag, orb.FRAG = orb.FRAG, plain_src
    try:
        plain = orb.Renderer()
    finally:
        orb.FRAG = real_frag
    was_fine = max(fine(ring(*pose(plain, "latched", lv,
                                   tune=orb.Tune(**SHAPE)))[0]) for lv in (0.0, 0.8))
    plain.close()
    check(plain_src != real_frag and was_fine < FINE_LO,
          "CONTROL: hands-free on the holding swell loses those ripples",
          f"fine octave {was_fine:.3f} at its worst, under the {FINE_LO} floor, "
          f"against {latch_fine:.3f} for the ripples wave")

    # The control: the shader as it was before 17 Sep, a raised sine to a power
    # with its mean off, at the spikiness that used to ship. Same tip height, so
    # only the SHAPE differs. Without this row the ceiling above could be met by
    # a metric that cannot see a spike -- which the first one could not.
    old_crest = """  float hw = pow(max(0.5 + 0.5 * sin(holdK * ga + holdPhase), 1e-7), 3.0);
  float lw = pow(max(0.5 + 0.5 * sin(latchK * ga + latchPhase), 1e-7), 3.0);
  float crest = holdAmp * (hw - 0.3125) / 0.6875
              + latchAmp * (lw - 0.3125) / 0.6875;"""
    new_crest = """  float crest = holdAmp * wave(holdK, holdPhase, holdTurb, ga, rippleT)
              + latchAmp * ripples(latchK, latchPhase, latchTurb, ga, rippleT);"""
    spiky_src = orb.FRAG.replace(new_crest, old_crest)
    real_frag, orb.FRAG = orb.FRAG, spiky_src
    try:
        spiky = orb.Renderer()
    finally:
        orb.FRAG = real_frag
    was = max(skew(spiky, orb.Tune(**SHAPE), lv) for lv in (0.0, 0.8))
    spiky.close()
    check(spiky_src != real_frag and was > ROUND,
          "CONTROL: the pow-sharpened crest this replaced fails that ceiling",
          f"tip/trough {was:.2f} at the spikiness that shipped until 17 Sep, "
          f"against {worst[0]:.2f} for the wave")

    loud = orb.Tune(size=96.0, height=6.0, push=16.0, swell=0.25)
    for name, tune in (("shipped", shipped), ("the loudest the sliders reach", loud)):
        c = orb.Choreography(tune)
        edge = cut = 0
        for i in range(240):            # 4s: a whole breath at BREATH_RATE
            c.step(1 / 60, "latched", 1.0)
            if i % 4:
                continue
            u = c.uniforms(1.0, 1.0)
            a = pixels(renderer.render(u, tune.win * orb.SUPERSAMPLE))[:, :, 3]
            edge = max(edge, a[0].max(), a[-1].max(), a[:, 0].max(), a[:, -1].max())
            small = pixels(renderer.render(u, (tune.win - 16) * orb.SUPERSAMPLE))[:, :, 3]
            cut = max(cut, small[0].max(), small[:, -1].max())
        check(edge <= 2 and cut > 40,
              f"{name}: its window holds the loudest orb, glow and all",
              f"{tune.win}px window, brightest edge pixel alpha {edge} over a breath "
              f"at full voice; 16px smaller cuts it at alpha {cut} (control)")

    junk = orb.Tune.resolve({"hold_crests": 7.6, "size": 999, "hue": "red",
                             "wobble": float("nan"), "glow": True, "bogus": 1})
    check(junk == replace(shipped, hold_crests=8, size=96.0)
          and isinstance(junk.hold_crests, int) and orb.Tune.resolve(None) == shipped,
          "a hand-edited orb_tune is clamped to the sliders, and junk keeps the default",
          f"{ {k: getattr(junk, k) for k in ('hold_crests', 'size', 'hue', 'wobble', 'glow')} }")

    # -- a running overlay takes a new tune -----------------------------------------
    same = ov._hwnd
    ov.set_orb_tune(orb.Tune(hue=40.0))
    spin(0.2)
    check(ov._hwnd == same and ov.widget.motion.tune.hue == 40.0,
          "a tune that fits the window is taken in place", f"hwnd {same} -> {ov._hwnd}")
    bigger = orb.Tune(size=90.0)
    ov.set_orb_tune(bigger)
    spin(0.6)
    grown = ov._hwnd
    check(grown != same and ov.widget.width() == bigger.win and ov.has_exstyle()
          and user32.IsWindowVisible(wintypes.HWND(grown)),
          "a tune that needs a bigger window rebuilds the orb in one, with its flags",
          f"{shipped.win}px -> {ov.widget.width()}px, bottom pad "
          f"{shipped.bottom_pad} -> {ov.widget.BOTTOM_PAD}")
    rb = body(shot(ov)) or (0, 0, 0, 0)
    r = probe_overlay.rect(grown)
    a = ov.work_area()
    check(abs((a.bottom() + 1 - (r.top + rb[3] + 1)) - MARGIN_Y) <= 1,
          "and the capsule still sits the pill's gap above the taskbar",
          f"gap={a.bottom() + 1 - (r.top + rb[3] + 1)}px want={MARGIN_Y}px")
    ov.set_orb_tune(shipped)
    spin(0.3)
    hwnd = ov._hwnd

    # -- switching looks live --------------------------------------------------
    old = hwnd
    ov.set_style("pill")
    spin(0.3)
    check(isinstance(ov.widget, PillWidget) and ov.style == "pill",
          "switches to the pill while running")
    check(not user32.IsWindowVisible(wintypes.HWND(old)), "the orb window is gone")
    check(ov.has_exstyle() and user32.IsWindowVisible(wintypes.HWND(ov._hwnd)),
          "the pill is on screen with its flags")
    ov.set_style("orb")
    spin(0.3)
    check(isinstance(ov.widget, OrbWidget) and ov.has_exstyle()
          and user32.IsWindowVisible(wintypes.HWND(ov._hwnd)),
          "and back to the orb, on screen with its flags")

    # -- falling back ----------------------------------------------------------
    calls = {"n": 0}

    def boom(*_args, **_kwargs):
        calls["n"] += 1
        raise RuntimeError("probe: no OpenGL")

    ov.widget.prepare = boom
    ov._dirty = True
    spin(0.3)
    check(calls["n"] >= 1 and ov.style == "pill" and isinstance(ov.widget, PillWidget),
          "a render that fails mid-flight falls back to the pill",
          f"prepare raised {calls['n']}x, style={ov.style}")
    ov.stop()
    spin(0.1)
    ov.teardown()

    real = orb.Renderer
    orb.Renderer = boom
    calls["n"] = 0
    try:
        fallback = Overlay(enabled=True, style="orb")
        fallback.build()
        check(calls["n"] == 1 and fallback.style == "pill"
              and isinstance(fallback.widget, PillWidget),
              "an orb that cannot start draws the pill instead", f"style={fallback.style}")
        fallback.stop()
        spin(0.1)
        fallback.teardown()
    finally:
        orb.Renderer = real
    check(Overlay(style="nonsense").style == "pill", "an unknown style means the pill")
    spin(0.2)

    # -- report ------------------------------------------------------------------
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
        print(f"FAIL {passed}/{len(rows)} orb assertions (control ok)")
        return 1
    print(f"PASS {passed}/{len(rows)} orb assertions, control confirmed focus theft")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
