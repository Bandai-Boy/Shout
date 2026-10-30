"""Gate: a launch takes over from a frozen Shout, and never from a live one.

On 21 Oct 2026 a graphics driver installed under a running Shout, the orb's
makeCurrent never returned, and the GUI thread sat in the driver holding the
GIL. Every thread stopped, while the single-instance mutex stayed held, so each
click on the icon logged "Shout is already running" and did nothing.

This drives the real `shout.instance.claim` and `start_beating` in child
processes, each holding the mutex the way one kind of Shout would:

  healthy   claims and beats from a QTimer, as Shout.run does
  frozen    the same, then blocks its GUI thread in native code WITH the GIL
            held (a PyDLL call), the shape of the 21 Oct hang. The real one sat
            in a kernel wait, which TerminateProcess cannot finish; that part
            cannot be staged, and claim only logs it.
  legacy    the pre-fix claim alone, no heartbeat: the 21 Oct process itself
  foreign   a heartbeat block naming a pid that is not Shout, never beating

Controls, each run against the same holder as the row it guards: the pre-fix
claim must refuse the frozen copy (the bug), a 0s watch must take over from a
healthy copy (the watch is what protects it), and with the image check off the
foreign pid must die (the check is what spares it).

Each child uses its own mutex name, so a running Shout is never touched.
"""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shout import instance  # noqa: E402

PY = ROOT / ".venv" / "Scripts" / "python.exe"
SYNCHRONIZE = 0x00100000
PROCESS_TERMINATE = 0x0001
WAIT_TIMEOUT = 258


def _prefix_claim(name: str) -> bool:
    """The claim as it shipped until 21 Oct, verbatim bar the name."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, name)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


# -- child side --------------------------------------------------------------

def _beat_forever(freeze: bool = False) -> None:
    from PySide6 import QtCore
    app = QtCore.QCoreApplication([])
    timer = instance.start_beating()  # noqa: F841
    if freeze:
        def hang() -> None:
            print("PROBE freezing", flush=True)
            ctypes.PyDLL("kernel32").Sleep(0xFFFFFFFF)
        QtCore.QTimer.singleShot(1500, hang)
    app.exec()


def child(mode: str, name: str, *args: str) -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="LOG %(message)s")
    print(f"PROBE pid {os.getpid()}", flush=True)
    if mode in ("healthy", "frozen"):
        print(f"PROBE claimed {instance.claim(name, 0)}", flush=True)
        _beat_forever(freeze=mode == "frozen")
    elif mode == "legacy":
        print(f"PROBE claimed {_prefix_claim(name)}", flush=True)
        time.sleep(600)
    elif mode == "foreign":
        print(f"PROBE claimed {instance.claim(name, 0)}", flush=True)
        instance._block.pid = int(args[0])
        time.sleep(600)
    elif mode == "launch":
        wait_s, flags = float(args[0]), args[1:]
        if "nocheck" in flags:
            instance._ours = lambda _process: True
        claimed = _prefix_claim(name) if "prefix" in flags else instance.claim(name, wait_s)
        print(f"PROBE claimed {claimed}", flush=True)
        if claimed and "stay" in flags:
            _beat_forever()


# -- parent side -------------------------------------------------------------

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = ctypes.c_void_p


def alive(pid: int) -> bool:
    h = _k32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    try:
        return _k32.WaitForSingleObject(ctypes.c_void_p(h), 0) == WAIT_TIMEOUT
    finally:
        _k32.CloseHandle(ctypes.c_void_p(h))


def kill(pid: int) -> None:
    h = _k32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if h:
        _k32.TerminateProcess(ctypes.c_void_p(h), 1)
        _k32.CloseHandle(ctypes.c_void_p(h))


class Child:
    """A probe child. Its own pid comes from its first line: the venv's
    python.exe is uv's trampoline, so Popen.pid is not the interpreter."""
    started: list["Child"] = []

    def __init__(self, mode: str, name: str, *args: str) -> None:
        self.proc = subprocess.Popen(
            [str(PY), "-u", __file__, mode, name, *args], cwd=str(ROOT),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
        self.lines: queue.Queue[str] = queue.Queue()
        self.seen: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        Child.started.append(self)
        self.pid = int(self.expect("PROBE pid ", 30) or 0)

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line.rstrip())

    def expect(self, prefix: str, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.1)
            except queue.Empty:
                continue
            self.seen.append(line)
            if line.startswith(prefix):
                return line[len(prefix):]
        return None

    def claimed(self, timeout: float = 30) -> str | None:
        return self.expect("PROBE claimed ", timeout)

    def logged(self, text: str) -> bool:
        while not self.lines.empty():
            self.seen.append(self.lines.get())
        return any(text in line for line in self.seen)

    @classmethod
    def kill_all(cls) -> None:
        for c in cls.started:
            if c.pid:
                kill(c.pid)
            c.proc.kill()


