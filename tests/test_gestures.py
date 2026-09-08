"""Synthetic gesture sequences against the pure state machine.

Pass means every case explicitly reported OK — not "no case said FAIL".
Per-case rows are printed so a green run is auditable at a glance.
Includes a negative control (a sequence that must NOT commit) and a
self-check that the harness can actually report a failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The console here is cp1252; harness output is not.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shout.gestures import Command, Event, Gestures, State

D, U, K, T = Event.CHORD_DOWN, Event.CHORD_UP, Event.OTHER_KEY, Event.TICK
START, COMMIT, DISCARD = Command.START_CAPTURE, Command.COMMIT, Command.DISCARD


def run(seq, **kw):
    """seq is [(event, t_seconds), ...]. Returns (commands, final_state)."""
    g = Gestures(**kw)
    out = []
    for ev, t in seq:
        out.extend(g.handle(ev, t))
    return out, g.state


# (name, sequence, expected commands, expected final state)
CASES = [
    # --- push to talk ---------------------------------------------------
    ("ptt: 2s hold commits on release",
     [(D, 0.0), (U, 2.0)], [START, COMMIT], State.IDLE),
    ("ptt: hold just past tap_max commits immediately",
     [(D, 0.0), (U, 0.31)], [START, COMMIT], State.IDLE),
    ("ptt: 10 minute hold is stopped by the ceiling",
     [(D, 0.0), (T, 121.0)], [START, COMMIT], State.DEAD_CHORD),
    ("ptt: ceiling then release returns to idle",
     [(D, 0.0), (T, 121.0), (U, 121.5)], [START, COMMIT], State.IDLE),
    ("ptt: tick before the ceiling does nothing",
     [(D, 0.0), (T, 60.0)], [START], State.HOLDING),

    # --- short tap that is NOT a double-tap ------------------------------
    ("tap: short tap waits out the latch window then commits",
     [(D, 0.0), (U, 0.10), (T, 0.50)], [START, COMMIT], State.IDLE),
    ("tap: still pending inside the latch window",
     [(D, 0.0), (U, 0.10), (T, 0.30)], [START], State.PENDING_LATCH),

    # --- double-tap to latch --------------------------------------------
    ("latch: tap-tap inside the window latches",
     [(D, 0.0), (U, 0.08), (D, 0.20)], [START], State.LATCHED),
    ("latch: second key-up does not end a latched session",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28)], [START], State.LATCHED),
    ("latch: long silence while latched keeps recording",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28), (T, 240.0)], [START], State.LATCHED),
    ("latch: tap again ends it and commits",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28), (D, 90.0)],
     [START, COMMIT], State.DEAD_CHORD),
    ("latch: end, release, back to idle",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28), (D, 90.0), (U, 90.1)],
     [START, COMMIT], State.IDLE),
    ("latch: ceiling stops a runaway latched session and keeps the audio",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28), (T, 601.0)],
     [START, COMMIT], State.IDLE),
    ("latch: exactly at the window boundary still latches",
     [(D, 0.0), (U, 0.10), (D, 0.45)], [START], State.LATCHED),

    # --- two separate quick push-to-talks, NOT a latch -------------------
    ("two ptts: gap past the window commits the first and starts a second",
     [(D, 0.0), (U, 0.10), (D, 0.60)], [START, COMMIT, START], State.HOLDING),
    ("two ptts: second one then commits on its own release",
     [(D, 0.0), (U, 0.10), (D, 0.60), (U, 2.0)],
     [START, COMMIT, START, COMMIT], State.IDLE),

    # --- third key cancels ----------------------------------------------
    ("cancel: Ctrl+Win+D discards the clip",
     [(D, 0.0), (K, 0.20)], [START, DISCARD], State.DEAD_CHORD),
    ("cancel: discard then release returns to idle",
     [(D, 0.0), (K, 0.20), (U, 0.40)], [START, DISCARD], State.IDLE),
    ("cancel: a third key while latched is normal typing, ignored",
     [(D, 0.0), (U, 0.08), (D, 0.20), (U, 0.28), (K, 5.0)], [START], State.LATCHED),

    # --- noise and idempotence ------------------------------------------
    ("idle: a stray key-up is ignored",
     [(U, 0.0)], [], State.IDLE),
    ("idle: a stray third key is ignored",
     [(K, 0.0)], [], State.IDLE),
    ("idle: ticks forever produce nothing",
     [(T, 1.0), (T, 500.0), (T, 5000.0)], [], State.IDLE),
    ("dead chord: extra presses are absorbed until release",
     [(D, 0.0), (K, 0.1), (D, 0.2), (K, 0.3), (U, 0.4)],
     [START, DISCARD], State.IDLE),

    # --- negative control: must never commit -----------------------------
    ("control: a chord that is still held has committed nothing",
     [(D, 0.0), (T, 5.0), (T, 10.0)], [START], State.HOLDING),
]


def main() -> int:
    failures = 0
    for name, seq, want_cmds, want_state in CASES:
        got_cmds, got_state = run(seq)
        ok = got_cmds == want_cmds and got_state == want_state
        failures += not ok
        mark = "ok  " if ok else "FAIL"
        print(f"[{mark}] {name}")
        if not ok:
            print(f"        commands: got {[c.value for c in got_cmds]} "
                  f"want {[c.value for c in want_cmds]}")
            print(f"        state:    got {got_state.value} want {want_state.value}")

    # Positive control: prove this harness can report a failure at all.
    bad_cmds, _ = run([(D, 0.0), (U, 2.0)])
    control_detects = bad_cmds != [START]
    print(f"[{'ok  ' if control_detects else 'FAIL'}] control: harness can detect a wrong result")
    failures += not control_detects

    total = len(CASES) + 1
    print(f"\nGESTURES {total - failures}/{total} ok, {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
