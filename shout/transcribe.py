"""faster-whisper wrapper.

Import order in this module is load-bearing: cuda_dlls.enable() MUST run before
faster_whisper (and therefore ctranslate2) is imported, or CUDA fails with
'cublas64_12.dll is not found'. See CLAUDE.md.
"""
from __future__ import annotations

import logging
import os
import re
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


# Whisper was trained on a great deal of subtitled video, so a chunk of audio it
# cannot resolve into words decodes to the boilerplate those subtitle files end
# with. It arrives after the last real word: a breath, a lip smack or a chair
# creak clears the VAD, reaches the decoder carrying no lexical content, and
# comes back as an outro. Measured 20 Sep 2026: trailing SILENCE is not the
# trigger, because vad_filter strips it before the decoder sees it (62 cases,
# synthetic and real noise floor, zero hallucinations) - it takes a real sound.
#
# A missing real word is invisible corruption; a surviving junk phrase is
# visible and one keystroke to delete. That asymmetry sets the inclusion rule:
# a phrase earns a place here only if it names watching, listening, a channel
# or a video, or is a bracketed sound tag or an attribution line. Those cannot
# be confused with plain prose. Generic calls to action are NOT here, however
# often Whisper invents them -- "stay tuned", "please subscribe" and "see you
# next time" are all real marketing copy someone dictates on purpose. So are
# "Thank you", "Bye" and "You", which are the most common hallucinations of
# all. If a new one shows up, shout.log's per-segment numbers are the evidence
# to add it from. Written without literal typographic characters or backslash
# escapes, per the lab notes.
_APOS = "['" + chr(0x2019) + "]"           # straight or curly
_SENT_END = ".!?" + chr(0x2026)            # . ! ? ellipsis

_BOILERPLATE = (
    r"thanks?(?: you)?(?: all)?(?: so much)? for watching",
    r"thanks?(?: you)?(?: all)?(?: so much)? for listening",
    r"we" + _APOS + r"?ll be right back",
    r"we will be right back",
    r"like and subscribe",
    r"subscribe to (?:my|our|the) channel",
    r"i" + _APOS + r"?ll see you (?:in|on) the next (?:one|video)",
    r"see you (?:in|on) the next (?:one|video)",
    r"subtitles?(?: and captions?)? (?:by|created by|provided by).*",
    r"transcri(?:ption|bed|ption provided) by.*",
    r"amara\.org.*",
    r"\[\s*(?:music|applause|laughter|silence|blank[ _]audio)\s*\]",
    r"\(\s*(?:music|applause|laughter)\s*\)",
)

# A whole segment that is nothing but boilerplate, ignoring edge punctuation.
_WHOLE = tuple(re.compile(p, re.IGNORECASE) for p in _BOILERPLATE)
# The same phrase fused onto the end of real text. Anchored to a sentence
# boundary so it can never cut into a sentence the user actually dictated.
_FUSED = tuple(
    re.compile("(?:(?<=[" + re.escape(_SENT_END) + r"])|\A)\s*" + p + r"[^\w]*\Z",
               re.IGNORECASE)
    for p in _BOILERPLATE
)
_TRIM = re.compile(r"^[^\w\[\(]+|[^\w\]\)]+$")


def _whole_match(text: str) -> int | None:
    core = _TRIM.sub("", " ".join(text.split()))
    if not core:
        return None
    for i, rx in enumerate(_WHOLE):
        if rx.fullmatch(core):
            return i
    return None


def assemble(segments) -> tuple[str, list[int]]:
    """Join the segment texts, dropping invented subtitle boilerplate.

    Returns the text and the indices of the _BOILERPLATE patterns that fired.
    An index is all the caller may log: it cannot leak a dictation even if this
    misfires on something real, which keeps README's promise that shout.log
    never holds your words.
    """
    kept = list(segments)
    dropped: list[int] = []
    while kept:                            # trailing junk can repeat
        idx = _whole_match(kept[-1].text)
        if idx is None:
            break
        dropped.append(idx)
        kept.pop()
    text = "".join(s.text for s in kept).strip()
    while True:                            # and can be fused onto real text
        for i, rx in enumerate(_FUSED):
            m = rx.search(text)
            if m:
                text = text[: m.start()].rstrip()
                dropped.append(i)
                break
        else:
            break
    return text, dropped


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
        segs = list(segments)
        text, dropped = assemble(segs)
        # Numbers only, never the words. These are what a confidence-based
        # filter would have to be tuned on, so they are recorded now rather
        # than guessed at later; a hallucinated tail should stand out on
        # no_speech_prob or avg_logprob.
        if segs:
            log.info("segments %s", " | ".join(
                f"{s.start:.2f}-{s.end:.2f} nsp={s.no_speech_prob:.2f} "
                f"lp={s.avg_logprob:+.2f} cr={s.compression_ratio:.2f} "
                f"n={len(s.text.strip())}"
                for s in segs))
        for i in dropped:
            log.info("dropped trailing boilerplate, pattern #%d", i)
        log.info("transcribed %.1fs of audio in %.2fs -> %d chars",
                 len(audio) / SAMPLE_RATE, time.perf_counter() - t0, len(text))
        return text