def beats_moved(name: str, over_s: float = 1.2) -> tuple[bool, str]:
    block = instance.open_block(name)
    if block is None:
        return False, "no block"
    a = block.beats
    time.sleep(over_s)
    b = block.beats
    return b != a, f"{a} -> {b}"


def main() -> int:
    if len(sys.argv) > 2:
        child(*sys.argv[1:])
        return 0
    rows: list[tuple[bool, str, str]] = []

    def check(ok: bool, name: str, detail: str = "") -> None:
        rows.append((bool(ok), name, detail))

    tag = f"Local\\ShoutProbe{os.getpid()}"
    try:
        # A: a live Shout keeps its place.
        name = tag + "A"
        holder = Child("healthy", name)
        check(holder.claimed() == "True", "healthy holder claimed")
        moved, d = beats_moved(name)
        check(moved, "premise: the healthy holder beats", d)
        t0 = time.monotonic()
        launch = Child("launch", name, "5")
        got = launch.claimed()
        dt = time.monotonic() - t0
        check(got == "False", "a launch is refused by a healthy copy", f"claimed {got}")
        check(dt < 4.0, f"refused without waiting out the watch ({dt:.1f}s)", "")
        check(alive(holder.pid), "the healthy copy is still running")
        got = Child("launch", name, "0").claimed()
        check(got == "True", "CONTROL: a 0s watch takes over from a healthy copy",
              f"claimed {got}")

        # B: a frozen Shout is replaced.
        name = tag + "B"
        holder = Child("frozen", name)
        check(holder.claimed() == "True", "frozen holder claimed")
        check(holder.expect("PROBE freezing", 15) is not None, "premise: holder froze")
        moved, d = beats_moved(name)
        check(not moved, "premise: the frozen holder stopped beating", d)
        got = Child("launch", name, "5", "prefix").claimed()
        check(got == "False", "CONTROL: the pre-fix claim refuses the frozen copy",
              f"claimed {got}")
        launch = Child("launch", name, "5", "stay")
        got = launch.claimed()
        check(got == "True", "a launch takes over from a frozen copy", f"claimed {got}")
        check(not alive(holder.pid), "the frozen copy was ended")
        check(launch.logged("ended the frozen copy"), "the takeover logged the kill")
        moved, d = beats_moved(name)
        check(moved, "the new copy beats in the same block", d)
        got = Child("launch", name, "5").claimed()
        check(got == "False", "a third launch is refused by the new copy", f"claimed {got}")

        # C: a copy from before the heartbeat, frozen or not.
        name = tag + "C"
        holder = Child("legacy", name)
        check(holder.claimed() == "True", "legacy holder claimed")
        launch = Child("launch", name, "5")
        got = launch.claimed()
        check(got == "True", "a launch runs past a copy with no heartbeat", f"claimed {got}")
        check(launch.logged("without a heartbeat"), "and says why")
        check(alive(holder.pid), "it kills nothing it has no pid for")

        # D: a stale pid that is not Shout is left alone.
        name = tag + "D"
        bystander = subprocess.Popen(["ping", "-n", "120", "127.0.0.1"],
                                     stdout=subprocess.DEVNULL)
        try:
            holder = Child("foreign", name, str(bystander.pid))
            check(holder.claimed() == "True", "foreign holder claimed")
            launch = Child("launch", name, "5")
            got = launch.claimed()
            check(got == "True", "a launch takes over from a stale block", f"claimed {got}")
            check(launch.logged("no longer Shout"), "it names the pid as not Shout")
            check(bystander.poll() is None, "the unrelated process is still running")
            # Its own holder: the launch above took over, so the block now
            # names that launch, not the bystander.
            name = tag + "D2"
            Child("foreign", name, str(bystander.pid)).claimed()
            got = Child("launch", name, "5", "nocheck").claimed()
            time.sleep(0.5)
            check(got == "True" and bystander.poll() is not None,
                  "CONTROL: with the image check off, the unrelated process dies",
                  f"claimed {got}, ping exit {bystander.poll()}")
        finally:
            bystander.kill()
    finally:
        Child.kill_all()

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if not ok and detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"single-instance assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
