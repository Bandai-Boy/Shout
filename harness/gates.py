"""Run every gate, serially, and report one summary.

Serial on purpose: these mutate shared machine state (the clipboard, the physical
keyboard, the foreground window). Running them concurrently would have them
fighting each other and reporting nonsense.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"

GATES = [
    ("gestures", ROOT / "tests" / "test_gestures.py"),
    ("smoke", ROOT / "harness" / "smoke.py"),
    ("hook", ROOT / "harness" / "probe_hook.py"),
    ("inject", ROOT / "harness" / "probe_inject.py"),
    ("stuck", ROOT / "harness" / "probe_stuck.py"),
]


def main() -> int:
    rows = []
    for name, script in GATES:
        t0 = time.perf_counter()
        proc = subprocess.run([str(PY), str(script)], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(ROOT))
        dt = time.perf_counter() - t0
        tail = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        summary = tail[-1] if tail else "NO OUTPUT"
        rows.append((proc.returncode == 0, name, summary, dt))
        if proc.returncode != 0:
            print(proc.stdout)
            print(proc.stderr, file=sys.stderr)

    failed = sum(not ok for ok, _, _, _ in rows)
    print("\n" + "=" * 72)
    for ok, name, summary, dt in rows:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:9s} {dt:5.1f}s   {summary}")
    print("=" * 72)
    print(f"{len(rows) - failed}/{len(rows)} gates passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
