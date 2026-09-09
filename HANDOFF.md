# Handoff — 8 Sep 2026

Session 4 is complete and pushed at `532b46a`. Tree clean, `master` tracks
`origin/master`. The pill has been judged and reshaped, the cue sounds are now
something you can sit and tune rather than constants in a file, and the repo has
a private remote with a commit guard in front of it. **Shout is running right
now** — PID 24284, still no config file, so every setting is a dataclass default.

Remote: **https://github.com/Bandai-Boy/Shout**, private (verified via the API,
not assumed). Ten commits up there; 39 files, all source and docs, and the only
audio blob is the public `jfk.wav` benchmark clip.

---

## ⚠ Waiting on Gabe

### 1. Confirm the pill disappears in a game
Carried from session 3 and still the only untested claim about the overlay.
Gabe said he was heading into a gaming session, so this may already be answered.
The pill hides while idle when `SHQueryUserNotificationState` reports fullscreen
or presentation, and deliberately stays visible while recording, latched or
transcribing. **Success:** launch a game, see the pill gone; dictate inside it,
see it appear.

### 2. Pick a cue voice
Right-click the tray icon → **Cue sounds…**. Six materials and seven sliders.
The brief was "deeper, softer, less shrieking": **Root** is the big lever and
**Decay** is the one that does the most at equal volume — 0 is the held beep
shipping now, higher is struck and ringing out. Try `soft` or `wood` first.
**Success:** a saved voice, Shout relaunched, and `harness/probe_cues.py` re-run
green. That gate reads the *configured* voice — CLAUDE.md says why it has to.

### 3. One open judgment on the pill, if it bothers you
Recording is now the short state (174px) and the labelled ones are not
(245–251px), so the pill **widens when it flips to "Transcribing"**, animated
over ~160ms, on every dictation. That falls out of deriving width per state,
which is what made removing the word pay off. If it reads as fidgety, one fixed
width for all states is small — but it would have to be ~250px, giving back most
of the gain. Nobody has watched this on a real dictation yet.

---

## Answered — do not re-ask

- **Pill looks:** distance from the taskbar, colours and opacity all approved
  as-is. Height was the complaint (fixed, −30%) and expanded width (fixed, by
  deleting the word "Recording"). The waveform was explicitly liked — its
  density is unchanged and should stay that way.
- **`cue_volume = 0.25` is right.** Judged, approved, closed.
- **Latch IS distinguishable by ear** from a plain push-to-talk. The sound work
  is therefore about *taste*, not distinctness — the "one material, three
  gestures, latch struck twice" redesign that session 3 proposed as the fix for
  indistinctness is not needed and was not built.
- **Should the repo have a remote?** Yes, and it now does: private, on the
  reasoning that the premise protects *audio and transcripts*, not source, and
  that the real gain is offsite backup for work that existed on one SSD.

## Current state

Branch `master` at `532b46a`, four commits added this session on top of
session 3's `0ecaabd`.

**Decisions already made — do not relitigate:** Qt owns all UI; the pill is
click-through, not clickable; the future stats view keeps counts only, never
transcript text; the recording dot uses a level-driven halo rather than a blink,
because the halo says *heard you* and a fixed-period blink does not.

Deliberately deferred, in this order: the settings window, then the counts view.
Both touch `__main__.py` and the Qt app object, so they are strictly serial.
Still deferred from earlier sessions: hotwords, per-app paste keys, VAD tuning,
idle VRAM release.

**Gitignored files, invisible to `git status`:** `.venv/Scripts/shoutw.exe` plus
`python312.dll` and `python3.dll` beside it, installed by
`scripts/install_shortcuts.ps1`. Required both for Shout to launch without a
console *and* for the tray's Cue sounds… entry to open the lab windowed. A venv
rebuild silently removes them; re-run that script. `scratch_bench.log` and
`scratch_probe.log` still date from 7 Sep and are stale.

The runtime log is outside the repo at `%APPDATA%\Shout\shout.log`.

