# Shout — Session 1: The Spine

**Goal.** Hold Ctrl+Win, talk, release. Text appears at the cursor. Nothing else.

Everything in this plan is derived from `RESEARCH.html`. Where this plan contradicts
the research doc, this plan wins and the reason is stated inline.

---

## Definition of done

Session 1 is finished when all six of these are measured, not estimated:

| # | Criterion | How it is measured |
|---|---|---|
| 1 | Hold chord → speak → release → text at cursor | Automated injection probe + manual pass in 3 real apps |
| 2 | End-to-end latency ≤ 800ms for a 10s utterance | Timestamp chord-up → clipboard-set, logged per invocation |
| 3 | Hook callback p99 ≤ 5ms | Per-event duration histogram written to log, asserted by probe |
| 4 | Tray ready and model warm ≤ 20s from launch | Startup timing log |
| 5 | Cannot get stuck recording | Hard ceiling + async-poll probe, both forced in a test |
| 6 | Gesture state machine correct on 20+ synthetic sequences | `tests/test_gestures.py`, per-case rows, every case explicitly OK |

**Not done** = any of those unmeasured. A green run with a case that measured nothing
counts as a failure (lab note 2026-08-05).

### Measured, 8 Sep 2026

All five gates green: `.venv/Scripts/python.exe harness/gates.py` — 81 assertions,
14s wall clock. Sub-second per gate except the model load, so they run after every
edit rather than per batch.

| # | Criterion | Budget | Measured |
|---|---|---|---|
| 1 | Text arrives at the cursor | works | exact match in the probe target; **manual pass done 8 Sep: VS Code, Chrome, Discord all clean** |
| 2 | End-to-end for a 10s utterance | 800ms | ~280ms (209ms transcribe + 68ms inject) |
| 3 | Hook callback p99 | 5ms | 0.019ms — 50,000x under the ~1000ms unhook ceiling |
| 4 | Tray ready and model warm | 20s | 2.85s warm cache, 13.1s cold disk |
| 5 | Cannot get stuck recording | — | 15/15, all four stuck paths forced and recovered |
| 6 | Gesture machine | 20+ cases | 25/25 in 72ms |

Throughput came out at 47.9x realtime against the 30.8x in `RESEARCH.html` — the
benchmark ran with VAD off, and VAD trims the reference clip's silence before the
decoder sees it.

**Two defects the gates caught, both invisible to a green typecheck:**

- `warmup()` ran with `vad_filter=False` while production runs with it on, so the
  Silero VAD's own ONNX first-call cost landed on the user's first real dictation —
  the precise failure warmup exists to prevent. First production-path call was
  644ms; after routing warmup through `transcribe()` it is 223ms against a 230ms
  median.
- `Watchdog` subclasses `threading.Thread` and named its flag `self._stop`, which
  shadows `Thread._stop()`. `join()` raised `TypeError: 'Event' object is not
  callable`. Nothing would have surfaced this until a shutdown hung.

### Manual pass, 8 Sep 2026 — session 1 complete

All six criteria measured. Real-use latency from `shout.log`: 177ms (2.6s audio),
207ms (5.0s), 333ms (18.4s) — against an 800ms budget.

- **VS Code, Chrome, Discord** — dictation and injection clean in all three.
- **Clipboard restore** — verified by hand: copy, dictate, Ctrl+V returns the
  original copy, not the transcript.
- **Elevated-window blackout** — behaves exactly as designed: pressing the chord
  with Task Manager focused does nothing, and refocusing a normal window restores
  the hotkey immediately with no restart.
- **Vocabulary** — no hotword list needed yet. Pokémon names and brand terms
  (DocuHub, Wix) that iOS dictation garbles came back correct with no biasing.

**The blackout is narrower than the research implied.** Dictation requires a
focused editable field, and an elevated window being focused means there isn't
one — the two conditions are mutually exclusive, so the failure can only occur in
a state where dictation would be pointless. The genuine exception is an elevated
app that has a text field (admin terminal, installers), which is not this user's
workflow. Not worth engineering around.

Residual, accepted: a latch-ending tap made while an elevated window has focus is
not seen, so recording continues until the next tap in a normal window or the
10-minute ceiling. Errs toward keeping audio rather than losing it.

**Windows Terminal was not tested** — deliberately. Terminals get commands, not
prose. If it ever matters, per-app paste keys (Ctrl+Shift+V) are the known fix.



---

## Decisions locked before writing code

