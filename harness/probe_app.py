"""Gate: the real composition runs, and shuts down.

Every other gate exercises a component alone. This one builds the actual `Shout`
object and runs the arrangement session 3 introduced — one Qt event loop owning
both the pill and the tray — because that is the part that cannot be tested in
pieces and the part that fails in the most confusing way: a process that looks
fine and then will not exit.

That risk is smaller than it was and the assertion is kept anyway. Session 2's
version existed because pystray's detached thread was not a daemon; pystray is
gone, so the same row now asserts the stronger claim that there is no non-daemon
worker thread at all.

Gestures are driven straight into the state machine rather than through synthetic
keystrokes, so this is safe to run without the "quit Shout first" warning that
applies to probe_hook and probe_stuck: no chord is ever pressed, nothing is
pasted, and the recorded audio is discarded rather than committed.

The model is deliberately not loaded — smoke.py owns that — which also covers the
case where a chord arrives before the model is ready.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtWidgets  # noqa: E402

from shout.__main__ import Shout  # noqa: E402
from shout.config import Config  # noqa: E402
from shout.gestures import Event  # noqa: E402
from shout.overlay import COLLAPSED_W, EXPANDED_W, fullscreen_app_running, user32  # noqa: E402

rows: list[tuple[bool, str, str]] = []

# A hang here is byte-identical to a hang anywhere else: no output, no error,
# and on Windows a killed child's buffered stdout is usually lost, so the run
# tells you nothing. The marker plus the watchdog make one run name its own
# wedge point.
STAGE = "start"
WATCHDOG_S = float(os.environ.get("SHOUT_PROBE_WATCHDOG_S", "60"))


def stage(name: str) -> None:
    global STAGE
    STAGE = name
    print(f"  .. {name}", flush=True)


def _watchdog() -> None:
    time.sleep(WATCHDOG_S)
    print(f"WATCHDOG stuck at: {STAGE}  (after {WATCHDOG_S:.0f}s)", flush=True)
    sys.stdout.flush()
    os._exit(1)


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def main() -> int:
    qapp = QtWidgets.QApplication(sys.argv[:1])
    qapp.setQuitOnLastWindowClosed(False)

    cfg = Config()
    app = Shout(cfg)

    played: list[str] = []
    real_play = app.cues.play
    app.cues.play = lambda name: (played.append(name), real_play(name))[0]

    returned = threading.Event()

    def drive() -> None:
        try:
            # The pill hides itself under a fullscreen app, which would fail
            # every visibility row below for a reason that has nothing to do
            # with the code. Say so rather than reporting a mystery.
            stage("driver: preconditions")
            check(not fullscreen_app_running(),
                  "precondition: no fullscreen app is running",
                  "the pill hides itself under one, by design")

            stage("driver: waiting for overlay hwnd")
            for _ in range(100):
                if app.overlay._hwnd:
                    break
                time.sleep(0.05)
            hwnd = app.overlay._hwnd
            check(hwnd != 0, "overlay came up on the Qt loop", f"hwnd={hwnd}")

            # --- persistent from startup: the session 3 change --------------
            # The model is deliberately never loaded here, so this is the boot
            # state, not idle. Asserting "idle" would be asserting something
            # this gate never reaches.
            stage("driver: startup persistence")
            time.sleep(0.4)
            check(visible(hwnd), "on screen from startup (persistent)",
                  f"state={app._tray_state()}")
            check(app._tray_state() == "loading",
                  "reports 'loading' until the model is ready",
                  app._tray_state())
            check(abs(app.overlay.pill_width - EXPANDED_W) < 1.0,
                  "expanded while loading, so a slow boot is visible",
                  f"width={app.overlay.pill_width:.0f}")

            # Session 2 needed a non-daemon pystray thread beside Tk. Nothing
            # should own a thread of its own now. MainThread is non-daemon by
            # definition and is not a worker, so it is excluded too — without
            # that this row can never pass.
            workers = [t for t in threading.enumerate()
                       if t is not threading.current_thread()
                       and t is not threading.main_thread()
                       and not t.daemon and t.is_alive()]
            check(not workers, "no non-daemon worker thread (pystray's is gone)",
                  f"{[t.name for t in workers] or 'none'}")

            # --- push to talk, while the model is still 'loading' -----------
            stage("driver: push to talk")
            t = time.perf_counter()
            app._dispatch(app.gestures.handle(Event.CHORD_DOWN, t))
            time.sleep(0.45)
            check(visible(hwnd), "visible while recording, even before the model is ready",
                  f"state={app._tray_state()}")
            check(app._tray_state() == "recording", "state is 'recording'",
                  app._tray_state())
            check(abs(app.overlay.pill_width - EXPANDED_W) < 1.0,
                  "expanded while recording",
                  f"width={app.overlay.pill_width:.0f}")
            check(played[:1] == ["start"], "start cue fired on chord-down",
                  f"cues={played}")

            # --- latch: two quick taps --------------------------------------
            stage("driver: latch")
            app._dispatch(app.gestures.handle(Event.CHORD_UP, t + 0.10))
            app._dispatch(app.gestures.handle(Event.CHORD_DOWN, t + 0.20))
            time.sleep(0.25)
            check(app._tray_state() == "latched", "double-tap latched",
                  app._tray_state())
            check(visible(hwnd), "still visible while latched")
            check("latch" in played, "latch cue fired on the latch transition",
                  f"cues={played}")
            check(played.count("latch") == 1,
                  "latch cue fired exactly once, not once per watchdog tick",
                  f"count={played.count('latch')}")

            # --- tap again to end the latch ---------------------------------
            # OTHER_KEY is deliberately ignored while latched (typing must not
            # end a hands-free session), so ending it is a tap. That commits, so
            # the transcriber is stubbed to return nothing: this gate is about
            # composition, and injecting into whatever window has focus is
            # exactly what a gate must never do.
            stage("driver: end latch and commit")
            app.ready.set()
            app.transcriber = SimpleNamespace(transcribe=lambda audio: "")
            app._dispatch(app.gestures.handle(Event.CHORD_DOWN, t + 0.40))
            app._dispatch(app.gestures.handle(Event.CHORD_UP, t + 0.45))
            check(played[-1] == "stop", "stop cue fired when the latch ended",
                  f"cues={played}")
            check(app.gestures.state.value == "idle", "back to idle",
                  app.gestures.state.value)

            # It no longer disappears, so what settling looks like is the pill
            # collapsing — asserting visibility here would now pass forever.
            stage("driver: waiting for collapse")
            for _ in range(80):          # _finish runs on the commit thread
                if app.overlay.pill_width - COLLAPSED_W < 1.0:
                    break
                time.sleep(0.05)
            check(visible(hwnd), "still on screen once the commit settles",
                  f"state={app._tray_state()}")
            check(abs(app.overlay.pill_width - COLLAPSED_W) < 1.0,
                  "collapsed again once the commit settles",
                  f"width={app.overlay.pill_width:.0f}")

            # --- shutdown ----------------------------------------------------
            # Deliberately from a worker thread, not the GUI thread: stop() must
            # be safe to call from anywhere, and the teardown that touches Qt
            # objects must wait until app.exec() has returned.
            stage("driver: calling _quit from a worker thread")
            app._quit()
        except Exception as exc:  # a driver crash must not hang the main thread
            check(False, "driver thread raised", repr(exc))
            app._quit()
        finally:
            returned.set()

    threading.Thread(target=_watchdog, name="watchdog", daemon=True).start()
    driver = threading.Thread(target=drive, name="driver", daemon=True)
    driver.start()

    stage("main: building tray + overlay")
    app.tray.build()
    app.overlay.build()
    stage("main: qapp.exec()")
    qapp.exec()                # blocks until _quit; this returning IS the test
    stage("main: teardown")
    app.overlay.teardown()
    app.tray.teardown()
    stage("main: joining")

    check(returned.wait(timeout=5), "the Qt event loop returned after quit")

    deadline = time.perf_counter() + 5.0
    alive: list[threading.Thread] = []
    while time.perf_counter() < deadline:
        alive = [t for t in threading.enumerate() if not t.daemon and t.is_alive()
                 and t is not threading.current_thread()]
        if not alive:
            break
        time.sleep(0.05)
    check(not alive, "no non-daemon thread outlives the quit (the process can exit)",
          f"still alive: {[t.name for t in alive]}")

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"app composition assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
