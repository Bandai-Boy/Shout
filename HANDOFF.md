# Handoff — 8 Sep 2026

Session 3 is complete and committed. Shout is a real application now: it launches
with no terminal window, starts with Windows, and shows a persistent pill above
the taskbar. Tree is clean at `c06ca07`. **Shout is running right now** — PID
17140, no config file, so every setting is a dataclass default.

---

## ⚠ Waiting on Gabe

### 1. Judge how the pill looks, and say what to change
This is the actual next task and everything else waits behind it. The pill is on
screen at rest right now; dictate a few times to see it expand, show the
waveform, and switch to "Transcribing". It was invisible except mid-sentence
until this session, which is exactly why no useful opinion about it existed.

Useful specifics: resting lozenge size (78×44), gap above the taskbar (14px),
expanded width (268px), expansion speed, waveform density and brightness, the
per-state colours. All are constants at the top of `shout/overlay.py`.
**Success:** a list of changes in your own words — "smaller at rest", "slower",
"less red" — is enough; they map to single constants.

### 2. Decide whether `cue_volume` is right
Still `0.25`, still unjudged — carried from session 2. It has to carry over
whatever you are doing without startling you, and the only way to know is to
dictate a few times with music or a video playing.
**Success:** you hear the start blip without flinching, and never find yourself
checking the tray to see whether it caught the chord.

### 3. Decide whether latch is distinguishable by ear
Start is a rising two-note blip, latch is the same two notes plus a higher third.
It shares its first two notes with start, so mid-task it may not read as "I have
latched". **Success:** you can tell a latch from a plain push-to-talk without
looking. If not, say so — the fix is a different *interval*, and it is already
folded into the sound-preset plan below rather than being a separate change.

### 4. Confirm the pill actually disappears in a game
New this session and unverifiable from here: the pill hides itself while idle
when `SHQueryUserNotificationState` reports a fullscreen or presentation app, and
deliberately stays visible while recording, latched or transcribing. Whether that
API fires for the games you actually play is a real-world question.
**Success:** launch a game, see the pill gone; dictate inside it, see it appear.

---

## Current state

Branch `master`. `c06ca07` is the Qt port, `080c942` the launcher fix; everything
before that is sessions 1–2. Nothing uncommitted.

**Decisions already made — do not relitigate:** Qt (PySide6) owns all UI; the
pill is **click-through**, not clickable; the future stats view keeps **counts
only, never transcript text**. Gabe chose all three explicitly.

Deliberately deferred, in this order: the settings window, the sound presets, the
counts view. All three were held back on purpose because they depend on the
answers above — the presets in particular are partly an answer to items 2 and 3.
Still deferred from earlier sessions: hotwords, per-app paste keys, VAD tuning,
idle VRAM release.

**Gitignored files changed this session** (invisible to `git status`):
`.venv/Scripts/shoutw.exe` plus `python312.dll` and `python3.dll` beside it —
installed by `scripts/install_shortcuts.ps1` and **required for Shout to launch
without a console**. A venv rebuild silently removes them; re-run that script.
Also `.venv/` generally (PySide6-Essentials 6.11.2 added) and the `__pycache__`
directories. `scratch_bench.log` and `scratch_probe.log` still date from 7 Sep.

The runtime log lives outside the repo at `%APPDATA%\Shout\shout.log`.

## What was done

- **The terminal window was a launcher bug, not scaffolding.** uv installed its
  *console* trampoline as `.venv/Scripts/pythonw.exe` — byte-identical to
  `python.exe`, PE subsystem 3. `install_shortcuts.ps1` now installs a real
  GUI-subsystem `shoutw.exe` and asserts the subsystem on both sides of the copy.
- **`shout/overlay.py` rewritten on Qt.** Persistent, collapsed at rest, animated
  expansion, mirrored waveform from a level history, soft shadow and antialiased
  corners (impossible under Tk, whose transparency was a chroma key), and the
  fullscreen policy in item 4.
- **`shout/tray.py`** is QSystemTrayIcon; pystray and its non-daemon thread are
  gone. **`shout/__main__.py`** runs one Qt event loop for both surfaces.
- **`harness/probe_overlay.py`** and **`probe_app.py`** rewritten; `probe_app`
  gained stage markers and a watchdog.

