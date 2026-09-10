"""Microphone capture -> float32 mono 16kHz numpy array.

Two capture modes, selected by cfg.preroll_ms:

    0   open the device on chord-down, close on release. The mic-in-use indicator
        only appears while actually dictating. Costs 50-200ms of device-start
        latency, so the first syllable can clip. This is the session 1 default.

    >0  keep the stream open with a rolling buffer of this length, so the audio
        from just before the chord press is already in hand. No clipping, but the
        mic indicator is on the whole time Shout runs.

The mode is a config flag rather than an architectural assumption on purpose:
switching should be a setting, not a rewrite.

Either way, unless cfg.input_device names one, the device is the MME Sound
Mapper, so both modes follow the Windows default mic as it changes. On
PortAudio's None the per-chord mode followed only by an accident of Windows
device numbering, and the held stream did not follow at all; see shout.devices.
"""
from __future__ import annotations

import collections
import logging
import threading

import numpy as np
import sounddevice as sd
import soxr

from .devices import follow_default

log = logging.getLogger(__name__)
TARGET_RATE = 16000


class AudioError(RuntimeError):
    pass


class Recorder:
    def __init__(self, device=None, preroll_ms: int = 0) -> None:
        self.device = device
        # Resolved once: PortAudio's device list is fixed for the life of the
        # process, and the Mapper's index with it.
        self._device = device if device is not None else follow_default("input")
        self.preroll_ms = preroll_ms
        self._lock = threading.Lock()
        self._stream: sd.InputStream | None = None
        self._rate = TARGET_RATE
        self._capturing = False
        self._chunks: list[np.ndarray] = []
        self._ring: collections.deque[np.ndarray] = collections.deque()
        self._ring_len = 0
        self._xruns = 0
        self.level = 0.0   # RMS of the last block; read by the overlay

    # -- device ------------------------------------------------------------

    def _pick_rate(self) -> int:
        """WASAPI shared mode usually hands back the device mix format (44.1k/48k)
        rather than the 16k we want. Try 16k, fall back to the device default and
        resample ourselves rather than letting an unknown driver path do it."""
        try:
            sd.check_input_settings(device=self._device, samplerate=TARGET_RATE,
                                    channels=1, dtype="float32")
            return TARGET_RATE
        except Exception:
            info = sd.query_devices(self._device, "input")
            rate = int(info["default_samplerate"])
            log.info("device rejects 16kHz; capturing at %dHz and resampling", rate)
            return rate

    def _callback(self, indata, frames, time_info, status) -> None:
        if status:
            self._xruns += 1
        buf = indata.copy()
        if self._capturing:
            self._chunks.append(buf)
            self.level = float(np.sqrt(np.mean(np.square(buf))))
        elif self._preroll_samples:
            self._ring.append(buf)
            self._ring_len += len(buf)
            while self._ring and self._ring_len - len(self._ring[0]) >= self._preroll_samples:
                self._ring_len -= len(self._ring.popleft())

    @property
    def _preroll_samples(self) -> int:
        return int(self._rate * self.preroll_ms / 1000)

    @property
    def capture_rate(self) -> int:
        """The rate the device is actually open at. 16000 means the audio engine
        is converting for us; anything else means soxr is doing it."""
        return self._rate

    def _open(self) -> None:
        if self._stream is not None:
            return
        self._rate = self._pick_rate()
        self._stream = sd.InputStream(
            samplerate=self._rate, channels=1, dtype="float32",
            device=self._device, callback=self._callback,
        )
        self._stream.start()
        log.info("mic open at %dHz on %r", self._rate,
                 sd.query_devices(self._device, "input")["name"])

    def _close(self) -> None:
        if self._stream is None:
            return
        self._stream.stop()
        self._stream.close()
        self._stream = None
        self._ring.clear()
        self._ring_len = 0

    # -- lifecycle ---------------------------------------------------------

    def arm(self) -> None:
        """Open the persistent stream. Only meaningful when preroll is enabled."""
        if self.preroll_ms > 0:
            with self._lock:
                self._open()

    def start(self) -> None:
        with self._lock:
            self._open()
            self._chunks = list(self._ring) if self.preroll_ms > 0 else []
            self._xruns = 0
            self._capturing = True

    def stop(self) -> np.ndarray:
        """Returns mono float32 at 16kHz. Empty array if nothing was captured."""
        with self._lock:
            self._capturing = False
            self.level = 0.0
            chunks, self._chunks = self._chunks, []
            rate = self._rate
            if self.preroll_ms == 0:
                self._close()
            xruns = self._xruns

        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(chunks, axis=0).reshape(-1).astype(np.float32)
        if rate != TARGET_RATE:
            audio = soxr.resample(audio, rate, TARGET_RATE).astype(np.float32)
        if xruns:
            log.warning("%d audio callback overruns during capture", xruns)
        return audio

    def close(self) -> None:
        with self._lock:
            self._capturing = False
            self.level = 0.0
            self._close()
