"""Which PortAudio device follows the Windows default, for the mic and the cues.

PortAudio's `device=None` means an MME device NUMBER, not a device: the waveOut
or waveIn ID that was the preferred device when the process started
(`pa_win_wmme.c`: DRVM_MAPPER_PREFERRED_GET at init). Windows renumbers those IDs
whenever the default changes so that 0 is always the current default, while
PortAudio keeps the name it cached at init. So a stream OPENED after a switch
lands on the new default under the old name, and a stream already OPEN never
moves. The cues hold one output stream for the life of the process, which is how
they kept blipping on the speakers after a switch to headphones, and how the cue
lab, opened later, stayed on the headphones after a switch back. The per-chord
mic reopens every time, so it followed already, by that accident; the held
preroll mic did not.

The MME Sound Mapper follows by design. Windows resolves it to the current default
each time it is opened, and re-routes a stream already open on it the moment the
default changes, in both directions, for input and output. Measured 9 Sep 2026;
`harness/probe_route.py` asserts it with a real default switch.

The renumbering also means an explicit MME index is not a pin: after a switch,
the index PortAudio labels "headphones" can open the speakers.

PortAudio lists the Mapper as the first MME device of each direction. It is found
by position rather than by name, because the name is localized.

Following the default is not the whole story. On 16 Sep 2026 Shout's held cue
stream ended up playing nowhere (no session for the process on any endpoint) for
the rest of the process, and the cause was never found. Each of these left a stream playing: a
default switch, disabling the endpoint the stream is on, a PS4 controller's USB
audio arriving and leaving (`harness/probe_hotplug.py`), and a second stream
opened and closed in the same process. So `shout.cues` also watches: it reopens
when `default_output()` changes, or when the stream stops or stalls. Whether
that failure stopped the callbacks is unknown, so the watcher is not proven
against it.
"""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

import sounddevice as sd

log = logging.getLogger(__name__)

_winmm = ctypes.WinDLL("winmm")


class _WAVEOUTCAPSW(ctypes.Structure):
    _fields_ = [("wMid", wintypes.WORD), ("wPid", wintypes.WORD),
                ("vDriverVersion", wintypes.UINT), ("szPname", wintypes.WCHAR * 32),
                ("dwFormats", wintypes.DWORD), ("wChannels", wintypes.WORD),
                ("wReserved1", wintypes.WORD), ("dwSupport", wintypes.DWORD)]


def default_output() -> tuple[int, str]:
    """(output device count, current default's name), for noticing that the
    default output or the set of outputs changed. MME device 0 is always the
    current default, because Windows renumbers. The name is cut at 31
    characters. About half a millisecond; no COM."""
    caps = _WAVEOUTCAPSW()
    count = _winmm.waveOutGetNumDevs()
    if _winmm.waveOutGetDevCapsW(0, ctypes.byref(caps), ctypes.sizeof(caps)) != 0:
        return count, ""
    return count, caps.szPname


def follow_default(kind: str) -> int | None:
    """The Sound Mapper for `kind`, "input" or "output". None, meaning
    PortAudio's own default, only when there is no MME host API at all."""
    channels = f"max_{kind}_channels"
    try:
        for api in sd.query_hostapis():
            if api["name"] == "MME":
                for index in api["devices"]:
                    if sd.query_devices(index)[channels] > 0:
                        return index
    except Exception:
        log.debug("no MME host API; %s will not follow the default", kind,
                  exc_info=True)
    return None
