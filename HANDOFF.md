# Handoff — 8 Sep 2026

Session 2 is complete and committed. Shout now tells you it is listening, by ear
and by eye. Tree is clean at `59d257d`. **Shout is running right now** — PID
26636.

---

## ⚠ Waiting on Gabe

Four judgements that no gate can make, because they are about how the thing
*feels* in use. The pill has been screenshotted and asserted, but no human has
yet seen it during a real dictation. All four are one-line edits in
`%APPDATA%/Shout/config.json` followed by a restart.

### 1. Decide whether `cue_volume` is right
Currently `0.25`. It has to carry over whatever you are doing without startling
you. **This is the number most likely to be wrong** and the only way to know is
to dictate a few times with music or a video playing.
**Success:** you hear the start blip without flinching, and you never find
yourself checking the tray to see whether it caught the chord.

### 2. Decide whether the three cues are distinguishable without looking
Start is a rising two-note blip, stop is the same two notes falling, latch is
start plus a third higher note. **Latch is the one at risk** — it shares its
first two notes with start, so mid-task it may not read as "I have latched."
**Success:** you can tell a latch from a plain push-to-talk by ear alone. If not,
the fix is a different interval for latch, not a louder one — say so and it is a
two-line change in `_TONES` in `shout/cues.py`, plus a re-run of `probe_cues`.

### 3. Confirm the pill is in the right place
It sits 14px above the taskbar, centred on the monitor holding the focused
window, and follows focus across monitors.
**Success:** it is visible where you naturally glance, and never covers something
you needed. On the 3440px ultrawide it lands at x=1597.

### 4. Decide whether either surface is noise rather than feedback
`cues` and `overlay` are independent booleans, both `true`. Turn either off if it
turns out to be clutter.

---

## Current state

Branch `master`. `59d257d` records the gate-invocation trap below; `b3ea920` is
session 2; `5413b08`, `a864bcf`, `e531c26`, `8c094df` are session 1 and its
follow-ups. Nothing uncommitted.

Deliberately deferred, not forgotten: hotwords, per-app paste keys, a settings
UI, VAD tuning, and idle VRAM release. Each waits until friction demands it.
Everything the old session-2 list contained beyond cues and the overlay was cut
on purpose.

No gitignored files in the repo changed this session — `scratch_bench.log` and
`scratch_probe.log` still date from 7 Sep. The runtime log lives **outside** the
repo at `%APPDATA%\Shout\shout.log` (rotating, 1MB × 3) and is where anything
about a live run has to be read from.

## What was done

- `shout/cues.py` — three synthesized tones, preloaded, played through a
  persistent output stream. `play()` is a single attribute rebind.
- `shout/overlay.py` — the pill. Never takes focus, never in alt-tab, clicks pass
  through it.
- `shout/__main__.py` — Tk moved to the main thread, tray detached beside it.
  Capture state now outranks "loading", so the pill appears if you dictate while
  the model is still warming.
- `shout/audio.py` — per-block RMS on `Recorder.level`, feeding the meter.
- `harness/probe_cues.py`, `probe_overlay.py`, `probe_app.py` — three new gates.
- `harness/gates.py` — per-gate timeout, utf-8 forced both directions.

Why each non-obvious decision went the way it did is in `PLAN.md` under *Session
2: Feedback*; the failure modes are in `CLAUDE.md` Lab Notes. Neither needs
re-deriving.

## Verified

Run from Bash, against the committed tree, with Shout stopped:

- **8 gates, 122 assertions, ~23s.** 8/8.
- `Cues.play()` at **0.001ms**; cue lengths 112ms / 112ms / 164ms, all zero-valued
  at both edges.
- Overlay at 14px above the work area, centred, correct size, click-through
  confirmed functionally via `WindowFromPoint` rather than by reading the flag.
- App reaches **READY in 2.14s** from launch with everything composed.

**What was proved able to fail** — the part that makes the green meaningful:
- The overlay focus gate shows an *identically configured* window stealing focus
  via `deiconify()`. Without that control the "did not take focus" row is worth
  nothing, and the gate reports INCONCLUSIVE rather than passing when the control
  cannot demonstrate theft.
- The cue bleed gate plays a loud reference tone first; on headphones it reports
  SKIPPED instead of passing trivially.
- Removing `tray.stop()` from `_quit` was run as a control and **hung the probe
  forever**, which is how the missing per-gate timeout was found.

**What is NOT verified:** everything in *Waiting on Gabe*. Nobody has watched the
pill during a real dictation, and the cue volume and latch distinctness are
unjudged. The gates prove the pill is correctly placed and cannot steal focus;
they say nothing about whether it is *pleasant*.

## Running things

Shout is live as **PID 26636** (`pythonw.exe -m shout`), started from the Start
menu shortcut via explorer so it is not tied to any session. It also autostarts
at boot. A second copy exits on the single-instance mutex, so relaunching is
harmless.

**It is running the code as committed.** If you change anything under `shout/`,
quit it from the tray and restart, or you will be testing the old build.

Standing commands are in `CLAUDE.md`; do not copy them here.

## Next

1. **Use it for a day, then answer the four questions above.** This is genuinely
   the next task — the remaining backlog was deferred pending exactly this
   feedback, so guessing at it now would be building on nothing.
2. If the latch cue turns out to be indistinct, retune `_TONES` in
   `shout/cues.py` and re-run `probe_cues`.
   - **Trap:** that gate's transcript assertions rely on the cue being *not
     speech-like* — the VAD is what makes the bleed harmless. A longer or more
     complex cue can break that, and the failure would be a stray word in your
     dictation, not a gate error. Re-run `probe_cues` after any tone change.
3. Only then consider hotwords or per-app paste keys.

**Trap that will cost you a round trip otherwise:** if `harness/gates.py` reports
6/8 with `hook` and `overlay` failing, nothing is broken — you invoked it from a
detached launcher instead of a real terminal, and those gates need the launching
process to hold Windows foreground rights. Quit Shout first too, or `inject`
fails. Both are written up in `CLAUDE.md`.

## Open questions

- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually earn a hotword entry?
- **Does the resident model cost anything while gaming?** Still unmeasured
  against a control. A warmed Shout holds ~2.3 GB of the 12 GB card. If a game
  stutters, `unload_model(to_cpu=True)` / `load_model()` is verified to exist —
  but do not build an idle-release timer speculatively. Test it properly: quit
  Shout from the tray and replay the same scene. "Felt fine" with it running is
  not evidence either way.

## Also open

- The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched and
  unrelated to the dictation app.
- The noScribe-vs-WhisperX diarization decision in `RESEARCH.html` is still open.
