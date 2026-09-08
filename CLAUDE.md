# Shout

Local-only voice stack on this machine. Two tracks that share an inference environment:

1. **Dictation app** — hold Ctrl+Win, speak, text appears at the cursor. Being built now.
   See [PLAN.md](PLAN.md).
2. **Meeting transcription** — `scripts/transcribe_meeting.py`, for recordings that may
   contain PHI. Separate concern, already working.

Nothing leaves this machine. No API, no cloud, no BAA required. That constraint drove
every architectural decision and is not negotiable.

**Read [RESEARCH.html](RESEARCH.html) before changing any inference, hotkey, or model
decision.** It carries the measured benchmarks, the claims audit, the ruled-out options
with reasons, and the four hotkey traps. Claims in it are tagged Measured / Verified /
Refuted / Unverified — respect the tags; the Unverified ones are leads, not facts.

## Environment

- Python 3.12 venv at `.venv/`, created with `uv`.
- faster-whisper 1.2.1, CTranslate2 4.8.2, cuBLAS 12.9, cuDNN 9.25 — all pip wheels.
  No cmake, no MSVC, no CUDA Toolkit.
- Model `large-v3-turbo` cached in the HF hub cache. `models/` is empty and unused.
- Target: RTX 5070, 12GB, sm_120 (Blackwell) · Ryzen 7 9800X3D · Win 11.

## Commands

```bash
.venv/Scripts/python.exe scripts/bench.py                    # throughput benchmark
.venv/Scripts/python.exe scripts/gpu_probe.py                # CUDA + compute-type smoke test
.venv/Scripts/python.exe scripts/transcribe_meeting.py <file>  # meeting pipeline
```

## Gotchas

- **`cuda_dlls.enable()` must be called before importing `ctranslate2` or
  `faster_whisper`.** pip-installed CUDA DLLs land in `site-packages/nvidia/*/bin`, which
  is not on the Windows DLL search path. `os.add_dll_directory()` alone is *not* enough —
  CTranslate2 delay-loads cuBLAS through the standard search order, which reads `PATH`.
  Both are required. This is written down nowhere upstream.
- **Set `HF_HUB_OFFLINE=1` and `local_files_only=True`.** Unauthenticated Hub calls retry
  with long backoff on every model load, which presents as a hang, not an error.
- **The first GPU op in any process costs ~20s** while CUDA builds kernels — once per
  process, not per model. Any long-lived process must fire a throwaway warmup
  transcription at startup or the first real use reads as a broken app.
- **Never denoise before transcribing.** Speech enhancement measurably raises Whisper WER.
- **Always set `condition_on_previous_text=False`** on anything longer than a sentence.
  At its default of `True`, one bad window poisons every window after it.
- Benchmark timing: 11s of audio, warm median 0.36s, 30.8× realtime. Anything far off
  that is a regression, not variance.

## Gotcha: never run the gates while Shout is running

`harness/probe_hook.py` synthesizes a real Ctrl+Win chord with `SendInput`, and a
live Shout instance cannot tell that apart from a human — it will start recording,
transcribe whatever the microphone hears, and paste it into whatever window has
focus. Quit Shout from the tray before running `harness/gates.py`. The same applies
to `probe_stuck.py`, which physically holds the chord to fake a dead hook.

## Lab Notes

_Project-specific failure modes and refinements. Read this section before committing to
any iteration approach — if it conflicts with a HANDOFF, prefer the lab note and say so._

- [2026-09-08] The chord contains Win, so firing Ctrl+V while the user still physically
  holds it produces **Ctrl+Win+V** — Clipboard History opens, focus leaves the field,
  nothing pastes. Transcription returns in ~230ms, which is faster than a slow finger
  lift, so this is the normal timing on a short utterance rather than an edge case.
  `inject()` waits for `GetAsyncKeyState` to show all modifiers clear first. Never
  synthesize a Win key-up to force it — that strands the modifier down.
- [2026-09-08] Transcripts pass through the clipboard, so Clipboard History retains them
  and cloud clipboard can sync them off the machine — which would silently defeat the
  whole local-only premise. `set_clipboard_text(private=True)` sets
  `ExcludeClipboardContentFromMonitorProcessing` plus the two DWORD-0 formats. History
  is currently off on this box; do not rely on that.
- [2026-09-08] A dead low-level hook cannot report itself: `listener.running` stays True
  because the pynput message loop is alive, it just never gets called. The detector has
  to observe the same reality by a different route — the watchdog's 20Hz
  `GetAsyncKeyState` poll seeing the chord held while the gesture machine reports
  nothing. If hotkeys ever "just stop working", check `shout.log` for reinstall lines
  before suspecting pynput.
