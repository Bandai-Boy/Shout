"""Audio cues — the only confirmation that recording actually started.

Three tones, synthesized rather than sampled: a rising blip on start, its reverse
on stop, and a three-note climb for latch. Nothing is read from disk on the hot
path; the arrays are built once at construction.

Two decisions worth keeping:

*Persistent output stream.* The cue IS the feedback, so it has to fire on
chord-down with no perceptible delay. `sd.play()` opens a device per call, which
costs tens of milliseconds and is audible as a lag. A stream held open with a
callback that mixes in whichever cue is pending costs one idle callback per block
and fires immediately.

*Played before the microphone opens — which is not the real defence.* With
`preroll_ms = 0` the mic opens on chord-down, so a cue played at the same instant
can be captured through the speakers. `__main__` still plays the cue first, but
measured 8 Sep 2026 that buys almost nothing: `recorder.start()` costs 14-22ms on
this machine, not the 50-200ms it was assumed to cost, so most of a 112ms cue is
still playing when the mic goes live and it lands in the capture at ~130x ambient.

What makes that harmless is the transcript, not the ordering: a pure tone is
rejected by the VAD, so the bleed alone transcribes to nothing and bleed-then-
speech transcribes identically to the same speech alone. `harness/probe_cues.py`
asserts exactly that, against a loud reference tone proving the speaker-to-mic
path is live. If the tones are ever changed, re-run it — the property being
relied on is "not speech-like", which a longer or more complex cue could break.

Every tone ends with a raised-cosine fade. A sine that starts at full amplitude
clicks audibly.
"""
from __future__ import annotations

import logging
import threading

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

FALLBACK_RATE = 48000
FADE_S = 0.005          # 5ms; shorter than this and the edge clicks
GAP_S = 0.012           # silence between notes, so a blip reads as two notes

# name -> (frequency Hz, duration s) per note
_TONES: dict[str, tuple[tuple[float, float], ...]] = {
    "start": ((660.0, 0.045), (990.0, 0.055)),
    "stop": ((990.0, 0.045), (660.0, 0.055)),
    "latch": ((660.0, 0.040), (990.0, 0.040), (1320.0, 0.060)),
}


def _note(freq: float, seconds: float, rate: int) -> np.ndarray:
    n = max(1, int(rate * seconds))
    t = np.arange(n, dtype=np.float32) / rate
    wave = np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    fade = max(1, min(int(rate * FADE_S), n // 2))
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32)))
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    return wave


def build(name: str, rate: int, volume: float) -> np.ndarray:
    gap = np.zeros(int(rate * GAP_S), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i, (freq, seconds) in enumerate(_TONES[name]):
        if i:
            parts.append(gap)
        parts.append(_note(freq, seconds, rate))
    return (np.concatenate(parts) * volume).astype(np.float32)


class Cues:
    """Non-blocking cue playback. Silently inert if no output device opens —
    a machine with no speakers must still dictate."""

    def __init__(self, enabled: bool = True, volume: float = 0.25,
                 device=None) -> None:
        self.enabled = enabled
        self._volume = float(volume)
        self.rate = FALLBACK_RATE
        self.device = device
        self._stream: sd.OutputStream | None = None
        self._channels = 1
        # (samples, position) as one tuple so the callback reads a consistent
        # pair without a lock. Rebinding one attribute is atomic under the GIL;
        # a lock here would be held on PortAudio's thread, which is worth avoiding.
        self._cursor: tuple[np.ndarray | None, int] = (None, 0)
        self.samples: dict[str, np.ndarray] = {}
        self._lock = threading.Lock()
        if enabled:
            self._open()

    # -- device ------------------------------------------------------------

    def _open(self) -> None:
        try:
            info = sd.query_devices(self.device, "output")
            self.rate = int(info["default_samplerate"]) or FALLBACK_RATE
        except Exception:
            log.debug("no output device info; assuming %dHz", FALLBACK_RATE,
                      exc_info=True)
        self._render()
        for channels in (1, 2):
            try:
                self._stream = sd.OutputStream(
                    samplerate=self.rate, channels=channels, dtype="float32",
                    device=self.device, callback=self._callback, latency="low",
                )
                self._stream.start()
                self._channels = channels
                log.info("cues ready at %dHz, %d channel(s)", self.rate, channels)
                return
            except Exception:
                self._stream = None
        log.warning("no audio output for cues; running silent", exc_info=True)
        self.enabled = False

    def _render(self) -> None:
        self.samples = {name: build(name, self.rate, self._volume)
                        for name in _TONES}

    # -- playback ----------------------------------------------------------

    def _callback(self, outdata, frames, time_info, status) -> None:
        samples, pos = self._cursor
        if samples is None or pos >= len(samples):
            outdata[:] = 0.0
            return
        chunk = samples[pos:pos + frames]
        outdata[:len(chunk)] = chunk.reshape(-1, 1) if self._channels == 1 \
            else np.repeat(chunk.reshape(-1, 1), self._channels, axis=1)
        if len(chunk) < frames:
            outdata[len(chunk):] = 0.0
        self._cursor = (samples, pos + frames)

    def play(self, name: str) -> None:
        """Start a cue. Returns immediately; a cue already playing is replaced."""
        if not self.enabled or self._stream is None:
            return
        samples = self.samples.get(name)
        if samples is None:
            return
        self._cursor = (samples, 0)

    def duration_s(self, name: str) -> float:
        samples = self.samples.get(name)
        return len(samples) / self.rate if samples is not None else 0.0

    def close(self) -> None:
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("closing the cue stream", exc_info=True)
