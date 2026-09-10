"""Gate: the cues and the mic follow the Windows default devices, live.

Switching Windows from speakers to headphones has to move the cues with it, in
the app and in the cue lab alike, and switching the default mic has to move what
Shout records. A stream held open on PortAudio's `device=None` never moves; see
shout.devices.

The measurement is Windows' own. Every process has an audio session on each
endpoint it has used. A session lingers on every endpoint the process has EVER
used, so presence proves nothing. For output, only a non-zero peak says where the
sound is going right now. For input, a quiet room reads a peak of 0 whether or not
the mic is open, so the signal is the session being ACTIVE instead.

Procedure: open the real `Cues` and a held-open `Recorder` and find them on the
current defaults; switch the defaults (console + multimedia roles) to another
active endpoint of each kind and find them there; open a per-chord `Recorder` (the
default mode) and find it there too; restore every role in a `finally` and find
them back. The switch is real: for about three seconds everything else playing or
recording moves with it.

POSITIVE CONTROL, run concurrently in a child process: a `Cues` and a held-open
`Recorder` pinned to the indexes PortAudio resolves None to -- the pre-fix
streams, exactly. They must STAY on the original endpoints through the switch. If
either follows, this harness cannot see the bug it exists to catch, and the
verdict is INCONCLUSIVE rather than a pass.

Not every row discriminates. Swapping the pre-fix behaviour in gave FAIL 14/18:
the two device rows, the cue follow and the held-mic follow flip, but the
per-chord mic row passes on the old code too. Windows renumbers devices so that a
stream OPENED after a switch lands on the new default either way. It stays as a
regression row for the mode Shout actually runs in; for the mic, it is the held
stream that tells old code from new.

A kind with only one active endpoint is SKIPPED, never a pass: there is nothing
to switch to.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from ctypes import HRESULT, POINTER, byref, c_float, c_uint, c_ulong, c_void_p
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sounddevice as sd

from shout.audio import Recorder
from shout.cues import Cues

QUIET = 0.004        # ~-48dBFS: plainly non-zero on a session meter, barely audible
SETTLE_S = 1.5       # Windows takes a moment to re-route after the default changes
BLIP_EVERY_S = 0.12  # replayed faster than a cue lasts, so the meter never idles

# --- Core Audio over raw ctypes ---------------------------------------------

ole32 = ctypes.WinDLL("ole32")
ole32.CoInitializeEx(None, 0)   # RPC_E_CHANGED_MODE is fine: COM is up either way


class GUID(ctypes.Structure):
    _fields_ = [("d1", c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort),
                ("d4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> GUID:
    g = GUID()
    if ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(g)) != 0:
        raise ValueError(text)
    return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort),
                ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("ptr", c_void_p), ("extra", c_void_p)]


CLSID_MMDeviceEnumerator = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IAudioSessionManager2 = _guid("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
IID_IAudioSessionControl2 = _guid("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
IID_IAudioMeterInformation = _guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")
# IPolicyConfig is undocumented but stable since Windows 7; it is what the Sound
# settings page itself calls, and what EarTrumpet and SoundSwitch use.
CLSID_PolicyConfigClient = _guid("{870AF99C-171D-4F9E-AF0D-E63DF40C2BC9}")
IID_IPolicyConfig = _guid("{F8679F50-850A-41CF-9C72-430F290290C8}")
PKEY_FriendlyName = PROPERTYKEY(_guid("{A45C254E-DF1C-4EFD-8020-67D146A850E0}"), 14)
CLSCTX_ALL = 23
RENDER, CAPTURE = 0, 1
KIND = {RENDER: "output", CAPTURE: "input"}
CONSOLE, MULTIMEDIA, COMMUNICATIONS = 0, 1, 2
ROLES = (CONSOLE, MULTIMEDIA, COMMUNICATIONS)


def _call(obj, index, argtypes=(), *args, restype=HRESULT):
    """Vtable slot `index` on a COM pointer. HRESULT restype raises on failure."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)(vtbl[index])(obj, *args)


def _release(obj) -> None:
    if obj:
        _call(obj, 2, restype=c_ulong)