| Decision | Ruling | Why |
|---|---|---|
| Language / stack | Python 3.12, no framework | Toolchain already installed; talks to faster-whisper in-process, no IPC |
| Fork Handy? | No | Rust + Tauri + cmake = ~2 sessions of toolchain tax before feature work |
| Elevation | Non-elevated, permanently | Admin-for-life on a mic-capturing process with third-party deps is the worse trade |
| Model | `large-v3-turbo`, `float16` | `int8_float16` is within noise once warm; float16 is simpler |
| Decoding | `condition_on_previous_text=False`, `vad_filter=True`, no denoise | Repetition-cascade fix; enhancement raises WER |
| Hotkey mechanism | `pynput` `WH_KEYBOARD_LL`, observe-only, never suppress | `RegisterHotKey` cannot bind modifier-only; `keyboard` archived Feb 2026 |
| Injection | Clipboard + synthetic Ctrl+V via `SendInput` | UIA TextPattern is read-only; typing char-by-char is slow and drops under load |
| Clipboard API | Raw `ctypes` on user32/kernel32 | `pywin32` is a heavy dep for ~60 lines; `pyperclip` cannot restore reliably |
| Overlay / settings UI | Deferred to session 2 | Tray icon state is enough feedback for a spine |

---

## New traps found while planning

These are **not** in `RESEARCH.html`. They were found designing session 1 and are the
main reason this plan exists as a document rather than a paragraph.

### 1. Injecting Ctrl+V while Win is still physically held opens Clipboard History

The chord is Ctrl+**Win**. If the injector fires `SendInput(Ctrl+V)` while the user still
has the physical Win key down, Windows sees **Ctrl+Win+V**, and `Win+V` is the Clipboard
History shortcut. Result: a flyout opens, focus leaves the text field, nothing pastes.

This is not hypothetical — it is the normal timing on a short utterance. Release the chord
after two seconds of speech, transcription returns in ~0.36s, and a slow finger lift is
easily longer than that.

**Handling.** Before injecting, poll `GetAsyncKeyState` for `VK_LWIN`, `VK_RWIN`,
`VK_LCONTROL`, `VK_RCONTROL` and wait until all are clear, with a 2s timeout. On timeout,
skip the paste and leave text on the clipboard with a notification. Never synthesize a Win
key-up to force it — that strands the modifier (research doc, trap 1).

### 2. The transcript persists in Clipboard History and can sync to the cloud

Text goes through the clipboard, so with Clipboard History enabled every transcript is
retained, and with cloud clipboard on it leaves the machine — which defeats the entire
premise of the project.

Checked on this box: `HKCU\Software\Microsoft\Clipboard\EnableClipboardHistory` is unset,
so history is currently off. That is not something to rely on; a Windows update or a
stray Win+V acceptance flips it.

**Handling.** Register the `ExcludeClipboardContentFromMonitorProcessing` clipboard format
and set it in the same `OpenClipboard` session as the text. This is the documented opt-out
that excludes content from history and cloud sync. Cheap, and it is the difference between
"local-only" being true and being aspirational.

### 3. Opening the mic at chord-down clips the first syllable

`sounddevice.InputStream` open + PortAudio device start is 50–200ms. Start it on chord-down
and the first word is already partly gone before the first sample lands. This is the single
most common complaint about push-to-talk dictation tools, and it is invisible in testing
because you naturally pause before speaking when you are testing.

**Handling.** See open question A — this is the one decision that changes the architecture.

### 4. Device sample rate will usually not be 16kHz

WASAPI shared mode hands you the device mix format (typically 44.1k or 48k). Requesting
16000 either fails or gets silently resampled by the driver at unknown quality.
faster-whisper assumes a 16kHz float32 array when handed a numpy array.

**Handling.** Try to open at 16000; on `PortAudioError`, reopen at the device default and
resample with `soxr` (small pure-wheel dep, no build step). Log which path was taken —
if the driver path is ever silently taken, accuracy questions later have a suspect.

### 5. "Windows silently unhooked me" has no error to catch

The research doc says Windows permanently unhooks a callback that exceeds
`LowLevelHooksTimeout` with no exception and no log. It does not say how to *detect* it.
`listener.running` stays `True` — the pynput message loop is alive, it just never gets
called again.

**Handling.** A real liveness test, using something being built anyway: the
`GetAsyncKeyState` safety poll runs at 20Hz regardless. If the poll observes both modifiers
held for >300ms while the hook has reported no chord-down, the hook is dead → tear down and
reinstall the listener, log it, flash the tray icon. Also reinstall proactively if any
callback is ever measured over 50ms.

---

## Module layout

```
shout/
  __main__.py     entry: single-instance mutex, wiring, tray on main thread
  cuda_dlls.py    moved from scripts/ — must run before ctranslate2 import
  config.py       dataclass + JSON at %APPDATA%/Shout/config.json
  audio.py        InputStream, pre-roll ring buffer, resample, float32 16k out
  hotkey.py       pynput LL hook — timestamps into a Queue and returns, nothing else
  gestures.py     PURE state machine: events in → commands out. Zero I/O, zero Windows
  transcribe.py   WhisperModel wrapper, startup warmup, hotwords passthrough
  inject.py       clipboard save → set → wait-for-modifiers → Ctrl+V → restore
  tray.py         pystray icon: idle / recording / working / error
  watchdog.py     GetAsyncKeyState poll, hook liveness cross-check, wall-clock ceiling
tests/
  test_gestures.py   synthetic event sequences against the pure state machine
harness/
  probe_hook.py      SendInput-synthesized chord → assert state machine fires, time callbacks
  probe_inject.py    Tkinter target window → inject → read back → assert text matches
```

