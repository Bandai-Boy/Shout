# Handoff — 8 Sep 2026

## Session 2 is complete

Shout now tells you it is listening. Hold Ctrl+Win and you get a rising blip and a
pill above the taskbar with a live level meter; double-tap and the pill turns
yellow, says Hands-free, and a three-note climb confirms the latch; release and a
falling blip closes it out.

**8 gates, 122 assertions, ~23s.** Results and the traps behind each decision are
in [PLAN.md](PLAN.md) under *Session 2: Feedback*; the durable failure modes are
in [CLAUDE.md](CLAUDE.md) Lab Notes. Neither needs re-deriving.

```
.venv/Scripts/python.exe harness/gates.py     # 8 gates, 122 assertions, ~23s
.venv/Scripts/python.exe -m shout             # run with a console + live log
```

The threading model changed: **Tk owns the main thread and pystray runs
detached.** pystray supports that on Windows and Tk does not, so only one of the
two could move. `harness/probe_app.py` is the gate that covers the composition —
including that the process can still exit, which it cannot if `tray.stop()` is
ever dropped from `_quit`.

## Waiting on you

Shout is **not running right now** — I stopped it to run the gates, and the code
has changed since. Start it from the Start menu, or:

```
.venv/Scripts/pythonw.exe -m shout
```

Four things only real use can answer. All are one-line edits in
`%APPDATA%/Shout/config.json`:

- **`cue_volume` (0.25).** Loud enough to hear over what you are doing, quiet
  enough not to startle. This is the number most likely to be wrong.
- **Do the three cues read as distinct** mid-task, without looking? Latch is the
  one at risk — it is the same two notes as start plus a third.
- **Is the pill in the right place?** 14px above the taskbar, centred on the
  monitor holding the focused window. It follows focus across monitors.
- **`cues` / `overlay` (both true).** Either can be turned off independently if it
  turns out to be noise rather than feedback.

Config additions this session: `cues`, `cue_volume`, `output_device`, `overlay`.

## Carried forward, unanswered

- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually earn a hotword entry?
- **Does the resident model cost anything while gaming?** Still unmeasured against
  a control. A warmed Shout holds ~2.3 GB of the 12 GB card. If a game ever
  stutters, `unload_model(to_cpu=True)` / `load_model()` is verified to exist —
  but do not build an idle-release timer speculatively. To test it properly, quit
  Shout from the tray and replay the same scene; "felt fine" with it running is
  not evidence either way.

## Two things to know before touching the gates

- **Quit Shout before running `harness/gates.py`.** Observed this session: leaving
  it running fails the inject gate, because `probe_hook`/`probe_stuck` synthesize
  a real Ctrl+Win chord that a live instance cannot tell from your hands.
  `probe_app`, `probe_cues` and `probe_overlay` are safe on their own.
- `probe_cues` plays three short tones through the speakers and records them.
  On headphones it reports the bleed test SKIPPED rather than passing it.

## Not started

The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched and
unrelated to the dictation app. The noScribe-vs-WhisperX diarization decision in
`RESEARCH.html` is still open.
