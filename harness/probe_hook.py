"""Drives the REAL keyboard hook with synthetic input.

The gesture logic is already covered by tests/test_gestures.py with no hardware.
What this proves is the layer underneath: that a WH_KEYBOARD_LL hook actually
delivers chord transitions to that machine, and that the callback returns fast
enough not to be silently unhooked by Windows.

Safety: this synthesizes real Ctrl and Win keystrokes system-wide. Ctrl is always
pressed first and released last, so Win never sees a press-release with no
intervening key and the Start menu cannot open. A finally block releases both
regardless of how the probe exits — a stranded Win key would be worse than a
failed test. The third-key tests use F13, which nothing binds.
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
from shout.hotkey import HotkeyListener
from shout.winapi import (INPUT, VK_CONTROL, VK_LWIN, key_down, user32,
                          _kb_input)

VK_F13 = 0x7C
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


def tap(vk: int, up: bool) -> None:
    events = (INPUT * 1)(_kb_input(vk, up))
    user32.SendInput(1, events, ctypes.sizeof(INPUT))
    time.sleep(0.02)


class Collector:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.commands: list[Command] = []
        self.gestures = Gestures()

    def __call__(self, event: Event, t: float) -> None:
        self.events.append(event)
        self.commands.extend(self.gestures.handle(event, t))

    def reset(self) -> None:
        self.events.clear()
        self.commands.clear()
        self.gestures = Gestures()


def settle(seconds: float = 0.25) -> None:
    time.sleep(seconds)


def main() -> int:
    col = Collector()
    hk = HotkeyListener(col)
    hk.start()
    settle(0.5)  # let the hook install

    try:
        # --- 1. a deliberate hold -------------------------------------
        col.reset()
        tap(VK_CONTROL, False)
        tap(VK_LWIN, False)
        time.sleep(0.6)
        tap(VK_LWIN, True)
        tap(VK_CONTROL, True)
        settle()
        check(Event.CHORD_DOWN in col.events and Event.CHORD_UP in col.events,
              "real hook delivers chord down and up",
              f"events={[e.value for e in col.events]}")
        check(col.commands == [Command.START_CAPTURE, Command.COMMIT],
              "a 600ms hold produces start then commit",
              f"commands={[c.value for c in col.commands]}")
        check(col.gestures.state is State.IDLE, "back to idle after the hold",
              col.gestures.state.value)

        # --- 2. double-tap to latch -----------------------------------
        # Ctrl stays down; tapping Win twice makes and breaks the chord, which is
        # the same transition sequence as tapping both keys together.
        col.reset()
        tap(VK_CONTROL, False)
        tap(VK_LWIN, False)
        tap(VK_LWIN, True)
        tap(VK_LWIN, False)
        settle()
        latched = col.gestures.state is State.LATCHED
        check(latched, "double-tap through the real hook latches",
              col.gestures.state.value)
        # end it
        tap(VK_LWIN, True)
        tap(VK_LWIN, False)
        tap(VK_LWIN, True)
        tap(VK_CONTROL, True)
        settle()
        check(Command.COMMIT in col.commands, "tapping again ends the latched session",
              f"commands={[c.value for c in col.commands]}")

        # --- 3. a third key cancels -----------------------------------
        col.reset()
        tap(VK_CONTROL, False)
        tap(VK_LWIN, False)
        tap(VK_F13, False)
        tap(VK_F13, True)
        tap(VK_LWIN, True)
        tap(VK_CONTROL, True)
        settle()
        check(Command.DISCARD in col.commands,
              "a third key while the chord is held discards the clip",
              f"commands={[c.value for c in col.commands]}")

        # --- 4. controls: things that must NOT fire -------------------
        col.reset()
        tap(VK_CONTROL, False)
        time.sleep(0.4)
        tap(VK_CONTROL, True)
        settle()
        check(col.events == [], "control: Ctrl alone produces no chord events",
              f"events={[e.value for e in col.events]}")
        check(col.commands == [], "control: Ctrl alone starts no capture")

        col.reset()
        tap(VK_F13, False)
        tap(VK_F13, True)
        settle()
        check(col.events == [], "control: an unrelated key produces no chord events",
              f"events={[e.value for e in col.events]}")

        # --- 5. the constraint that silently kills the hook -----------
        worst = hk.worst_callback_ms
        durations = sorted(hk.durations)
        p99 = durations[int(len(durations) * 0.99)] * 1000 if durations else 0.0
        check(len(durations) > 20, "callback timings were actually collected",
              f"n={len(durations)} (a probe that measured nothing is not a pass)")
        check(p99 <= 5.0, "hook callback p99 within budget",
              f"p99={p99:.3f}ms worst={worst:.3f}ms — Windows unhooks above ~1000ms")
        check(hk.installs == 1, "the hook was never reinstalled during the run",
              f"installs={hk.installs}")

    finally:
        # Never leave a modifier stranded down.
        for vk in (VK_LWIN, VK_CONTROL):
            if key_down(vk):
                tap(vk, True)
        hk.stop()

    check(not key_down(VK_LWIN) and not key_down(VK_CONTROL),
          "no modifier left stuck down after the probe")

    failed = 0
    print()
    for ok, name, detail in RESULTS:
        failed += not ok
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}" + (f"  —  {detail}" if detail else ""))
    print(f"\nHOOK {len(RESULTS) - failed}/{len(RESULTS)} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
