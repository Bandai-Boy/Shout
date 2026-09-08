"""Criterion 5: prove Shout cannot get stuck recording.

There is no error to catch for any of these — a stuck recording looks exactly
like a working app that happens to be listening. So each failure mode is forced
deliberately and the recovery is measured:

* a key-up that never arrives (an elevated-window input blackout)
* a hook Windows has silently unhooked
* a held chord that outlives the wall-clock ceiling
* a latched session left running

Each subject has a control, because a watchdog that fires on everything is as
useless as one that fires on nothing.
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The console here is cp1252; harness output is not.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shout.gestures import Command, Event, Gestures, State
from shout.watchdog import DEAD_HOOK_S, Watchdog
from shout.winapi import INPUT, VK_CONTROL, VK_LWIN, _kb_input, key_down, user32

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


def tap(vk: int, up: bool) -> None:
    events = (INPUT * 1)(_kb_input(vk, up))
    user32.SendInput(1, events, ctypes.sizeof(INPUT))
    time.sleep(0.02)


class StubHotkey:
    """Stands in for the real listener so the watchdog's decisions are visible."""

    def __init__(self) -> None:
        self.synthetic_ups = 0
        self.reinstalls: list[str] = []
        self.worst_callback_ms = 0.0
        self.gestures: Gestures | None = None
        self.commands: list[Command] = []

    def chord_up_synthetic(self, t: float) -> None:
        self.synthetic_ups += 1
        if self.gestures is not None:
            self.commands.extend(self.gestures.handle(Event.CHORD_UP, t))

    def reinstall(self, reason: str) -> None:
        self.reinstalls.append(reason)


def run_watchdog(gestures, hotkey, seconds: float) -> list[Command]:
    collected: list[Command] = []
    wd = Watchdog(gestures, hotkey, collected.extend)
    wd.start()
    time.sleep(seconds)
    wd.stop()
    wd.join(timeout=1.0)
    return collected


def main() -> int:
    try:
        # --- 1a. a key-up that never arrived, after a real hold -------
        # The realistic blackout case: the user held the chord for seconds and the
        # release was eaten by an elevated window.
        g = Gestures()
        hk = StubHotkey()
        hk.gestures = g
        g.handle(Event.CHORD_DOWN, time.perf_counter() - 2.0)  # hook saw the press...
        assert g.state is State.HOLDING
        cmds = run_watchdog(g, hk, 0.20) + hk.commands         # ...and never the release
        check(hk.synthetic_ups >= 1,
              "missed key-up after a hold is noticed and a release is synthesized",
              f"synthetic_ups={hk.synthetic_ups}")
        check(not g.capturing and g.state is State.IDLE,
              "recording stopped with no further input", f"state={g.state.value}")
        check(Command.COMMIT in cmds, "and the audio was kept, not discarded",
              f"commands={[c.value for c in cmds]}")

        # --- 1b. same, but the press was short enough to be a tap -----
        # Here the machine is right to sit in pending_latch briefly: it cannot yet
        # know whether a second press is coming. What matters is that it resolves
        # on its own, with no further input.
        g1b = Gestures()
        hk1b = StubHotkey()
        hk1b.gestures = g1b
        g1b.handle(Event.CHORD_DOWN, time.perf_counter())
        cmds1b = run_watchdog(g1b, hk1b, 0.60) + hk1b.commands
        check(g1b.state is State.IDLE,
              "a short tap with a missed key-up resolves itself after the latch window",
              f"state={g1b.state.value}")
        check(Command.COMMIT in cmds1b, "and it too commits rather than hanging",
              f"commands={[c.value for c in cmds1b]}")

        # control: while the machine is idle the watchdog must stay quiet
        g2, hk2 = Gestures(), StubHotkey()
        run_watchdog(g2, hk2, 0.30)
        check(hk2.synthetic_ups == 0 and not hk2.reinstalls,
              "control: an idle machine produces no synthetic events",
              f"ups={hk2.synthetic_ups} reinstalls={len(hk2.reinstalls)}")

        # --- 2. a hook Windows silently unhooked ----------------------
        # Hold the chord for real while the gesture machine hears nothing, which
        # is exactly what a dead hook looks like from the outside.
        g3, hk3 = Gestures(), StubHotkey()
        wd_events: list[Command] = []
        wd = Watchdog(g3, hk3, wd_events.extend)
        wd.start()
        try:
            tap(VK_CONTROL, False)
            tap(VK_LWIN, False)
            time.sleep(DEAD_HOOK_S + 0.25)
        finally:
            tap(VK_LWIN, True)
            tap(VK_CONTROL, True)
            wd.stop()
            wd.join(timeout=1.0)
        check(len(hk3.reinstalls) >= 1,
              "a dead hook is detected from the physical key state alone",
              f"reason={hk3.reinstalls[0] if hk3.reinstalls else 'NONE'}")
        check(wd.dead_hook_recoveries >= 1, "recovery was counted",
              f"recoveries={wd.dead_hook_recoveries}")

        # --- 3. a callback slow enough to be at risk ------------------
        g4, hk4 = Gestures(), StubHotkey()
        hk4.worst_callback_ms = 120.0
        run_watchdog(g4, hk4, 0.15)
        check(len(hk4.reinstalls) >= 1,
              "a slow callback triggers a proactive reinstall before Windows drops it",
              f"reason={hk4.reinstalls[0] if hk4.reinstalls else 'NONE'}")

        g5, hk5 = Gestures(), StubHotkey()
        hk5.worst_callback_ms = 1.0
        run_watchdog(g5, hk5, 0.15)
        check(not hk5.reinstalls,
              "control: a fast callback is left alone",
              f"reinstalls={len(hk5.reinstalls)}")

        # --- 4. wall-clock ceilings -----------------------------------
        g6 = Gestures(ptt_ceiling_s=0.2)
        hk6 = StubHotkey()
        hk6.gestures = g6
        g6.handle(Event.CHORD_DOWN, time.perf_counter())
        cmds = run_watchdog(g6, hk6, 0.45)
        check(Command.COMMIT in cmds,
              "a held chord past the ceiling is committed, not dropped",
              f"commands={[c.value for c in cmds]}")
        check(not g6.capturing, "and capture stopped", f"state={g6.state.value}")

        g7 = Gestures(latch_ceiling_s=0.2)
        hk7 = StubHotkey()
        hk7.gestures = g7
        now = time.perf_counter()
        g7.handle(Event.CHORD_DOWN, now)
        g7.handle(Event.CHORD_UP, now + 0.05)
        g7.handle(Event.CHORD_DOWN, now + 0.15)
        check(g7.state is State.LATCHED, "latched for the ceiling test", g7.state.value)
        cmds7 = run_watchdog(g7, hk7, 0.45)
        check(Command.COMMIT in cmds7,
              "a runaway latched session is committed at its own ceiling",
              f"commands={[c.value for c in cmds7]}")

        # --- 5. nothing left stranded ---------------------------------
        check(not key_down(VK_LWIN) and not key_down(VK_CONTROL),
              "no modifier left stuck down by this probe")

    finally:
        for vk in (VK_LWIN, VK_CONTROL):
            if key_down(vk):
                tap(vk, True)

    failed = 0
    print()
    for ok, name, detail in RESULTS:
        failed += not ok
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}" + (f"  —  {detail}" if detail else ""))
    print(f"\nSTUCK {len(RESULTS) - failed}/{len(RESULTS)} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