Why each non-obvious decision went the way it did is in the module docstrings and
`CLAUDE.md` Lab Notes. Neither needs re-deriving.

## Verified

Run from Bash, against the committed tree, with Shout stopped:

- **8 gates, 157 assertions, ~27s. 8/8.** (Was 122 assertions.)
- READY in **2.09s** from launch, against a 2.14s baseline.
- Idle CPU **1.12%** of one core with the overlay on, against a **1.37%** control
  with `overlay: false` — the pill's cost is *below* noise, and that baseline is
  the watchdog's 20Hz key poll.
- Launcher: `shoutw.exe` runs with **no child process and no console window** in
  a full window enumeration; before the fix the same enumeration found a
  `PseudoConsoleWindow` and a Windows Terminal titled "Shout".

**What was proved able to fail** — the part that makes the green meaningful:
- Reverting the cross-thread quit fix hangs `probe_app` at exactly the stage it
  names. That bug (`QApplication.quit()` from a worker never ends the loop) was
  found by the gate, not by reasoning.
- The overlay focus gate still shows an identically-flagged window stealing
  focus, and reports INCONCLUSIVE rather than passing if it cannot.
- The cue gate still plays a loud reference tone first and reports SKIPPED on
  headphones instead of passing trivially.

**What is NOT verified:** everything in *Waiting on Gabe*. No human has yet
watched the pill during a real dictation, the fullscreen hide has never met a
real game, and the cue volume and latch distinctness remain unjudged.

## Running things

Shout is live as **PID 17140** (`shoutw.exe -m shout`), started from the Start
menu shortcut. It autostarts at login. A second copy exits on the single-instance
mutex, so relaunching is harmless. **It is running the code as committed** — if
you change anything under `shout/`, quit from the tray and relaunch or you are
testing the old build.

There is **no `config.json`**; every setting is a default. Creating one at
`%APPDATA%\Shout\config.json` overrides only the keys it contains.

Standing commands and the two gate-invocation traps are in `CLAUDE.md`; do not
copy them here.

## Next

1. **Gabe answers the four items above.** Genuinely the next task — the remaining
   backlog was deferred pending exactly this feedback.
2. **Sound presets**, once items 2 and 3 are answered. The design agreed this
   session: one preset = one *material*, and the three cues are three *gestures*
   on it — struck once rising for start, once falling for stop, and **twice
   quickly for latch**, mirroring the double-tap. That replaces "start plus a
   third note" and is the structural fix for item 3. Candidate materials: Blip
   (current), Wood, Marimba, Glass, Drop, Tick. Build a standalone preview script
   *before* wiring any of it into the app — restarting Shout to audition a sound
   is far too slow a loop to converge on taste.
   - **Trap:** `probe_cues` asserts that cue bleed does not change the
     transcript, and the property it relies on is that the VAD rejects a *pure
     tone*. Whisper hallucinates confident garbage on ambiguous non-speech, so a
     noise-based preset (Tick, and anything breathy) can break that — and the
     failure is a stray word in your dictation, not a gate error. Harmonic
     presets are structurally safe; re-run `probe_cues` for every preset and cut
     any that fails.
3. **Settings window and the counts view**, last. Both touch `__main__.py` and
   the Qt app object, so they are strictly serial after each other and after any
   overlay changes from item 1 — there is no fan-out available in this phase.
   - **Trap:** anything that quits or touches a widget from a non-GUI thread must
     follow the `stop()`-sets-a-flag / `teardown()`-after-`exec()` split already
     in `overlay.py` and `tray.py`. Qt does not raise on a cross-thread widget
     touch; it corrupts quietly.

## Open questions

- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually earn a hotword entry?
- **Does the resident model cost anything while gaming?** Still unmeasured
  against a control. A warmed Shout holds ~2.3 GB of the 12 GB card.
  `unload_model(to_cpu=True)` / `load_model()` is verified to exist — but do not
  build an idle-release timer speculatively. Test it properly: quit Shout from
  the tray and replay the same scene. "Felt fine" with it running is not evidence.

## Also open

- `harness/probe_inject.py` still imports Tk for a throwaway text field. Harmless
  and unrelated to the app, but Tk is therefore still a harness dependency.
- The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched.
- The noScribe-vs-WhisperX diarization decision in `RESEARCH.html` is still open.
