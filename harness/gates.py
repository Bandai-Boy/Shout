"""Run every gate, serially, and report one summary.

Serial on purpose: these mutate shared machine state (the clipboard, the physical
keyboard, the foreground window). Running them concurrently would have them
fighting each other and reporting nonsense.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

# A probe printing a non-ASCII character in its summary line used to take the
# whole runner down with a cp1252 UnicodeEncodeError -- after every gate had
# already run. Force utf-8 in both directions so a gate can never be lost to
# the way its output was encoded.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
# Overridable so the timeout path itself can be exercised quickly.
GATE_TIMEOUT_S = int(os.environ.get("SHOUT_GATE_TIMEOUT_S", "180"))

GATES = [
    ("gestures", ROOT / "tests" / "test_gestures.py"),
    ("smoke", ROOT / "harness" / "smoke.py"),
    ("tail", ROOT / "harness" / "probe_tail.py"),
    ("hook", ROOT / "harness" / "probe_hook.py"),
    ("inject", ROOT / "harness" / "probe_inject.py"),
    ("stuck", ROOT / "harness" / "probe_stuck.py"),
    ("cues", ROOT / "harness" / "probe_cues.py"),
    ("route", ROOT / "harness" / "probe_route.py"),
    ("lab", ROOT / "harness" / "probe_lab.py"),
    ("overlay", ROOT / "harness" / "probe_overlay.py"),
    ("app", ROOT / "harness" / "probe_app.py"),
    ("repo", ROOT / "harness" / "probe_repo.py"),
]


def main() -> int:
    rows = []
    for name, script in GATES:
        t0 = time.perf_counter()
        timed_out = False
        try:
            proc = subprocess.run(
                [str(PY), str(script)], capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=str(ROOT),
                env={**os.environ, "PYTHONIOENCODING": "utf-8",
                     # Flush as produced rather than block-buffering into a
                     # pipe. Note this still does not reliably recover a killed
                     # child's output on Windows -- a TIMED OUT row is usually
                     # all you get, which is why it names the likely cause.
                     "PYTHONUNBUFFERED": "1"},
                timeout=GATE_TIMEOUT_S)
            out, err = proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            # A gate that wedges must not take the suite with it. This is not
            # hypothetical: dropping tray.stop() from _quit hangs probe_app
            # forever on a non-daemon thread, with no error of its own.
            timed_out = True
            raw = exc.stdout or ""
            out = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            err = ""
        dt = time.perf_counter() - t0
        if timed_out:
            rows.append((False, name, f"TIMED OUT after {GATE_TIMEOUT_S}s "
                                      f"(wedged, not slow — see its output above)", dt))
            print(out)
            continue
        tail = [ln for ln in out.splitlines() if ln.strip()]
        summary = tail[-1] if tail else "NO OUTPUT"
        rows.append((proc.returncode == 0, name, summary, dt))
        if proc.returncode != 0:
            print(out)
            print(err, file=sys.stderr)

    failed = sum(not ok for ok, _, _, _ in rows)
    print("\n" + "=" * 72)
    for ok, name, summary, dt in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:9s} {dt:5.1f}s   {summary}")
    print("=" * 72)
    print(f"{len(rows) - failed}/{len(rows)} gates passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
