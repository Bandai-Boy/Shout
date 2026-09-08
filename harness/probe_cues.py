"""Gate: the cue tones are well formed, instant, and do not land in the transcript.

Two halves with very different characters.

The waveform half is deterministic and always binds: shape, envelope, pitch
direction, and that `play()` returns immediately rather than blocking the chord.

The bleed half is physical. With `preroll_ms = 0` the mic opens on chord-down, so
a cue played at that instant can be picked up through the speakers and end up
transcribed. It is measured with the app's real ordering — cue first, then open
the device — because that ordering turns out not to help much.

Energy in the capture is the wrong thing to assert on. Measured 8 Sep: the cue
DOES reach the microphone (~130x ambient) because `recorder.start()` costs only
14-22ms on this machine, not the 50-200ms that playing the cue first was supposed
to hide it behind. What matters is whether that bleed changes the transcript, and
it does not — a pure tone is rejected by the VAD. So the energy figures are
printed as diagnostics and the assertions are made at transcript level: the bleed
alone must produce no text, and bleed followed by speech must transcribe
identically to that speech alone.

Those assertions are only meaningful if the speaker-to-microphone path is live,
which it is not on headphones or with the speakers muted — and with no bleed in
the recording they would both pass trivially. So a loud reference tone is played
as a POSITIVE CONTROL first, and the verdict distinguishes three outcomes:
control unheard (SKIPPED, never a pass), control heard but no bleed (clean), and
control heard with bleed present (assert it is inert).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shout.audio import Recorder
from shout.cues import Cues, build

RATE = 16000
BAND = (550.0, 1450.0)      # spans the 660 / 990 / 1320 notes
CONTROL_MIN_RATIO = 3.0     # below this the speakers are not reaching the mic
BLEED_PRESENT_RATIO = 3.0   # above this the cue is genuinely in the recording

rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def band_energy(audio: np.ndarray, rate: int = RATE) -> float:
    if len(audio) < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    freqs = np.fft.rfftfreq(len(audio), 1.0 / rate)
    sel = (freqs >= BAND[0]) & (freqs <= BAND[1])
    return float(np.sum(spec[sel] ** 2) / max(1, sel.sum()))


def dominant(audio: np.ndarray, rate: int) -> float:
    spec = np.abs(np.fft.rfft(audio * np.hanning(len(audio))))
    return float(np.fft.rfftfreq(len(audio), 1.0 / rate)[int(np.argmax(spec))])


def waveform_checks() -> None:
    rate, vol = 48000, 0.25
    for name in ("start", "stop", "latch"):
        a = build(name, rate, vol)
        ms = len(a) / rate * 1000
        check(60 <= ms <= 260, f"{name}: duration is a blip", f"{ms:.0f}ms")
        check(abs(a[0]) < 1e-6 and abs(a[-1]) < 1e-6,
              f"{name}: starts and ends at zero (no click)",
              f"first={a[0]:+.2e} last={a[-1]:+.2e}")
        check(np.max(np.abs(a)) <= vol + 1e-6, f"{name}: respects the volume setting",
              f"peak={np.max(np.abs(a)):.3f}")
        # A raw sine jumps to full amplitude in one sample; a faded one must not.
        head = int(rate * 0.002)
        check(np.max(np.abs(a[:head])) < vol * 0.7,
              f"{name}: fades in rather than jumping to full amplitude")

    lo, hi = 0.05, 0.045
    s = build("start", rate, vol)
    st = build("stop", rate, vol)
    la = build("latch", rate, vol)
    first, last = int(rate * lo), int(rate * hi)
    check(dominant(s[:first], rate) < dominant(s[-last:], rate),
          "start rises in pitch",
          f"{dominant(s[:first], rate):.0f}Hz -> {dominant(s[-last:], rate):.0f}Hz")
    check(dominant(st[:first], rate) > dominant(st[-last:], rate),
          "stop falls in pitch (the reverse of start)",
          f"{dominant(st[:first], rate):.0f}Hz -> {dominant(st[-last:], rate):.0f}Hz")
    check(dominant(la[-last:], rate) > dominant(s[-last:], rate),
          "latch ends higher than start, so the three are distinguishable",
          f"latch {dominant(la[-last:], rate):.0f}Hz vs start {dominant(s[-last:], rate):.0f}Hz")
    check(len(la) > len(s), "latch is longer than start")


def bleed_test(cues: Cues) -> str:
    """Returns 'clean', 'inert', 'fail', or a reason string for SKIPPED."""
    if not cues.enabled or cues._stream is None:
        return "no audio output device"

    rec = Recorder(preroll_ms=0)

    def capture(play: str | None, before: bool = False,
                settle: float = 0.18, tail: float = 0.55):
        if before:                      # the app's ordering: cue, then open the mic
            cues.play(play)
            rec.start()
            time.sleep(tail)
            return rec.stop()
        rec.start()
        time.sleep(settle)              # let the device actually come up
        if play:
            cues.play(play)
        time.sleep(tail)
        return rec.stop()

    ambient = capture(None)
    time.sleep(0.3)
    cue = capture("start", before=True)          # exactly what a chord press does
    time.sleep(0.3)
    cues.samples["_ref"] = build("start", cues.rate, min(0.85, cues._volume * 3 + 0.4))
    reference = capture("_ref")
    rec.close()

    for label, a in (("ambient", ambient), ("cue", cue), ("reference", reference)):
        if len(a) < RATE // 4:
            return f"capture too short ({label}={len(a)} samples) — no microphone?"

    e_amb = band_energy(ambient) + 1e-12
    ctrl_ratio = band_energy(reference) / e_amb
    cue_ratio = band_energy(cue) / e_amb

    print(f"    ambient band energy   {e_amb:.3e}")
    print(f"    cue     band energy   {band_energy(cue):.3e}   ({cue_ratio:8.1f}x ambient)")
    print(f"    control band energy   {band_energy(reference):.3e}   ({ctrl_ratio:8.1f}x ambient)")

    if ctrl_ratio < CONTROL_MIN_RATIO:
        return (f"the loud control tone was not heard by the mic "
                f"({ctrl_ratio:.2f}x ambient, need {CONTROL_MIN_RATIO}x) — "
                f"headphones, muted speakers, or no loopback path")

    if cue_ratio < BLEED_PRESENT_RATIO:
        check(True, "no cue energy reaches the microphone",
              f"{cue_ratio:.1f}x ambient, control heard at {ctrl_ratio:.0f}x")
        return "clean"

    # The cue IS in the recording. Assert the thing that actually matters.
    from shout.config import Config
    from shout.transcribe import Transcriber, load_wav

    tr = Transcriber(Config())
    tr.load()
    tr.warmup()
    speech = load_wav(Path(__file__).resolve().parent.parent / "audio" / "jfk.wav")

    alone = tr.transcribe(cue.astype(np.float32))
    baseline = tr.transcribe(speech.astype(np.float32))
    mixed = tr.transcribe(np.concatenate([cue, speech]).astype(np.float32))

    ok_alone = check(alone == "", "the bleed alone transcribes to nothing",
                     f"got {alone[:40]!r}")
    ok_mixed = check(mixed == baseline,
                     "bleed followed by speech transcribes identically to speech alone",
                     f"{'identical' if mixed == baseline else repr(mixed[:60])}")
    check(baseline != "", "the speech reference itself transcribed (control)",
          f"{baseline[:40]!r}")
    print(f"    cue is audible to the mic at {cue_ratio:.0f}x ambient and is inert"
          if ok_alone and ok_mixed else "    cue bleed CHANGED the transcript")
    return "inert" if (ok_alone and ok_mixed) else "fail"


def main() -> int:
    waveform_checks()

    cues = Cues(enabled=True, volume=0.25)
    check(cues.enabled, "cue output stream opened", f"{cues.rate}Hz")
    for name in ("start", "stop", "latch"):
        check(name in cues.samples, f"{name}: preloaded, not read from disk on the hot path")

    t0 = time.perf_counter()
    cues.play("start")
    dt = (time.perf_counter() - t0) * 1000
    check(dt < 2.0, "play() returns immediately (the cue IS the feedback)",
          f"{dt:.3f}ms")
    time.sleep(0.4)

    print("  bleed test (playing three short tones):")
    verdict = bleed_test(cues)
    cues.close()

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))

    passed = sum(ok for ok, _, _ in rows)
    hard_fail = passed != len(rows)
    if verdict not in ("clean", "inert", "fail"):
        print(f"  [SKIPPED] microphone bleed - {verdict}")
        print(f"{'FAIL' if hard_fail else 'PASS'} {passed}/{len(rows)} cue assertions; "
              f"BLEED NOT MEASURED (see reason above)")
        return 1 if hard_fail else 0
    tail = {"clean": "no bleed reaches the mic",
            "inert": "bleed reaches the mic and does not change the transcript",
            "fail": "BLEED CHANGES THE TRANSCRIPT"}[verdict]
    print(f"{'FAIL' if hard_fail else 'PASS'} {passed}/{len(rows)} cue assertions, "
          f"speaker path confirmed live - {tail}")
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
