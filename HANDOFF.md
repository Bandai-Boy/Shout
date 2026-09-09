# Handoff — 8 Sep 2026

Session 4 is complete and committed at `0306471`. Tree clean. The pill has been
judged and reshaped, and the cue sounds are now something you can sit and tune
rather than something baked into constants. **Shout is running right now** — PID
24724, still no config file, so every setting is a dataclass default.

**There is no git remote.** `master` has no upstream and `git remote -v` is
empty, so nothing has ever been pushed. Everything is committed locally. Creating
a remote is an open decision, not an oversight.

---

## ⚠ Waiting on Gabe

### 1. Confirm the pill disappears in a game
Carried from session 3, still the only untested claim about the overlay, and
Gabe said he was about to game — so this is likely already answered by the time
anyone reads this. The pill hides while idle when `SHQueryUserNotificationState`
reports fullscreen or presentation, and deliberately stays visible while
recording, latched or transcribing. **Success:** launch a game, see the pill
gone; dictate inside it, see it appear.

### 2. Pick a cue voice
Right-click the tray icon → **Cue sounds…**. Six materials (blip, soft, wood,
marimba, drop, glass) and seven sliders. The brief was "deeper, softer, less
shrieking": **Root** is the big lever and **Decay** is the one that matters most
at equal volume — 0 is the held beep that is shipping now, higher is struck and
ringing out. Save writes `cue_preset` / `cue_voice` / `cue_volume` into
config.json, merging rather than rewriting.
**Success:** a saved voice, Shout relaunched, and `harness/probe_cues.py` re-run
green. That gate reads the *configured* voice — see CLAUDE.md for why it has to.

### 3. One open judgment on the pill, if it bothers you
Because recording is now the short state (174px) and the labelled ones are not
(245–251px), the pill **widens when it flips to "Transcribing"**, animated over
~160ms, on every dictation. That is a consequence of deriving width per state,
which is what made removing the word pay off. If it reads as fidgety, one fixed
width for all states is a small change — but it would have to be ~250px, giving
back most of what was gained. Nobody has watched this happen yet on a real
dictation.

---

## Answered this session — do not re-ask

- **Pill looks:** distance from taskbar, colours and opacity all approved as-is.
  Height was the complaint (fixed, −30%), and expanded width (fixed, via
  deleting "Recording"). The waveform itself was explicitly liked — its density
  is unchanged and should stay that way.
- **`cue_volume = 0.25` is right.** Judged, approved, closed.
- **Latch IS distinguishable by ear** from a plain push-to-talk. The
  sound-preset work is therefore about *taste*, not about distinctness — the
  "one material, three gestures, latch struck twice" redesign the session-3
  handoff proposed as the fix for indistinctness is not needed and was not built.

## Current state

Branch `master`, `0306471` on top of session 3's `0ecaabd`.

**Decisions already made — do not relitigate:** Qt owns all UI; the pill is
click-through, not clickable; the future stats view keeps counts only, never
transcript text; the recording dot uses a level-driven halo rather than a blink,
because the halo says *heard you* and a fixed-period blink does not.

Deliberately deferred, in this order: the settings window, then the counts view.
Both touch `__main__.py` and the Qt app object so they are strictly serial after
each other. Still deferred from earlier sessions: hotwords, per-app paste keys,
VAD tuning, idle VRAM release.

**Gitignored files, invisible to `git status`:** `.venv/Scripts/shoutw.exe` plus
`python312.dll` and `python3.dll` beside it, installed by
`scripts/install_shortcuts.ps1` and required both for Shout to launch without a
console *and* for the tray's Cue sounds… entry to open the lab windowed. A venv
rebuild silently removes them; re-run that script. `scratch_bench.log` and
`scratch_probe.log` still date from 7 Sep and are stale.

The runtime log is outside the repo at `%APPDATA%\Shout\shout.log`.

## What was done

- **`shout/overlay.py`** — HEIGHT 44→31 with bar height, dot and halo made
  fractions of it; expanded width derived per state from measured label widths;
  `LABEL["recording"] = ""`; expanded-to-expanded width changes glide.
- **`shout/cues.py`** — gestures are frequency *ratios*, a `Voice` says how they
  sound (root, spread, length, gap, decay, brightness, trim), six harmonic
  presets. The default is byte-identical to the old tones at 48k and 44.1k.