def _qi(obj, iid: GUID) -> c_void_p:
    out = c_void_p()
    _call(obj, 0, (POINTER(GUID), POINTER(c_void_p)), byref(iid), byref(out))
    return out


def _create(clsid: GUID, iid: GUID) -> c_void_p:
    out = c_void_p()
    hr = ole32.CoCreateInstance(byref(clsid), None, CLSCTX_ALL, byref(iid), byref(out))
    if hr != 0:
        raise OSError(f"CoCreateInstance failed: 0x{hr & 0xFFFFFFFF:08X}")
    return out


def _device_id(dev) -> str:
    p = c_void_p()
    _call(dev, 5, (POINTER(c_void_p),), byref(p))
    try:
        return ctypes.wstring_at(p.value)
    finally:
        ole32.CoTaskMemFree(p)


def _friendly_name(dev) -> str:
    store = c_void_p()
    _call(dev, 4, (c_ulong, POINTER(c_void_p)), 0, byref(store))
    pv = PROPVARIANT()
    try:
        _call(store, 5, (POINTER(PROPERTYKEY), POINTER(PROPVARIANT)),
              byref(PKEY_FriendlyName), byref(pv))
        return ctypes.wstring_at(pv.ptr) if pv.vt == 31 else "?"   # VT_LPWSTR
    finally:
        ole32.PropVariantClear(byref(pv))
        _release(store)


def _active_devices(flow: int):
    """Yields (endpoint id, IMMDevice); the caller releases each device."""
    en = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    coll = c_void_p()
    try:
        _call(en, 3, (c_uint, c_ulong, POINTER(c_void_p)), flow, 1, byref(coll))
        n = c_uint()
        _call(coll, 3, (POINTER(c_uint),), byref(n))
        for i in range(n.value):
            dev = c_void_p()
            _call(coll, 4, (c_uint, POINTER(c_void_p)), i, byref(dev))
            yield _device_id(dev), dev
    finally:
        _release(coll)
        _release(en)


def endpoints(flow: int) -> dict[str, str]:
    """{endpoint id: friendly name} for every active endpoint of `flow`."""
    out = {}
    for dev_id, dev in _active_devices(flow):
        out[dev_id] = _friendly_name(dev)
        _release(dev)
    return out


def default_endpoint(flow: int, role: int) -> str:
    en = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator)
    dev = c_void_p()
    try:
        _call(en, 4, (c_uint, c_uint, POINTER(c_void_p)), flow, role, byref(dev))
        return _device_id(dev)
    finally:
        _release(dev)
        _release(en)


def set_default_endpoint(device_id: str, role: int) -> None:
    """The id carries its own direction, so this serves input and output alike."""
    pc = _create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
    try:
        _call(pc, 13, (ctypes.c_wchar_p, c_uint), device_id, role)   # SetDefaultEndpoint
    finally:
        _release(pc)


def sessions(pid: int, flow: int) -> dict[str, tuple[bool, float]]:
    """{endpoint id: (this pid's session is active, its peak right now)}, for
    endpoints of `flow` where the pid has a session at all."""
    out: dict[str, tuple[bool, float]] = {}
    for dev_id, dev in _active_devices(flow):
        mgr, enum = c_void_p(), c_void_p()
        try:
            _call(dev, 3, (POINTER(GUID), c_ulong, c_void_p, POINTER(c_void_p)),
                  byref(IID_IAudioSessionManager2), CLSCTX_ALL, None, byref(mgr))
            _call(mgr, 5, (POINTER(c_void_p),), byref(enum))
            count = ctypes.c_int()
            _call(enum, 3, (POINTER(ctypes.c_int),), byref(count))
            for i in range(count.value):
                ctl = c_void_p()
                _call(enum, 4, (ctypes.c_int, POINTER(c_void_p)), i, byref(ctl))
                ctl2 = _qi(ctl, IID_IAudioSessionControl2)
                owner = c_ulong()
                _call(ctl2, 14, (POINTER(c_ulong),), byref(owner))    # GetProcessId
                if owner.value == pid:
                    state = ctypes.c_int()
                    _call(ctl, 3, (POINTER(ctypes.c_int),), byref(state))  # GetState
                    meter = _qi(ctl, IID_IAudioMeterInformation)
                    peak = c_float()
                    _call(meter, 3, (POINTER(c_float),), byref(peak))
                    _release(meter)
                    was_active, was_peak = out.get(dev_id, (False, 0.0))
                    out[dev_id] = (was_active or state.value == 1,
                                   max(was_peak, peak.value))
                _release(ctl2)
                _release(ctl)
        finally:
            _release(enum)
            _release(mgr)
            _release(dev)
    return out


