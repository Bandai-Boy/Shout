# Handoff — 8 Sep 2026

## Session 1 is complete and shipped

Shout works. Hold Ctrl+Win, speak, release, text appears at the cursor. Double-tap
to latch hands-free, tap again to end. All six done-criteria measured, manual
real-app pass passed (VS Code, Chrome, Discord), 177–333ms end-to-end.

Full results in [PLAN.md](PLAN.md); conventions and gotchas in
[CLAUDE.md](CLAUDE.md). Neither needs re-deriving.

```
.venv/Scripts/python.exe harness/gates.py     # 5 gates, 81 assertions, ~14s
.venv/Scripts/python.exe -m shout             # run with a console + live log
```

Now a git repo (`master`, one commit). It autostarts: `scripts/install_shortcuts.ps1`
created a Start menu entry and a Startup entry, both pointing at
`.venv\Scripts\pythonw.exe -m shout` (pythonw = no console window) with
`assets/shout.ico`. **Shortcuts store absolute paths — re-run that script after
moving the project folder or they silently point at nothing.** A second copy exits
on the single-instance mutex, so double-launching is harmless.

Vocabulary needs no hotword biasing yet: Pokémon names and brand terms (DocuHub,
Wix) that iOS dictation garbles came back correct with no tuning.

---

# Next session: make it feel like a product

Two items, both requested after real use. This is the entire scope — the rest of
the old session 2 list (hotwords, per-app paste keys, settings UI) stays parked
until friction actually demands it.

## 1. Audio cues

The one thing that trips Gabe up: **no confirmation that recording started.**
Wispr Flow plays a short rising blip on start and its reverse on stop; that is the
target behaviour, and a third distinct cue for latch-on is probably worth it since
latching is otherwise invisible.

Synthesize the tones rather than extracting Wispr's — theirs are their property,
and a two-note blip is a few lines of numpy. Use a short envelope (~5ms fade in
and out); a raw sine that starts at full amplitude clicks audibly.

**The trap.** With `preroll_ms = 0` the mic opens on chord-down and the start cue
plays at that same instant, so the cue can be picked up by the microphone through
the speakers and land in the transcript, or trip VAD into thinking speech started.
Test it deliberately with speakers at normal volume before assuming it is fine.
Options if it bites: play the cue before opening the stream, trim the first ~80ms
of captured audio, or pick a short high-frequency blip outside the speech band.

Latency matters — the cue is the feedback, so it must fire on chord-down with no
perceptible delay. Preload the samples into numpy arrays at startup and play them
non-blocking (`sounddevice.play`, or `winsound.PlaySound` with `SND_ASYNC` if the
samples get written out as .wav). Do not read a file on the hot path.

## 2. The overlay bubble

A persistent pill above the taskbar showing state — hidden or a small dot when
idle, visibly active while recording, distinct while latched. A live audio level
inside it would make "is it actually hearing me" answerable at a glance.

**The critical constraint: the overlay must never take focus.** Dictation requires
a focused editable field, so an overlay that steals focus would break the app with
its own UI. It needs `WS_EX_NOACTIVATE` (0x08000000) plus `WS_EX_TOOLWINDOW` (keeps
it out of alt-tab), applied via `SetWindowLongPtrW(GWL_EXSTYLE)` in ctypes — the
`winapi.py` module already has the bindings pattern to follow. Add
`WS_EX_TRANSPARENT` if it should be click-through.

This is precisely why the research ruled out Tauri v2: it has open Windows bugs for
non-focusable overlay behaviour. Tkinter with `overrideredirect(True)`, `-topmost`,
and a raw `SetWindowLong` call is the intended path.

**The architectural question to settle first:** pystray currently owns the main
thread (`icon.run()` blocks). Tkinter needs a thread that owns all of its calls.
Either run Tk in its own thread with its own mainloop, or move pystray off main.
Decide this before writing overlay code — it is the kind of thing that is painful
to retrofit.

Position it against the **work area** (`SystemParametersInfo(SPI_GETWORKAREA)`),
not screen height, so it sits correctly regardless of taskbar size or position.
Multi-monitor: it should appear on the monitor holding the focused window.

## Open questions, to answer from use rather than analysis

- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually earn a hotword entry?
- **Does the resident model cost anything while gaming?** Measured 8 Sep: a warmed
  Shout holds **~2.3 GB of the 12 GB card** (5250 MiB vs a 2924 MiB baseline), and
  holds it across at least 90s of idle. Note that is well under the ~6 GB quoted in
  `RESEARCH.html`, which is peak inference including beam-search working memory, not
  resident weights. One earlier data point suggested Windows had evicted an idle
  instance's VRAM entirely — killing it freed only 12 MiB — which would mean WDDM
  reclaims it under pressure from other GPU apps. **That mechanism is unproven**;
  it was not reproducible on demand without real memory pressure.

  If games ever feel starved, the fix is already available and verified to exist:
  `WhisperModel.model.unload_model(to_cpu=True)` parks the weights in system RAM
  and `load_model()` brings them back, so an idle timer could release VRAM after
  N minutes and reload on the next chord press. Do not build this speculatively —
  wait until a game actually stutters.

  To test it: the comparison only means something against a control, so quit Shout
  from the tray menu and play the same scene again. "Felt fine" with it running is
  not evidence either way.

## Not started

The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched and
unrelated to the dictation app. The noScribe-vs-WhisperX diarization decision in
`RESEARCH.html` is still open.
