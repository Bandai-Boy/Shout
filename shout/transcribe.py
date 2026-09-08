"""faster-whisper wrapper.

Import order in this module is load-bearing: cuda_dlls.enable() MUST run before
faster_whisper (and therefore ctranslate2) is imported, or CUDA fails with
'cublas64_12.dll is not found'. See CLAUDE.md.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_OFFLINE", "1")  # unauthenticated Hub calls hang on backoff

from . import cuda_dlls

_CUDA_DIRS = cuda_dlls.enable()

from faster_whisper import WhisperModel  # noqa: E402  (must follow enable())

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SAMPLE_RATE = 16000


def load_wav(path) -> np.ndarray:
    """16-bit PCM WAV -> float32 mono. Only used for the reference clip."""
    import wave

    with wave.open(str(path), "rb") as w:
        frames = w.readframes(w.getnframes())
        channels = w.getnchannels()
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio


class Transcriber:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.model: WhisperModel | None = None
        self.ready = False
        log.info("cuda dll dirs: %s", _CUDA_DIRS)

    def load(self) -> float:
        t0 = time.perf_counter()
        self.model = WhisperModel(
            self.cfg.model,
            device=self.cfg.device,
            compute_type=self.cfg.compute_type,
            local_files_only=True,
        )
        dt = time.perf_counter() - t0
        log.info("model loaded in %.2fs (%s, %s)", dt, self.cfg.model, self.cfg.compute_type)
        return dt

    def warmup(self) -> float:
        """The first GPU op in a process costs ~20s while CUDA builds kernels. Pay
        it here, in the background, instead of on the first thing the user dictates.

        Deliberately goes through transcribe() rather than calling the model
        directly: the VAD filter is a separate ONNX session with its own first-call
        cost, so warming a different code path than production leaves that cost
        sitting on the user's first dictation — which is the exact thing this is
        supposed to prevent.

        Uses the JFK reference clip when present, so warmup doubles as a
        correctness check against a known-good transcript.
        """
        jfk = ROOT / "audio" / "jfk.wav"
        if jfk.exists():
            audio = load_wav(jfk)
        else:
            rng = np.random.default_rng(0)
            audio = (rng.standard_normal(SAMPLE_RATE) * 0.01).astype(np.float32)

        t0 = time.perf_counter()
        text = self.transcribe(audio)
        dt = time.perf_counter() - t0
        self.ready = True
        log.info("warmup %.2fs -> %r", dt, text[:60])
        return dt

    def transcribe(self, audio: np.ndarray) -> str:
        if self.model is None:
            raise RuntimeError("transcribe() before load()")
        t0 = time.perf_counter()
        segments, _ = self.model.transcribe(
            audio,
            beam_size=self.cfg.beam_size,
            language=self.cfg.language,
            vad_filter=True,
            # One bad window otherwise poisons every window after it.
            condition_on_previous_text=False,
        )
        text = "".join(s.text for s in segments).strip()
        log.info("transcribed %.1fs of audio in %.2fs -> %d chars",
                 len(audio) / SAMPLE_RATE, time.perf_counter() - t0, len(text))
        return text
