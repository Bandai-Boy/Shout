"""Gate: the real composition runs, and shuts down.

Every other gate exercises a component alone. This one builds the actual `Shout`
object and runs the arrangement session 2 introduced — Tk owning the main thread
with pystray detached beside it — because that is the part that cannot be tested
in pieces and the part that fails in the most confusing way: a process that looks
fine and then will not exit, because pystray's detached thread is not a daemon.

Gestures are driven straight into the state machine rather than through synthetic
keystrokes, so this is safe to run without the "quit Shout first" warning that
applies to probe_hook and probe_stuck: no chord is ever pressed, nothing is
pasted, and the recorded audio is discarded rather than committed.

The model is deliberately not loaded — smoke.py owns that — which also covers the
case where a chord arrives before the model is ready.
"""
from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shout.__main__ import Shout
from shout.config import Config
from shout.gestures import Event
from shout.overlay import user32

rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def visible(hwnd: int) -> bool:
    return bool(user32.IsWindowVisible(wintypes.HWND(hwnd)))


def main() -> int:
    cfg = Config()
    app = Shout(cfg)

    played: list[str] = []
    real_play = app.cues.play
    app.cues.play = lambda name: (played.append(name), real_play(name))[0]

    returned = threading.Event()

    def drive() -> None:
        try:
            # Wait for the overlay to exist on the main thread.
            for _ in range(100):
                if app.overlay._hwnd:
                    break
                time.sleep(0.05)
            hwnd = app.overlay._hwnd
            check(hwnd != 0, "overlay came up alongside the detached tray",
                  f"hwnd={hwnd}")
            check(not visible(hwnd), "hidden while idle")

            tray_thread = [t for t in threading.enumerate()
                           if t is not threading.current_thread()
                           and not t.daemon and t.is_alive()]
            check(any(tray_thread), "pystray is running on its own thread",
                  f"{len(tray_thread)} non-daemon worker(s)")

            # --- push to talk, while the model is still 'loading' -----------
            t = time.perf_counter()
            app._dispatch(app.gestures.handle(Event.CHORD_DOWN, t))
            time.sleep(0.25)
            check(visible(hwnd), "visible while recording, even before the model is ready",
                  f"state={app._tray_state()}")
            check(app._tray_state() == "recording", "state is 'recording'",
                  app._tray_state())
            check(played[:1] == ["start"], "start cue fired on chord-down",
                  f"cues={played}")

            # --- latch: two quick taps --------------------------------------
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
            app.ready.set()
            app.transcriber = SimpleNamespace(transcribe=lambda audio: "")
            app._dispatch(app.gestures.handle(Event.CHORD_DOWN, t + 0.40))
            app._dispatch(app.gestures.handle(Event.CHORD_UP, t + 0.45))
            check(played[-1] == "stop", "stop cue fired when the latch ended",
                  f"cues={played}")
            check(app.gestures.state.value == "idle", "back to idle",
                  app.gestures.state.value)

            for _ in range(60):          # _finish runs on the commit thread
                if not visible(hwnd):
                    break
                time.sleep(0.05)
            check(not visible(hwnd), "hidden again once the commit settles",
                  f"state={app._tray_state()}")

            # --- shutdown ----------------------------------------------------
            app._quit()
        except Exception as exc:  # a driver crash must not hang the main thread
            check(False, "driver thread raised", repr(exc))
            app._quit()
        finally:
            returned.set()

    driver = threading.Thread(target=drive, name="driver", daemon=True)
    driver.start()
    app.tray.run_detached()
    app.overlay.run()          # blocks until _quit; this returning IS the test

    check(returned.wait(timeout=5), "overlay.run() returned after quit")

    # If pystray's non-daemon thread outlives the quit, the real app hangs on
    # exit with no window and no log line to explain it.
    deadline = time.perf_counter() + 5.0
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
