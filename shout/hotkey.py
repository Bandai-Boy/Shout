"""Ctrl+Win via a WH_KEYBOARD_LL hook (pynput).

RegisterHotKey cannot bind a modifier-only chord, and the `keyboard` package was
archived in Feb 2026, so a low-level hook is the only route.

The hook callback is the hard constraint: Windows silently and permanently unhooks
a callback that exceeds LowLevelHooksTimeout, with no exception and no log — the
hotkey just stops working until the process restarts. So the callback here does
exactly three things: read a clock, put a tuple on a queue, return. Every decision
about what the keystroke means happens on the worker thread.

The hook only observes and never suppresses, so Ctrl+Win+D and the other OS
shortcuts keep working normally.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time

from pynput import keyboard

from .gestures import Event

log = logging.getLogger(__name__)

_CTRL = {keyboard.Key.ctrl_l, keyboard.Key.ctrl_r, keyboard.Key.ctrl}
_WIN = {keyboard.Key.cmd_l, keyboard.Key.cmd_r, keyboard.Key.cmd}


class HotkeyListener:
    def __init__(self, on_event) -> None:
        self._on_event = on_event
        self._q: queue.Queue = queue.Queue()
        self._listener: keyboard.Listener | None = None
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        self._down: set = set()
        self._chord = False
        # Callback durations, for the p99 criterion and the watchdog's health check.
        self.durations: collections.deque[float] = collections.deque(maxlen=4096)
        self.installs = 0

    # -- the hook callback: must return in microseconds ---------------------

    def _press(self, key) -> None:
        t0 = time.perf_counter()
        try:
            self._q.put_nowait((True, key, t0))
        except Exception:  # never let an exception kill the listener
            pass
        self.durations.append(time.perf_counter() - t0)

    def _release(self, key) -> None:
        t0 = time.perf_counter()
        try:
            self._q.put_nowait((False, key, t0))
        except Exception:
            pass
        self.durations.append(time.perf_counter() - t0)

    # -- worker: all the actual logic ---------------------------------------

    def _run_worker(self) -> None:
        while not self._stopping.is_set():
            try:
                pressed, key, t = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self._process(pressed, key, t)
            except Exception:
                log.exception("hotkey worker")

    def _process(self, pressed: bool, key, t: float) -> None:
        if pressed:
            self._down.add(key)
        else:
            self._down.discard(key)

        ctrl = bool(self._down & _CTRL)
        win = bool(self._down & _WIN)
        chord = ctrl and win

        if chord and not self._chord:
            self._chord = True
            self._on_event(Event.CHORD_DOWN, t)
        elif not chord and self._chord:
            self._chord = False
            self._on_event(Event.CHORD_UP, t)
        elif chord and pressed and key not in _CTRL and key not in _WIN:
            # A third key while both modifiers are held: Ctrl+Win+D and friends.
            self._on_event(Event.OTHER_KEY, t)

    def chord_up_synthetic(self, t: float) -> None:
        """Called by the watchdog when the physical keys are up but we never saw
        the key-up event — the signature of an elevated-window input blackout."""
        self._down -= (_CTRL | _WIN)
        if self._chord:
            self._chord = False
            self._on_event(Event.CHORD_UP, t)

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        self._stopping.clear()
        self._worker = threading.Thread(target=self._run_worker, name="hotkey-worker",
                                        daemon=True)
        self._worker.start()
        self._install()

    def _install(self) -> None:
        self._listener = keyboard.Listener(on_press=self._press,
                                           on_release=self._release,
                                           suppress=False)
        self._listener.start()
        self.installs += 1
        log.info("keyboard hook installed (install #%d)", self.installs)

    def reinstall(self, reason: str) -> None:
        log.warning("reinstalling keyboard hook: %s", reason)
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            log.exception("stopping the old listener")
        self._down.clear()
        self._chord = False
        self.durations.clear()
        self._install()

    def stop(self) -> None:
        self._stopping.set()
        if self._listener is not None:
            self._listener.stop()

    @property
    def worst_callback_ms(self) -> float:
        return max(self.durations) * 1000 if self.durations else 0.0
