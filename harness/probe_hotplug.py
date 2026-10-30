"""Manual probe: the cues survive a USB audio device being plugged in and out,
and the device cannot take the default devices once its endpoints are disabled.

NOT in gates.py, because a person has to plug something in. Run it, then plug
the device in, wait ~5s, unplug it, wait ~5s, and plug it in again.

Written 16 Sep 2026 on the theory that a PS4 controller's USB audio killed
Shout's held cue stream (it had no session on any endpoint). The run REFUTED it:
the controller took the default mic but not the output (its earphone endpoint
was already disabled), and CONTROL kept playing through the plug and the unplug,
so the verdict was INCONCLUSIVE. The cause is still unknown; see shout.devices.

SUBJECT and CONTROL are separate processes, each holding a real `Cues` and
blipping at -90dBFS: inaudible in headphones, and still above the session
meter's floor (measured: 3e-5 reads, 0 does not). probe_route's QUIET level was
plainly audible as a stream of ticks over minutes of waiting. CONTROL has its
watcher switched off, which is the pre-fix `Cues`. Where each one is heard comes
from Windows' per-session peak meters (see probe_route). If CONTROL is still
heard after the unplug, this run did not reproduce the bug and the verdict is
INCONCLUSIVE, whatever SUBJECT did.

Second phase, after the unplug: every endpoint the device brought is disabled
(IPolicyConfig::SetEndpointVisibility), and the replug has to leave the default
output and input where they were. That DISABLES THE DEVICE'S AUDIO on this
machine; re-enable it from the Sound control panel (mmsys.cpl, right-click,
"Show Disabled Devices"). Windows keys endpoints by USB port for a device with
no serial number, so the same controller in a different port can arrive fresh.
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
from ctypes import POINTER, byref, c_uint, c_ulong, c_void_p
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import probe_route as pr   # noqa: E402

from shout.cues import Cues   # noqa: E402

WAIT_ARRIVAL_S = 3600
WAIT_REMOVAL_S = 300
SETTLE_S = 5.0
SILENT = 3e-5
ACTIVE, DISABLED, NOTPRESENT = 1, 2, 4


def child(role: str) -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO,
                        format="LOG %(relativeCreated)d %(message)s")
    if role == "control":
        Cues._watch = lambda self: None
    cues = Cues(enabled=True, volume=SILENT)
    print("PID", os.getpid(), flush=True)
    threading.Thread(target=pr.blip, args=(cues, threading.Event()), daemon=True).start()
    while True:
        s = cues._stream
        print("STAT", cues._calls, s is not None and s.active, flush=True)
        time.sleep(0.5)


class Child:
    def __init__(self, role: str) -> None:
        self.role = role
        self.proc = subprocess.Popen([sys.executable, __file__, "--child", role],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace")
        self.logs: list[str] = []
        for line in self.proc.stdout:          # Cues logs "cues ready" before the PID
            if line.startswith("PID "):
                self.pid = int(line.split()[1])
                break
            self.logs.append(line.strip())
        self.calls, self.active, self.advancing = 0, False, True
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            parts = line.split()
            if parts and parts[0] == "STAT":
                calls = int(parts[1])
                self.advancing = calls != self.calls
                self.calls, self.active = calls, parts[2] == "True"
            elif parts and parts[0] == "LOG":
                self.logs.append(line.strip())

    def state(self) -> str:
        return f"calls {'moving' if self.advancing else 'STUCK'}, active={self.active}"


def all_endpoints() -> dict[str, tuple[str, int]]:
    """{endpoint id: (friendly name, state)} for every endpoint in any state."""
    out = {}
    for flow in (pr.RENDER, pr.CAPTURE):
        en = pr._create(pr.CLSID_MMDeviceEnumerator, pr.IID_IMMDeviceEnumerator)
        coll = c_void_p()
        try:
            pr._call(en, 3, (c_uint, c_ulong, POINTER(c_void_p)), flow, 0xF, byref(coll))
            n = c_uint()
            pr._call(coll, 3, (POINTER(c_uint),), byref(n))
            for i in range(n.value):
                dev = c_void_p()
                pr._call(coll, 4, (c_uint, POINTER(c_void_p)), i, byref(dev))
                state = c_ulong()
                pr._call(dev, 6, (POINTER(c_ulong),), byref(state))   # GetState
                try:
                    name = pr._friendly_name(dev)
                except OSError:
                    name = "?"
                out[pr._device_id(dev)] = (name, state.value)
                pr._release(dev)
        finally:
            pr._release(coll)
            pr._release(en)
    return out


def set_visible(dev_id: str, visible: bool) -> None:
    pc = pr._create(pr.CLSID_PolicyConfigClient, pr.IID_IPolicyConfig)
    try:
        pr._call(pc, 14, (ctypes.c_wchar_p, ctypes.c_int), dev_id, int(visible))
    finally:
        pr._release(pc)


def defaults() -> tuple[str, str]:
    names = {k: v[0] for k, v in all_endpoints().items()}
    out = []
    for flow in (pr.RENDER, pr.CAPTURE):
        try:
            out.append(names.get(pr.default_endpoint(flow, pr.MULTIMEDIA), "?"))
        except OSError:
            out.append("(none)")
    return out[0], out[1]


def heard(child: Child) -> set[str]:
    names = pr.endpoints(pr.RENDER)
    return {names.get(k, k) for k in pr.heard(child.pid)}


T0 = time.monotonic()
last_line = [""]


def trace(subject: Child, control: Child, note: str = "") -> tuple[set, set, tuple]:
    d = defaults()
    s, c = heard(subject), heard(control)
    line = (f"out={d[0]!r} in={d[1]!r} | SUBJECT heard {sorted(s) or 'NOWHERE'} "
            f"({subject.state()}) | CONTROL heard {sorted(c) or 'NOWHERE'} "
            f"({control.state()})")
    if line != last_line[0] or note:
        print(f"{time.monotonic() - T0:6.1f}s {note:9} {line}", flush=True)
        last_line[0] = line
    return s, c, d


rows: list[tuple[str, str, str]] = []


def row(verdict: str, name: str, detail: str = "") -> None:
    rows.append((verdict, name, detail))


def wait_for(pred, timeout: float, subject: Child, control: Child):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = pred()
        if got:
            return got
        trace(subject, control)
    return None


def main() -> int:
    subject, control = Child("subject"), Child("control")
    try:
        time.sleep(1.5)
        before = all_endpoints()
        base_defaults = defaults()
        s, c, _ = trace(subject, control, "baseline")
        if not s or not c:
            for ch in (subject, control):
                print(f"  {ch.role} pid {ch.pid} sessions:", pr.sessions(ch.pid, pr.RENDER))
            print("RESULT INCONCLUSIVE: a child is not heard at baseline")
            return 2
        print(">>> plug the device in, wait ~5s, unplug it, wait ~5s, plug it in again",
              flush=True)

        def arrived():
            now = all_endpoints()
            return {k: v[0] for k, v in now.items()
                    if v[1] == ACTIVE and before.get(k, ("", 0))[1] != ACTIVE} or None

        new = wait_for(arrived, WAIT_ARRIVAL_S, subject, control)
        if not new:
            print("RESULT SKIPPED: nothing was plugged in")
            return 2
        trace(subject, control, "ARRIVED")
        print("          arrived:", sorted(new.values()), flush=True)
        time.sleep(SETTLE_S)
        _, _, d = trace(subject, control, "present")
        row("INFO", "the device took the defaults",
            f"out {base_defaults[0]!r} -> {d[0]!r}, in {base_defaults[1]!r} -> {d[1]!r}")

        def left():
            now = all_endpoints()
            return all(now.get(k, ("", 0))[1] != ACTIVE for k in new) or None

        if not wait_for(left, WAIT_REMOVAL_S, subject, control):
            print("RESULT SKIPPED: the device was never unplugged")
            return 2
        trace(subject, control, "LEFT")
        time.sleep(SETTLE_S)
        s, c, d = trace(subject, control, "after")
        s |= heard(subject)
        c |= heard(control)
        reproduced = not c
        row("PASS" if reproduced else "INCONCLUSIVE",
            "CONTROL (no watcher) went silent: the bug reproduced",
            f"control heard {sorted(c) or 'nowhere'}, {control.state()}")
        row("PASS" if d[0] in s else "FAIL",
            "SUBJECT is heard on the default output after the unplug",
            f"default {d[0]!r}, subject heard {sorted(s) or 'nowhere'}")
        reopens = [l for l in subject.logs if "reopening" in l or "cues ready" in l]
        row("INFO", "SUBJECT reopens", " / ".join(reopens) or "none")
        row("INFO", "CONTROL log", " / ".join(control.logs) or "nothing")

        # -- phase 2: disable the device's endpoints, then the replug ----------
        for dev_id in new:
            try:
                set_visible(dev_id, False)
            except OSError as e:
                row("FAIL", f"disable {new[dev_id]!r}", str(e))
        restored = defaults()

        def rearrived():
            now = all_endpoints()
            return all(now.get(k, ("", NOTPRESENT))[1] != NOTPRESENT for k in new) or None

        if not wait_for(rearrived, WAIT_REMOVAL_S, subject, control):
            row("SKIP", "replug with the device's endpoints disabled", "never plugged back in")
        else:
            trace(subject, control, "REPLUG")
            time.sleep(SETTLE_S)
            s, _, d = trace(subject, control, "replugged")
            now = all_endpoints()
            states = {new[k]: now.get(k, ("", 0))[1] for k in new}
            row("PASS" if all(v == DISABLED for v in states.values()) else "FAIL",
                "the device's endpoints stay disabled across the replug",
                f"states {states} (2 = disabled)")
            row("PASS" if d == restored else "FAIL",
                "the replug leaves the default output and input alone",
                f"before {restored}, after {d}")
            row("PASS" if d[0] in s else "FAIL",
                "SUBJECT is still heard on the default output", f"heard {sorted(s)}")
    finally:
        for ch in (subject, control):
            ch.proc.kill()

    print()
    for verdict, name, detail in rows:
        print(f"  {verdict:12} {name}" + (f"  [{detail}]" if detail else ""))
    verdicts = {v for v, _, _ in rows}
    if "FAIL" in verdicts:
        result = "FAIL"
    elif "INCONCLUSIVE" in verdicts:
        result = "INCONCLUSIVE"
    else:
        result = "PASS"
    print(f"RESULT {result}")
    return 0 if result == "PASS" else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        child(sys.argv[2])
    else:
        sys.exit(main())
