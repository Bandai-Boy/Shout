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
from shout.config import Config
from shout.cues import GESTURES, MAX_GESTURE_S, Cues, Voice, build, duration_s, notes

RATE = 16000
CONTROL_MIN_RATIO = 3.0     # below this the speakers are not reaching the mic
BLEED_PRESENT_RATIO = 3.0   # above this the cue is genuinely in the recording

# The gate tests the voice that is actually CONFIGURED, not the default. The
# property it exists to protect — that the cue is inert in the transcript — is a
# property of the sound, so a gate pinned to the default would go on passing
# while the app played something the VAD might not reject.
CFG = Config.load()
VOICE = Voice.resolve(CFG.cue_preset, CFG.cue_voice)


def band(voice: Voice = VOICE) -> tuple[float, float]:
    """The band the cue's fundamentals live in, derived from the voice rather
    than hardcoded — a deeper preset would fall out of a fixed window and the
    bleed measurement would silently read as ambient."""
    freqs = [f for name in GESTURES for f, _ in notes(name, voice)]
    return min(freqs) * 0.7, max(freqs) * 1.4


BAND = band()


def note_slices(name: str, rate: int, voice: Voice = VOICE):
    """(nominal Hz, start, end) per note. Measuring each note where it actually
    is beats slicing a fixed number of milliseconds off each end, which a change
    of note length silently invalidates."""
    out, pos = [], 0
    gap = int(rate * voice.gap_ms / 1000.0)
    for i, (freq, seconds) in enumerate(notes(name, voice)):
        if i:
            pos += gap
        n = max(1, int(rate * seconds))
        out.append((freq, pos, pos + n))
        pos += n
    return out

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
    print(f"  voice {VOICE.name!r}  root={VOICE.root_hz:.0f}Hz  decay={VOICE.decay}"
          f"  bright={VOICE.bright}  length={VOICE.length}"
          f"  band={BAND[0]:.0f}-{BAND[1]:.0f}Hz")
    for name in ("start", "stop", "latch"):
        a = build(name, rate, vol, VOICE)
        ms = len(a) / rate * 1000
        check(ms <= MAX_GESTURE_S * 1000, f"{name}: duration is still a blip",
              f"{ms:.0f}ms, ceiling {MAX_GESTURE_S * 1000:.0f}ms")
        check(abs(ms - duration_s(name, VOICE) * 1000) < 1.0,
              f"{name}: every note the voice describes is present",
              f"{ms:.0f}ms built vs {duration_s(name, VOICE) * 1000:.0f}ms described")
        check(abs(a[0]) < 1e-6 and abs(a[-1]) < 1e-6,
              f"{name}: starts and ends at zero (no click)",
              f"first={a[0]:+.2e} last={a[-1]:+.2e}")
        check(np.max(np.abs(a)) <= vol + 1e-6, f"{name}: respects the volume setting",
              f"peak={np.max(np.abs(a)):.3f}")
        # Each note is measured where the voice says it is, so a wrong root,
        # a dropped note or a mis-scaled interval all show up as a pitch miss.
        for freq, lo_i, hi_i in note_slices(name, rate):
            got = dominant(a[lo_i:hi_i], rate)
            tol = max(30.0, freq * 0.08)
            check(abs(got - freq) <= tol, f"{name}: note at {freq:.0f}Hz is in tune",
                  f"measured {got:.0f}Hz (tol +-{tol:.0f})")
        # A raw sine jumps to full amplitude in one sample; a faded one must not.
        head = int(rate * 0.002)
        check(np.max(np.abs(a[:head])) < vol * 0.7,
              f"{name}: fades in rather than jumping to full amplitude")

    # Direction, measured note by note rather than by slicing a fixed number of
    # milliseconds off each end — a voice with longer notes or a decay envelope
    # makes a fixed tail slice land in near-silence and read as noise.
    def measured(name: str) -> list[float]:
        a = build(name, rate, vol, VOICE)
        return [dominant(a[lo_i:hi_i], rate) for _, lo_i, hi_i in note_slices(name, rate)]

    s_n, st_n, la_n = measured("start"), measured("stop"), measured("latch")
    check(s_n[0] < s_n[-1], "start rises in pitch",
          f"{s_n[0]:.0f}Hz -> {s_n[-1]:.0f}Hz")
    check(st_n[0] > st_n[-1], "stop falls in pitch (the reverse of start)",
          f"{st_n[0]:.0f}Hz -> {st_n[-1]:.0f}Hz")
    check(la_n[-1] > s_n[-1],
          "latch ends higher than start, so the three are distinguishable",
          f"latch {la_n[-1]:.0f}Hz vs start {s_n[-1]:.0f}Hz")
    check(len(build("latch", rate, vol, VOICE)) > len(build("start", rate, vol, VOICE)),
          "latch is longer than start")

    # Anchored on the voice's declared root and the gesture's declared ratio,
    # NOT on notes() — the synth builds from notes() too, so a check that reads
    # its expectation from there agrees with a broken notes() forever. Measured
    # 8 Sep: detuning notes() by 20% slipped past every per-note check above and
    # is caught by exactly these two rows.
    check(abs(s_n[0] - VOICE.root_hz) <= max(30.0, VOICE.root_hz * 0.08),
          "start's first note IS the voice's root",
          f"measured {s_n[0]:.0f}Hz, voice says {VOICE.root_hz:.0f}Hz")
    want_ratio = GESTURES["start"][1][0] ** VOICE.spread
    got_ratio = s_n[-1] / max(1e-6, s_n[0])
    check(abs(got_ratio - want_ratio) <= 0.08,
          "the interval is the one the gesture declares",
          f"measured {got_ratio:.3f}x, gesture says {want_ratio:.3f}x")


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
    cues.samples["_ref"] = build("start", cues.rate,
                                 min(0.85, cues._volume * 3 + 0.4), cues.voice)
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

    cues = Cues(enabled=True, volume=CFG.cue_volume, voice=VOICE)
    check(cues.enabled, "cue output stream opened",
          f"{cues.rate}Hz, voice {cues.voice.name!r} at volume {CFG.cue_volume}")
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
