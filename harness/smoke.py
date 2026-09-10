"""Component smoke test. Every case prints an explicit ok/FAIL row.

Exercises the pieces that need real Windows and real hardware: capture path and
sample-rate handling, clipboard round-trip with the history-exclusion formats,
the modifier wait, and model load + warmup timing.

Does NOT need the user to speak — it checks plumbing, shapes and timings, and
transcribes the known JFK reference clip for correctness.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The console here is cp1252; harness output is not.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import sounddevice as sd

from shout.audio import TARGET_RATE, Recorder
from shout.config import Config
from shout import inject as inj
from shout.winapi import foreground_is_elevated

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


def main() -> int:
    cfg = Config()

    # --- capture path -------------------------------------------------
    rec = Recorder(device=cfg.input_device, preroll_ms=cfg.preroll_ms)
    # The device the Recorder actually opens, not PortAudio's resolution of None.
    info = sd.query_devices(rec._device, "input")
    native = int(info["default_samplerate"])
    t0 = time.perf_counter()
    rec.start()
    open_ms = (time.perf_counter() - t0) * 1000
    opened_at = rec.capture_rate
    time.sleep(1.5)
    audio = rec.stop()
    rec.close()

    how = ("audio engine converts" if opened_at == TARGET_RATE
           else f"soxr resamples {opened_at}Hz -> {TARGET_RATE}Hz")
    check(True, "capture path resolved",
          f"{info['name']} default {native}Hz, opened at {opened_at}Hz ({how})")

    check(audio.dtype == np.float32, "capture returns float32", str(audio.dtype))
    check(audio.ndim == 1, "capture returns mono 1-D", f"shape={audio.shape}")
    dur = len(audio) / TARGET_RATE
    check(1.2 < dur < 1.9, "capture length is right after resampling",
          f"{dur:.2f}s at {TARGET_RATE}Hz from {native}Hz")
    check(np.isfinite(audio).all(), "no NaN or Inf in captured audio")
    check(open_ms < 500, "device open latency", f"{open_ms:.0f}ms (why preroll exists)")

    # --- clipboard ----------------------------------------------------
    original = inj.get_clipboard_text()
    probe = "shout-smoke-éà中文-" + str(time.time())
    check(inj.set_clipboard_text(probe), "clipboard set succeeded")
    check(inj.get_clipboard_text() == probe, "clipboard round-trips unicode exactly")

    fmt = inj.user32.RegisterClipboardFormatW(inj._EXCLUDE)
    check(fmt != 0, "history-exclusion format registered", f"id={fmt}")
    if inj.user32.OpenClipboard(None):
        try:
            present = bool(inj.user32.GetClipboardData(fmt))
        finally:
            inj.user32.CloseClipboard()
    else:
        present = False
    check(present, "transcript is marked private to clipboard history")

    # Negative control: a non-private write must NOT carry the exclusion marker,
    # proving the check above is reading a real signal and not always true.
    inj.set_clipboard_text("plain", private=False)
    if inj.user32.OpenClipboard(None):
        try:
            leaked = bool(inj.user32.GetClipboardData(fmt))
        finally:
            inj.user32.CloseClipboard()
    else:
        leaked = True
    check(not leaked, "control: a plain write is NOT marked private")

    if original is not None:
        inj.set_clipboard_text(original, private=False)
        check(inj.get_clipboard_text() == original, "previous clipboard restored")
    else:
        check(True, "previous clipboard was empty/non-text", "nothing to restore")

    # --- modifier wait ------------------------------------------------
    t0 = time.perf_counter()
    cleared = inj.wait_for_modifiers_clear(0.5)
    check(cleared, "modifier wait returns immediately when nothing is held",
          f"{(time.perf_counter() - t0) * 1000:.0f}ms")
    check(foreground_is_elevated() is False, "elevation probe runs and says not elevated")

    # --- model --------------------------------------------------------
    t0 = time.perf_counter()
    from shout.transcribe import Transcriber
    tr = Transcriber(cfg)
    load_s = tr.load()
    warm_s = tr.warmup()
    ready_s = time.perf_counter() - t0
    check(ready_s < 20, "tray-ready budget: model load + warmup", f"{ready_s:.2f}s (load {load_s:.2f}s, warmup {warm_s:.2f}s)")

    from shout.transcribe import load_wav
    jfk = load_wav(ROOT / "audio" / "jfk.wav")
    jfk_s = len(jfk) / TARGET_RATE

    # One call is not a latency measurement — take a median, and report the first
    # call separately so a cold VAD session cannot hide inside the average.
    times = []
    for _ in range(5):
        t0 = time.perf_counter()
        text = tr.transcribe(jfk)
        times.append(time.perf_counter() - t0)
    first, med = times[0], sorted(times)[len(times) // 2]

    expected = ("And so, my fellow Americans, ask not what your country can do for you, "
                "ask what you can do for your country.")
    check(text == expected, "warm transcribe of the JFK reference is exact", repr(text[:70]))
    check(med < 0.6, "warm transcribe median (production path, VAD on)",
          f"{med * 1000:.0f}ms for {jfk_s:.1f}s audio ({jfk_s / med:.1f}x realtime)")
    check(first < med * 1.5, "first post-warmup call is not an outlier",
          f"first {first * 1000:.0f}ms vs median {med * 1000:.0f}ms "
          f"— warmup covered the VAD path")

    # A 10s utterance is the criterion-2 case; scale the measured rate to it.
    projected = 10.0 / (jfk_s / med) * 1000
    check(projected < 700, "projected transcribe time for a 10s utterance",
          f"{projected:.0f}ms, leaving {800 - projected:.0f}ms of the 800ms budget "
          f"for injection")

    # --- report -------------------------------------------------------
    failed = 0
    print()
    for ok, name, detail in RESULTS:
        failed += not ok
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}" + (f"  —  {detail}" if detail else ""))
    print(f"\nSMOKE {len(RESULTS) - failed}/{len(RESULTS)} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
