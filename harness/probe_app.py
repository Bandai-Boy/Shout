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

It also owns the way back to a paste that landed nowhere, because that is
composition too: the transcript has to reach the recent list BEFORE inject()
runs, and a left click on the tray icon has to put it back on the clipboard.
inject() is replaced for the whole run, since the real one would paste into
whatever window has focus. The Shout window is opened through the real tray
menu but never shown on screen, and the clipboard the gate borrows is put back,
marked private.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtWidgets  # noqa: E402

import shout.__main__ as shout_main  # noqa: E402
from shout import inject as inj  # noqa: E402
from shout.__main__ import Shout  # noqa: E402
from shout.config import Config  # noqa: E402
from shout.gestures import Event  # noqa: E402
from shout.overlay import (COLLAPSED_W, cursor_monitor, fullscreen_state,  # noqa: E402
                           user32)
from shout.window import application  # noqa: E402

inj.user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]

TRIGGER = QtWidgets.QSystemTrayIcon.ActivationReason.Trigger    # left click
CONTEXT = QtWidgets.QSystemTrayIcon.ActivationReason.Context    # right click
SENTINEL = "probe_app clipboard sentinel"

rows: list[tuple[bool, str, str]] = []


class OffscreenWindow(shout_main.Window):
    """The real window, off the screen: it is an ordinary activating window,
    and showing it would take focus from whatever the user is doing."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)


shout_main.Window = OffscreenWindow


def clipboard_is_private() -> bool:
    fmt = inj.user32.RegisterClipboardFormatW(inj._EXCLUDE)
    return bool(fmt) and bool(inj.user32.IsClipboardFormatAvailable(fmt))


def clipboard_becomes(text: str, seconds: float = 2.0) -> str | None:
    """Polled, because a click emitted from the driver thread is delivered on
    the GUI thread."""
    deadline = time.monotonic() + seconds
    got = inj.get_clipboard_text()
    while got != text and time.monotonic() < deadline:
        time.sleep(0.02)
        got = inj.get_clipboard_text()
    return got

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
    qapp = application(sys.argv[:1])
    qapp.setQuitOnLastWindowClosed(False)

    cfg = Config()
    app = Shout(cfg)

    played: list[str] = []
    real_play = app.cues.play
    app.cues.play = lambda name: (played.append(name), real_play(name))[0]

    # What each paste was handed, beside what the recent list's newest entry
    # held at that instant. Never the real inject(): see the docstring.
    pasted: list[tuple[str, str | None]] = []

    def fake_inject(text: str, _cfg) -> str:
        last = app.recent.last()
        pasted.append((text, last.text if last else None))
        return "pasted"

    inj.inject = fake_inject
    notices: list[str] = []
    app.tray.notify = lambda message, title="Shout": notices.append(message)
    borrowed = inj.get_clipboard_text()
    inj.set_clipboard_text(SENTINEL, private=False)

    returned = threading.Event()

    def drive() -> None:
        try:
            # The pill hides itself on a screen showing a fullscreen app, which
            # would fail every visibility row below for a reason that has
            # nothing to do with the code. Say so rather than reporting a mystery.
            stage("driver: preconditions")
            everywhere, fullscreen_on = fullscreen_state()
            check(not everywhere and cursor_monitor() not in fullscreen_on,
                  "precondition: nothing is fullscreen on the mouse's screen",
                  "the pill hides itself there, by design")

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
            want = app.overlay.expanded_width_for("loading")
            check(abs(app.overlay.pill_width - want) < 1.0,
                  "expanded while loading, so a slow boot is visible",
                  f"width={app.overlay.pill_width:.0f} want={want:.0f}")

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
            want = app.overlay.expanded_width_for("recording")
            check(abs(app.overlay.pill_width - want) < 1.0,
                  "expanded while recording",
                  f"width={app.overlay.pill_width:.0f} want={want:.0f}")
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
            app.transcriber = SimpleNamespace(transcribe=lambda audio: "first probe words")
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

            # --- the way back to a paste that landed nowhere ----------------
            stage("driver: recent list")
            last = app.recent.last()
            check(pasted[:1] == [("first probe words", "first probe words")],
                  "a dictation is in the recent list BEFORE its paste runs",
                  f"paste got {pasted[0][0]!r} with {pasted[0][1]!r} already listed"
                  if pasted else "inject was never reached")
            check(last is not None and last.text == "first probe words",
                  "and stays there after it", f"newest: {last.text if last else None!r}")

            def broken(text: str, _cfg) -> str:
                raise OSError("simulated paste failure")

            inj.inject = broken
            app.transcriber = SimpleNamespace(transcribe=lambda audio: "zebra quartz umbrella")
            logging.disable(logging.CRITICAL)       # the traceback is expected
            try:
                app._finish([0.0] * 16000, time.perf_counter())
            finally:
                logging.disable(logging.NOTSET)
                inj.inject = fake_inject
            texts = [e.text for e in app.recent.newest_first()]
            check(texts == ["zebra quartz umbrella", "first probe words"],
                  "a paste that raises still leaves its words in the list",
                  f"{texts}")

            stage("driver: tray clicks")
            inj.set_clipboard_text(SENTINEL, private=False)
            before = len(notices)
            app.tray.icon.activated.emit(CONTEXT)
            time.sleep(0.3)
            check(inj.get_clipboard_text() == SENTINEL and len(notices) == before,
                  "a right click copies nothing (control: it only opens the menu)",
                  f"clipboard {inj.get_clipboard_text()!r}")
            app.tray.icon.activated.emit(TRIGGER)
            got = clipboard_becomes("zebra quartz umbrella")
            check(got == "zebra quartz umbrella",
                  "a left click on the tray icon copies the last dictation",
                  f"clipboard {got!r}")
            check(clipboard_is_private(), "marked private, like the dictation was")
            said = notices[-1] if len(notices) > before else ""
            check("3 words" in said and not any(
                w in said for w in ("zebra", "quartz", "umbrella")),
                  "and the notice gives a count, never the words",
                  f"{said!r}")

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

    # The tray is the only way into Shout, so "can you reach the window" is a
    # real question about the app. Through the real menu actions, on the GUI
    # thread, the way a click delivers them.
    labels = [a.text() for a in app.tray._menu.actions() if not a.isSeparator()]
    check(labels == ["Recent dictations...", "Cue sounds...", "Quit Shout"],
          "the tray menu opens either page of the window, and quits",
          f"menu={labels}")
    actions = {a.text(): a for a in app.tray._menu.actions()}
    actions["Cue sounds..."].trigger()
    first = app.window
    check(first is not None and first.isVisible()
          and first.stack.currentWidget() is first.lab,
          "Cue sounds... opens the window on the sounds page",
          type(first.stack.currentWidget()).__name__ if first else "no window")
    actions["Recent dictations..."].trigger()
    check(app.window is first and first.stack.currentWidget() is first.recent_page,
          "Recent dictations... switches that same window, not a second one",
          type(app.window.stack.currentWidget()).__name__)
    check(first.recent_page.recent is app.recent,
          "and its list reads the store Shout fills, not a copy")
    first.close()
    actions["Recent dictations..."].trigger()
    check(app.window is not first and app.window.isVisible(),
          "a closed window is rebuilt on the next open, reading the config afresh")
    app.window.close()

    app.tray.icon.activated.emit(TRIGGER)
    check(inj.get_clipboard_text() == SENTINEL and notices == ["Nothing dictated yet"],
          "a left click before any dictation leaves the clipboard alone, and says so",
          f"notices={notices}")

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

    if borrowed is not None:
        # Private: the gate cannot know whether what it borrowed was a password.
        inj.set_clipboard_text(borrowed, private=True)

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"app composition assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