## What was done

- **`shout/overlay.py`** — HEIGHT 44→31, with bar height, dot and halo made
  fractions of it; expanded width derived per state from measured label widths;
  `LABEL["recording"] = ""`; expanded-to-expanded changes glide.
- **`shout/cues.py`** — gestures are frequency *ratios*, a `Voice` says how they
  sound, six harmonic presets. The default is byte-identical to the old tones at
  both 48k and 44.1k.
- **`scripts/cue_lab.py`** — new. Drives the app's own `build()` through the same
  output stream, so it cannot tune something the app will not play.
- **`shout/tray.py`** — a Cue sounds… entry launching the lab detached.
- **`hooks/`** — the pre-commit guard, and `harness/probe_repo.py` proving it.
- **Probes** — `probe_cues` follows the configured voice; `probe_overlay` and
  `probe_app` assert per-state widths and the tray's reachability.

Why each non-obvious decision went the way it did is in the module docstrings and
`CLAUDE.md` Lab Notes. Neither needs re-deriving.

## Verified

Run from Bash, against the committed tree, with Shout stopped:

- **9 gates, 201 assertions, ~31s. 9/9.** (Was 8 gates, 157 assertions.)
- READY in **2.14s** from launch, against the 2.09s baseline.
- The live pill, measured off the real window rather than the offscreen render:
  31px tall, 14px gap above the taskbar, NOACTIVATE / TOOLWINDOW / TRANSPARENT
  all set.
- The tray entry opens the lab in **under 0.5s**, proven by window enumeration
  with a before/after control (8 → 9 visible titled windows).
- The commit guard blocks a real transcript-shaped commit in *this* repo, not
  only in the sandbox — attempted for real, refused, cleaned up.
- What GitHub actually holds was listed from the API after the push: 39 files,
  no transcripts, no recordings but the benchmark clip, no secrets.

**What was proved able to fail** — the part that makes the green meaningful:
- Restoring the word "Recording" fails exactly the three new width rows.
- Renaming the tray entry, or moving the lab script, each fail their own row.
- Ignoring `spread` fails the interval anchor, caught on `drop` — the one preset
  where spread ≠ 1.0.
- A fake `APPDATA` proved `probe_cues` follows the configured voice.
- The repo gate's two controls stop it being satisfied by a guard that blocks
  everything: ordinary source must still commit, and `audio/jfk.wav` must still
  be allowed.
- **One control found a real hole rather than confirming a fix.** A deliberate
  20% detune inside `notes()` slipped past all 26 waveform assertions, because
  the "in tune" check read its expectation from the same helper the synth builds
  from. Two rows anchored on what the voice *declares* now catch it.

**What is NOT verified:** everything in *Waiting on Gabe*. No human has watched
the reshaped pill during a real dictation, the fullscreen hide has never met a
real game, and no cue voice has been auditioned.

## Running things

Shout is live as **PID 24284** (`shoutw.exe -m shout`), autostarts at login, and
**is running the code as pushed** — if you change anything under `shout/`, quit
from the tray and relaunch or you are testing the old build. A second copy exits
on the single-instance mutex, so relaunching is harmless.

There is **no `config.json`** until the cue lab writes one. Creating it at
`%APPDATA%\Shout\config.json` overrides only the keys it contains.

Standing commands, the commit-guard setup, and the two gate-invocation traps are
all in `CLAUDE.md`; do not copy them here.

## Next

1. **Gabe answers items 1 and 2 above.** Item 1 costs nothing extra — it is the
   same gaming session as the resident-model question below.
2. **Whatever the pill and the voice need after that.** Both are a single
   constant or a saved config; neither is a rebuild.
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

## Also open

- `harness/probe_inject.py` still imports Tk for a throwaway text field. Harmless
  and unrelated to the app, but Tk is therefore still a harness dependency.
- The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched.
- The noScribe-vs-WhisperX diarization decision in `RESEARCH.html` is still open.
