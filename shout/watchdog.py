"""The safety net that runs independently of the keyboard hook.

Four jobs, all of which exist because the hook cannot be trusted on its own:

1. Feed TICK into the gesture machine so the latch window and the wall-clock
   ceilings fire without needing a keystroke to arrive.

2. Emit a synthetic chord-up when the physical keys are up but we never saw the
   release. That is what an elevated-window input blackout looks like, and without
   this the app would record forever.

3. Detect a dead hook. Windows silently unhooks a slow callback and there is no
   error to catch — listener.running stays True because the message loop is alive,
   it just never gets called. The honest test: if the poll can see both modifiers
   held for 300ms while the machine never got a chord-down, the hook is gone.

4. Reinstall proactively if any callback was ever measured slow enough to be at
   risk of the same fate.
"""
from __future__ import annotations

import logging
import threading
import time

from .gestures import Event
from .winapi import (VK_LCONTROL, VK_LWIN, VK_RCONTROL, VK_RWIN,
                     any_chord_key_down, key_down)

log = logging.getLogger(__name__)

POLL_S = 0.02
DEAD_HOOK_S = 0.30
SLOW_CALLBACK_MS = 50.0


def _physical_chord_down() -> bool:
    ctrl = key_down(VK_LCONTROL) or key_down(VK_RCONTROL)
    win = key_down(VK_LWIN) or key_down(VK_RWIN)
    return ctrl and win


class Watchdog(threading.Thread):
    def __init__(self, gestures, hotkey, dispatch) -> None:
        super().__init__(name="watchdog", daemon=True)
        self._g = gestures
        self._hk = hotkey
        self._dispatch = dispatch
        self._stopping = threading.Event()
        self._unseen_since: float | None = None
        self.dead_hook_recoveries = 0

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        while not self._stopping.wait(POLL_S):
            try:
                self._poll()
            except Exception:
                log.exception("watchdog poll")

    def _poll(self) -> None:
        now = time.perf_counter()
        physical = _physical_chord_down()

        # (1) drive timeouts
        self._dispatch(self._g.handle(Event.TICK, now))

        # (2) a key-up we never received
        if self._g.chord_held and not any_chord_key_down():
            log.warning("chord released without a key-up event; synthesizing one")
            self._hk.chord_up_synthetic(now)

        # (3) hook liveness
        if physical and not self._g.chord_held and self._g.state.value == "idle":
            if self._unseen_since is None:
                self._unseen_since = now
            elif now - self._unseen_since > DEAD_HOOK_S:
                self._unseen_since = None
                self.dead_hook_recoveries += 1
                self._hk.reinstall("chord held but the hook reported nothing")
        else:
            self._unseen_since = None

        # (4) a callback slow enough to be at risk
        if self._hk.worst_callback_ms > SLOW_CALLBACK_MS:
            worst = self._hk.worst_callback_ms
            self._hk.reinstall(f"callback took {worst:.0f}ms")
