"""Audio cues — the only confirmation that recording actually started.

Three gestures, synthesized rather than sampled: a rising blip on start, its
reverse on stop, and a three-note climb for latch. Nothing is read from disk on
the hot path; the arrays are built once at construction.

A gesture is *shape only* — a sequence of frequency RATIOS and durations. What
those ratios sound like is a `Voice`: root pitch, how wide the intervals are, how
long the notes ring, how fast they decay, and how much upper harmonic they carry.
So "start" is always the same rising two-note move, and changing the voice
changes the whole set consistently rather than one tone at a time.

The default voice reproduces the original tones exactly — 660/990Hz, flat
sustain, pure sine — so nothing changes until a voice is chosen. Audition them
with `scripts/cue_lab.py`, which drives THIS module rather than its own copy of
the synth, and writes the result to config.json.

Three decisions worth keeping:

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
path is live. **Re-run it after changing the voice.** The property being relied
on is "not speech-like", and it is a property of the SOUND, not of the code — a
long, complex or noisy cue can break it, and the failure is a stray word in your
dictation rather than an error. Every preset here is harmonic for that reason.

*Every note ends with a raised-cosine fade.* A sine that starts at full amplitude
clicks audibly, and the click is the part that reads as cheap.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

FALLBACK_RATE = 48000
FADE_S = 0.005          # 5ms; shorter than this and the edge clicks
MAX_GESTURE_S = 0.40    # a cue longer than this is not a blip any more

# Gesture -> (frequency ratio against the root, duration in ms at length 1.0).
# Ratios, not frequencies: the voice's root transposes all three together and
# they keep their relationship to each other.
GESTURES: dict[str, tuple[tuple[float, float], ...]] = {
    "start": ((1.0, 45.0), (1.5, 55.0)),
    "stop": ((1.5, 45.0), (1.0, 55.0)),
    "latch": ((1.0, 40.0), (1.5, 40.0), (2.0, 60.0)),
}


@dataclass(frozen=True)
class Voice:
    """How the gestures sound. Defaults are the original tones, unchanged."""

    name: str = "blip"
    root_hz: float = 660.0
    spread: float = 1.0     # exponent on the ratios; <1 narrows the intervals
    length: float = 1.0     # multiplier on every note duration
    gap_ms: float = 12.0    # silence between notes, so a blip reads as two notes
    decay: float = 0.0      # 0 = flat sustain (a beep); 1 = struck and ringing out
    bright: float = 0.0     # how much of `partials` is mixed in
    gain: float = 1.0       # per-voice trim, so presets match each other in level
    partials: tuple[tuple[float, float], ...] = ((2.0, 0.50), (3.0, 0.25))

    @classmethod
    def resolve(cls, preset: str = "blip", overrides: dict | None = None) -> "Voice":
        """A named preset with optional per-field tweaks on top — which is what
        the lab exports and what config.json stores."""
        voice = PRESETS.get(preset, PRESETS["blip"])
        if overrides:
            known = {f for f in cls.__dataclass_fields__ if f != "name"}
            clean = {k: v for k, v in overrides.items() if k in known}
            if "partials" in clean:
                clean["partials"] = tuple(tuple(p) for p in clean["partials"])
            if clean:
                voice = replace(voice, **clean)
        return voice


# Materials, not melodies: every preset plays the same three gestures. All are
# harmonic — no noise — because probe_cues relies on the VAD rejecting them.
PRESETS: dict[str, Voice] = {
    "blip": Voice(),
    "soft": Voice("soft", root_hz=440.0, length=1.4, decay=0.45, bright=0.0),
    "wood": Voice("wood", root_hz=392.0, length=1.8, decay=0.80, bright=0.30,
                  gain=0.95, partials=((2.0, 0.40), (3.0, 0.30), (4.2, 0.18))),
    "marimba": Voice("marimba", root_hz=330.0, length=2.2, decay=0.65, bright=0.35,
                     gain=0.95, partials=((4.0, 0.55), (9.2, 0.10))),
    "drop": Voice("drop", root_hz=262.0, spread=0.7, length=2.4, decay=0.85,
                  bright=0.0),
    "glass": Voice("glass", root_hz=880.0, length=1.6, decay=0.50, bright=0.45,
                   gain=0.9, partials=((2.0, 0.45), (5.4, 0.22))),
}

DEFAULT_VOICE = PRESETS["blip"]


def notes(name: str, voice: Voice = DEFAULT_VOICE) -> list[tuple[float, float]]:
    """The gesture as concrete (frequency Hz, seconds). Exposed because the gate
    needs to know where each note is to measure it, rather than guessing at
    offsets that a length change would silently invalidate."""
    return [(voice.root_hz * ratio ** voice.spread, ms * voice.length / 1000.0)
            for ratio, ms in GESTURES[name]]


def duration_s(name: str, voice: Voice = DEFAULT_VOICE) -> float:
    n = notes(name, voice)
    return sum(s for _, s in n) + (len(n) - 1) * voice.gap_ms / 1000.0


def _note(freq: float, seconds: float, rate: int, voice: Voice) -> np.ndarray:
    n = max(1, int(rate * seconds))
    t = np.arange(n, dtype=np.float32) / rate
    wave = np.sin(2.0 * np.pi * freq * t).astype(np.float32)
    if voice.bright > 0.0:
        for mult, gain in voice.partials:
            # Nyquist is a real limit at 330Hz roots with a 9th partial.
            if freq * mult < rate * 0.45:
                wave += (voice.bright * gain
                         * np.sin(2.0 * np.pi * freq * mult * t).astype(np.float32))
        peak = float(np.max(np.abs(wave))) or 1.0
        wave /= peak
    if voice.decay > 0.0:
        # Struck rather than held. At decay = 1 the note is down to e^-8 of its
        # attack by the end of its slot, which is what reads as wood or mallet
        # instead of as a beep.
        wave *= np.exp(-t / (seconds / (8.0 * voice.decay))).astype(np.float32)
    fade = max(1, min(int(rate * FADE_S), n // 2))
    ramp = (0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, fade, dtype=np.float32)))
    wave[:fade] *= ramp
    wave[-fade:] *= ramp[::-1]
    return wave


def build(name: str, rate: int, volume: float,
          voice: Voice = DEFAULT_VOICE) -> np.ndarray:
    gap = np.zeros(int(rate * voice.gap_ms / 1000.0), dtype=np.float32)
    parts: list[np.ndarray] = []
    for i, (freq, seconds) in enumerate(notes(name, voice)):
        if i:
            parts.append(gap)
        parts.append(_note(freq, seconds, rate, voice))
    return (np.concatenate(parts) * volume * voice.gain).astype(np.float32)


class Cues:
    """Non-blocking cue playback. Silently inert if no output device opens —
    a machine with no speakers must still dictate."""

    def __init__(self, enabled: bool = True, volume: float = 0.25,
                 device=None, voice: Voice | None = None) -> None:
        self.enabled = enabled
        self._volume = float(volume)
        self.voice = voice or DEFAULT_VOICE
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
                log.info("cues ready at %dHz, %d channel(s), voice %r",
                         self.rate, channels, self.voice.name)
                return
            except Exception:
                self._stream = None
        log.warning("no audio output for cues; running silent", exc_info=True)
        self.enabled = False

    def _render(self) -> None:
        self.samples = {name: build(name, self.rate, self._volume, self.voice)
                        for name in GESTURES}

    def set_voice(self, voice: Voice) -> None:
        """Re-synthesize in place. Used by the lab, which is auditioning; the app
        builds once and never calls this."""
        self.voice = voice
        self._cursor = (None, 0)
        self._render()

    def set_volume(self, volume: float) -> None:
        self._volume = float(volume)
        self._cursor = (None, 0)
        self._render()

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