- **`scripts/cue_lab.py`** — new. Drives the app's own `build()` through the
  same output stream, so it cannot tune something the app will not play.
- **`shout/tray.py`** — a Cue sounds… entry, launching the lab detached with the
  GUI-subsystem interpreter.
- **`shout/config.py` / `__main__.py`** — `cue_preset` and `cue_voice`.
- **Probes** — `probe_cues` follows the configured voice; `probe_overlay` and
  `probe_app` assert per-state widths and the tray's reachability.

Why each non-obvious decision went the way it did is in the module docstrings and
`CLAUDE.md` Lab Notes. Neither needs re-deriving.

## Verified

Run from Bash, against the committed tree, with Shout stopped:

- **8 gates, 184 assertions, ~28s. 8/8.** (Was 157 assertions.)
- READY in **2.14s** from launch, against the 2.09s baseline.
- The live pill, measured off the real window rather than the offscreen render:
  31px tall, 14px gap above the taskbar, NOACTIVATE / TOOLWINDOW / TRANSPARENT
  all set.
- The tray entry opens the lab in **under 0.5s**, proven end to end by window
  enumeration with a before/after control (8 → 9 visible titled windows).
- The lab's Save merges: unrelated config keys survived a save, and an untouched
  preset field was not written as an override.

**What was proved able to fail** — the part that makes the green meaningful:
- Restoring the word "Recording" fails exactly the three new width rows.
- Renaming the tray entry, or moving the lab script, each fail their own row.
- Ignoring `spread` fails the interval anchor, caught on `drop` — the one preset
  where spread ≠ 1.0.
- A fake `APPDATA` proved `probe_cues` follows the configured voice rather than
  the default.
- **One control found a real hole rather than confirming a fix.** A deliberate
  20% detune inside `notes()` slipped past all 26 waveform assertions, because
  the "in tune" check read its expectation from the same helper the synth builds
  from. Two rows anchored on what the voice *declares* now catch it. Without that
  control the suite would have been reported as green and been blind to it.

**What is NOT verified:** everything in *Waiting on Gabe*. No human has yet
watched the reshaped pill during a real dictation, the fullscreen hide has never
met a real game, and no cue voice has been auditioned.

## Running things

Shout is live as **PID 24724** (`shoutw.exe -m shout`), autostarts at login, and
**is running the code as committed** — if you change anything under `shout/`,
quit from the tray and relaunch or you are testing the old build. A second copy
exits on the single-instance mutex, so relaunching is harmless.

There is **no `config.json`** until the cue lab writes one. Creating it at
`%APPDATA%\Shout\config.json` overrides only the keys it contains.

Standing commands and the two gate-invocation traps are in `CLAUDE.md`; do not
copy them here.

## Next

1. **Gabe answers items 1 and 2 above.** Item 1 costs nothing extra — it is the
   same gaming session as the resident-model question below.
2. **Whatever the pill and the voice need after that.** Both are single
   constants or a saved config; neither is a rebuild.
3. **Settings window, then the counts view.** Strictly serial — both touch
   `__main__.py` and the Qt app object, so there is no fan-out in this phase.
   - **Trap:** anything that quits or touches a widget from a non-GUI thread must
     follow the `stop()`-sets-a-flag / `teardown()`-after-`exec()` split already
     in `overlay.py` and `tray.py`. Qt does not raise on a cross-thread widget
     touch; it corrupts quietly.

## Open questions

- **Does the resident model cost anything while gaming?** Still unmeasured
  against a control, and answerable in the same session as item 1. A warmed
  Shout holds ~2.3 GB of the 12 GB card. `unload_model(to_cpu=True)` /
  `load_model()` is verified to exist — but do not build an idle-release timer
  speculatively. Quit Shout from the tray and replay the same scene. "Felt fine"
  with it running is not evidence.
- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually earn a hotword entry?
- Should this repo have a remote at all?

## Also open

- `harness/probe_inject.py` still imports Tk for a throwaway text field. Harmless
  and unrelated to the app, but Tk is therefore still a harness dependency.
- The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched.
- The noScribe-vs-WhisperX diarization decision in `RESEARCH.html` is still open.