# --- the probe ---------------------------------------------------------------

def blip(cues: Cues, stop: threading.Event) -> None:
    while not stop.is_set():
        cues.play("start")
        time.sleep(BLIP_EVERY_S)


def heard(pid: int) -> dict[str, float]:
    """Output endpoints carrying this pid's sound: {id: loudest peak} over ~0.5s."""
    seen: dict[str, float] = {}
    for _ in range(10):
        for dev_id, (_, peak) in sessions(pid, RENDER).items():
            seen[dev_id] = max(seen.get(dev_id, 0.0), peak)
        time.sleep(0.05)
    return {k: v for k, v in seen.items() if v > 0.0}


def recording(pid: int) -> set[str]:
    """Input endpoints where this pid has an ACTIVE session."""
    return {k for k, (active, _) in sessions(pid, CAPTURE).items() if active}


def control_child() -> None:
    """The pre-fix streams: Cues and a held Recorder pinned to the indexes
    PortAudio resolves None to. Reports its own pid, because the venv's
    python.exe is uv's trampoline and the parent's Popen.pid is the shim."""
    pinned_out = sd.query_devices(kind="output")["index"]
    pinned_in = sd.query_devices(kind="input")["index"]
    cues = Cues(enabled=True, volume=QUIET, device=pinned_out)
    mic = Recorder(device=pinned_in, preroll_ms=200)
    mic.arm()
    print(os.getpid(), flush=True)
    stop = threading.Event()
    threading.Thread(target=blip, args=(cues, stop), daemon=True).start()
    sys.stdin.read()        # until the parent closes the pipe, or dies
    stop.set()
    mic.close()
    cues.close()


rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def main() -> int:
    names = {flow: endpoints(flow) for flow in KIND}
    orig = {flow: {role: default_endpoint(flow, role) for role in ROLES}
            for flow in KIND}
    home = {flow: orig[flow][CONSOLE] for flow in KIND}
    away: dict[int, str] = {}
    skipped: list[str] = []
    for flow, kind in KIND.items():
        others = [i for i in names[flow] if i != home[flow]]
        if others:
            away[flow] = others[0]
            print(f"  {kind}: {names[flow][home[flow]]!r} -> "
                  f"{names[flow][away[flow]]!r} -> back")
        else:
            skipped.append(f"{kind} (only {names[flow].get(home[flow], '?')!r} is active)")
    if not away:
        print(f"SKIPPED - nothing to switch to: {'; '.join(skipped)}")
        return 0

    def expect(flow: int, pid: int, want: set[str], name: str) -> bool:
        found = heard(pid) if flow == RENDER else recording(pid)
        if flow == RENDER:
            detail = ", ".join(f"{names[flow].get(k, k)} @ {v:.4f}"
                               for k, v in found.items()) or "silent everywhere"
        else:
            detail = ", ".join(names[flow].get(k, k) for k in found) \
                or "not recording anywhere"
        return check(set(found) == want, name, detail)

    # Anchored on PortAudio's own resolution of None, not on shout.devices:
    # an expectation read from the code under test moves with its bugs.
    pinned_out = sd.query_devices(kind="output")["index"]
    pinned_in = sd.query_devices(kind="input")["index"]
    cues = Cues(enabled=True, volume=QUIET)
    held = Recorder(preroll_ms=200)
    chord = Recorder(preroll_ms=0)
    opened = cues._stream.device if cues._stream is not None else None
    check(opened is not None and opened != pinned_out,
          f"the cue stream is not on the device PortAudio pins None to ({pinned_out})",
          f"device {opened}: "
          f"{sd.query_devices(opened)['name'] if opened is not None else 'none'}")
    check(chord._device is not None and chord._device != pinned_in,
          f"the mic is not the device PortAudio pins None to ({pinned_in})",
          f"device {chord._device}: "
          f"{sd.query_devices(chord._device)['name'] if chord._device is not None else 'none'}")

    child = subprocess.Popen([sys.executable, __file__, "--control"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    child_pid = int(child.stdout.readline().split()[0])
    stop = threading.Event()
    threading.Thread(target=blip, args=(cues, stop), daemon=True).start()
    if CAPTURE in away:
        held.arm()
    me = os.getpid()
    time.sleep(0.5)
    control_ok = True
    try:
        if RENDER in away:
            expect(RENDER, me, {home[RENDER]},
                   "cues: heard on the current default output, and only there")
            control_ok &= expect(RENDER, child_pid, {home[RENDER]},
                                 "control: the pinned cue stream is heard there too")
        if CAPTURE in away:
            expect(CAPTURE, me, {home[CAPTURE]},
                   "mic (held open): recording from the current default input only")
            control_ok &= expect(CAPTURE, child_pid, {home[CAPTURE]},
                                 "control: the pinned mic is recording there too")
        for flow in away:
            for role in (CONSOLE, MULTIMEDIA):
                set_default_endpoint(away[flow], role)
        time.sleep(SETTLE_S)
        for flow in away:
            current = default_endpoint(flow, CONSOLE)
            check(current == away[flow], f"the default {KIND[flow]} actually changed",
                  names[flow].get(current, "?"))
        if RENDER in away:
            expect(RENDER, me, {away[RENDER]},
                   "cues: follow to the new default output")
            control_ok &= expect(RENDER, child_pid, {home[RENDER]},
                                 "control: the pinned (pre-fix) cue stream stayed behind")
        if CAPTURE in away:
            expect(CAPTURE, me, {away[CAPTURE]},
                   "mic (held open): follows to the new default input, live")
            control_ok &= expect(CAPTURE, child_pid, {home[CAPTURE]},
                                 "control: the pinned (pre-fix) mic stayed behind")
            held.close()
            time.sleep(0.3)
            # Makes the next row attributable to the per-chord recorder alone.
            expect(CAPTURE, me, set(),
                   "mic: nothing of ours records once the held stream closes")
            chord.start()
            time.sleep(0.3)
            expect(CAPTURE, me, {away[CAPTURE]},
                   "mic (per chord, the default mode): opens on the new default input")
            chord.stop()
    finally:
        for flow in away:
            for role in ROLES:
                set_default_endpoint(orig[flow][role], role)
    time.sleep(SETTLE_S)
    for flow in away:
        now = {role: default_endpoint(flow, role) for role in ROLES}
        check(now == orig[flow], f"every default {KIND[flow]} role is restored exactly",
              ", ".join(names[flow].get(now[r], "?") for r in ROLES))
    if RENDER in away:
        expect(RENDER, me, {home[RENDER]}, "cues: follow back after restoring")
    if CAPTURE in away:
        chord.start()
        time.sleep(0.3)
        expect(CAPTURE, me, {home[CAPTURE]},
               "mic (per chord): opens on the restored default input")
        chord.stop()

    stop.set()
    cues.close()
    held.close()
    chord.close()
    child.stdin.close()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    for reason in skipped:
        print(f"  [SKIPPED] {reason} - nothing to switch to")
    passed = sum(ok for ok, _, _ in rows)
    what = " and ".join(w for w, flow in (("cues", RENDER), ("mic", CAPTURE))
                        if flow in away)
    tail = f"; NOT MEASURED: {'; '.join(skipped)}" if skipped else ""
    if not control_ok:
        print(f"INCONCLUSIVE {passed}/{len(rows)} - a pinned control did not stay "
              f"behind, so this harness cannot see the bug it exists to catch")
        return 1
    if passed != len(rows):
        print(f"FAIL {passed}/{len(rows)} routing assertions - {what} do NOT follow "
              f"the Windows default (rows above say where they stayed){tail}")
        return 1
    print(f"PASS {passed}/{len(rows)} routing assertions - {what} follow the Windows "
          f"default there and back; the pinned controls stayed put{tail}")
    return 0


if __name__ == "__main__":
    if "--control" in sys.argv:
        control_child()
    else:
        raise SystemExit(main())