`gestures.py` being pure is deliberate: the gesture logic is the part most likely to be
wrong and the part hardest to test through a real keyboard. Keeping it free of Windows
calls means 20 test cases run in milliseconds with no hardware.

**Parallelization: all serial.** The modules are file-disjoint on paper, but every one of
them lands in the same wiring in `__main__.py`, the hook and injector traps are
judgment-heavy, and the Agent tool is off for this session anyway. There is no fan-out
worth taking here.

---

## Build order

Each step ends in something runnable. Nothing is built on top of an unverified layer.

1. **Scaffold + config + DLL fix.** `shout/` package, `cuda_dlls.enable()` proven to run
   before any ctranslate2 import, config load/save round-trip.
2. **`transcribe.py` + warmup.** Model resident, background warmup thread, `transcribe(np.ndarray) -> str`.
   Verify against `audio/jfk.wav` for the known exact-match sentence. This reuses a
   measured result rather than trusting a new one.
3. **`audio.py`.** Capture to float32 16k. Verify by recording 5s, writing a WAV, and
   transcribing it through step 2 — end-to-end proof the array format is right.
4. **`gestures.py` + `tests/test_gestures.py`.** Pure state machine first, tests
   immediately after, before any hook exists.
5. **`hotkey.py` + `probe_hook.py`.** Real hook, callback timing measured, wired to step 4.
6. **`inject.py` + `probe_inject.py`.** Clipboard round-trip, modifier wait, history
   exclusion, UIPI failure path.
7. **`tray.py` + `__main__.py`.** Wire it together, single-instance mutex, run it.

---

## Verification

Per lab notes: every harness needs a positive control, per-case rows, and a watchdog.

- **`probe_inject.py` oracle is a Tkinter `Text` widget** (stdlib, zero deps, fully
  controlled). Focus it, inject, read `.get()` back, compare. Positive control: run once
  with the paste step disabled and assert the oracle reports empty — proving the probe can
  see a failure.
- **`probe_hook.py`** synthesizes the chord with `SendInput` and asserts the state machine
  fires. Positive control: synthesize Ctrl alone and assert it does *not* fire.
- **Ceiling test** forces a missed key-up by dropping the release event, then asserts the
  wall-clock ceiling stops recording.
- **Real-app manual pass** is not skippable and not automatable: Chrome address bar, VS Code
  editor, Windows Terminal, Slack. A Tkinter widget proves the mechanism, not the world.
- `time` each harness once on first run and let the real number set the cadence — if they
  are sub-second, they run after every edit, not per batch.

---

## Explicitly out of scope for session 1

Floating overlay · double-tap latch (pending open question B) · hotword dictionaries ·
autostart · settings UI · LLM cleanup pass · context awareness · command mode ·
model switching · anything touching the meeting/PHI pipeline.

---

## Resolved (was: open questions)

**A. Mic stream → open-on-demand.** Stream opens at chord-down, closes at chord-up.
Confirmed that always-open would NOT have blocked other apps (WASAPI shared mode lets
Discord/OBS/browsers hold the same device simultaneously; only exclusive mode locks out,
and PortAudio opens shared) — so the tradeoff was purely the permanent mic indicator
against first-syllable clipping. Gabe has already adapted to pausing before speaking, so
pre-roll would solve a problem already worked around.

Build `audio.py` so pre-roll is a **config flag**, not an architectural assumption:
capture path takes an optional ring buffer, defaults to `preroll_ms = 0`. Flipping it on
later must not require restructuring.

**B. Double-tap latch → in v1.** Hold to talk, double-tap to latch hands-free, tap again
to end. Discriminate on the gap between first release and second press: under ~200ms is a
deliberate double-tap, over ~400ms is two separate push-to-talks; latch window 300–350ms.
Recording starts on chord-down every time, so waiting out the window costs no latency.

The hard wall-clock ceiling (60–120s) applies to held-PTT only. **A latched session must
not be killed by it** — that is the mode where a two-minute dictation is the point. Latched
mode gets its own much longer ceiling (10 min) plus the `GetAsyncKeyState` poll as the
stuck-key guard.

## Deferred to session 2

Overlay, latch (if deferred), VAD tuning, hotword dictionaries for TCG and medical
vocabulary, settings UI, autostart, per-app paste-key overrides (classic consoles need
Ctrl+Shift+V).
