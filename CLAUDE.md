# Shout

Local-only voice stack on this machine. Two tracks that share an inference environment:

1. **Dictation app** — hold Ctrl+Win, speak, text appears at the cursor. Being built now.
   See [PLAN.md](PLAN.md).
2. **Meeting transcription** — `scripts/transcribe_meeting.py`, for recordings that may
   contain PHI. Separate concern, already working.

Nothing leaves this machine. No API, no cloud, no BAA required. That constraint drove
every architectural decision and is not negotiable.

**This repo is PUBLIC** at github.com/Bandai-Boy/Shout (since 10 Sep 2026), so
everything committed is published. Commits go out under the GitHub noreply
address, set in this repo's local git config. `Bandai-Boy/Shout-archive` is the
private, archived pre-publication history, and its commits carry the personal
email: never unarchive it or make it public. `HANDOFF*.md` is gitignored on
purpose.

**Read [RESEARCH.html](RESEARCH.html) before changing any inference, hotkey, or model
decision.** It carries the measured benchmarks, the claims audit, the ruled-out options
with reasons, and the four hotkey traps. Claims in it are tagged Measured / Verified /
Refuted / Unverified — respect the tags; the Unverified ones are leads, not facts.

## Environment

- Python 3.12 venv at `.venv/`, built by `uv sync --locked` from `pyproject.toml`
  + `uv.lock`. Every direct dependency is pinned to the version the gates ran
  against, and `[tool.uv] constraint-dependencies` holds the transitive ones that
  would otherwise resolve newer (onnxruntime runs the VAD). The lock reproduces
  this venv exactly, bar the unused `pystray`. Upgrading a pin means re-locking
  AND re-running the gates; `scripts/install.ps1` is the one-command install.
- faster-whisper 1.2.1, CTranslate2 4.8.2, cuBLAS 12.9, cuDNN 9.25 — all pip wheels.
  No cmake, no MSVC, no CUDA Toolkit.
- PySide6-Essentials 6.11.2 owns both UI surfaces (pill + tray). Tk and pystray
  are gone from the app; `harness/probe_inject.py` still uses Tk for a throwaway
  text field, which is unrelated.
- The launcher is `.venv/Scripts/shoutw.exe`, installed by
  `scripts/install_shortcuts.ps1`. It is gitignored — re-run that script after
  any venv rebuild or Shout goes back to opening a terminal window.
- Model `large-v3-turbo` cached in the HF hub cache. `models/` is empty and unused.
- Target: RTX 5070, 12GB, sm_120 (Blackwell) · Ryzen 7 9800X3D · Win 11.

## Commands

```bash
powershell -ExecutionPolicy Bypass -File scripts/install.ps1  # sync, model, shortcuts
.venv/Scripts/python.exe scripts/download_model.py           # fetch + verify the model
.venv/Scripts/python.exe scripts/make_readme_image.py        # re-render the README pill
.venv/Scripts/python.exe scripts/bench.py                    # throughput benchmark
.venv/Scripts/python.exe scripts/gpu_probe.py                # CUDA + compute-type smoke test
.venv/Scripts/python.exe scripts/transcribe_meeting.py <file>  # meeting pipeline
```

## The Shout window

`shout/window.py` is one window with three pages, **Recent dictations**, **Cue
sounds** and **Orb**, opened from the tray menu (an entry per page). It runs INSIDE Shout's
process, on the GUI thread; the cue lab used to be a separate `scripts/cue_lab.py`
process and moved in on 10 Sep, because the recent dictations live only in
Shout's memory (`shout/recent.py`, the last 10) and a second process could only
see them if they were written to disk, which the README promises never happens.
Consequences: `window.application()` builds the QApplication for Shout AND the
gates, and sets Fusion app-wide (the tray menu renders the same under it); the
window is rebuilt on each open after a close, so the sounds page re-reads
config.json; and `Window.closeEvent` must close the lab's output stream, since
no process exit does it any more.

A left click on the tray icon copies the last dictation (private). Its notice
says a word count and never the words: Windows keeps past notifications in the
notification center. `_finish` adds to the list BEFORE `inject()`, so a paste
that raises or lands nowhere still leaves the words; `probe_app` asserts that
order, and moving the add after `inject()` fails exactly three of its rows.

Back-to-back dictations **join with a leading space**: `_finish` prefixes one when
the paste goes to the same foreground window as the last one AND
`HotkeyListener.keystrokes` has not moved since. That counter ignores modifiers
and the V of a Ctrl+V (Shout's own paste), so Enter or any typing makes the next
dictation stand alone. Shout cannot read the field's text, so a mouse click to
somewhere else in the same window still joins. The recent list keeps the words
without the space. `shout.log` marks a joined paste `(joined)`. `probe_app` covers all
six cases, and each of three mutations (never join, no counter, V counted) fails
only its own rows. Not yet seen in real use: whether Windows injects a key when
the chord's Win lifts, which would stop every join (the log would show none).

The lab drives `shout.cues.build()` directly and merges the
cue keys into config.json rather than rewriting it. A running Shout polls that
file every 500ms and follows the **cue keys only**, so a Save plays from the
next chord with no restart; every other key still needs a relaunch.

The six materials in `cues.PRESETS` are **read-only**. A voice you tune is saved
under a name of its own into `cue_presets`, so the material stays as shipped and
stays selectable; editing a material renames what you are editing, and saving
under a material's name is refused. `Voice.resolve()` is the single place that
turns config into a voice and the only thing both readers use — `__main__.py`
and `probe_cues.py`. `harness/probe_lab.py` drives the real `Window` against a
throwaway `APPDATA` and asserts the round trip: what the lab played is what the
app resolves. It also covers the recent page, and its layout sweep checks
wrapped text against `heightForWidth` at the current width, because a wrapped
label's `minimumSizeHint` is taken at some other width (17px for a label
needing 85).

**Re-run `harness/probe_cues.py` after saving a new voice:** that gate reads the
configured voice and asserts the cue stays inert in the transcript, which is a
property of the sound, not of the code.

## The orb

`overlay_style` in config.json picks the overlay's look, `pill` (the default) or
`orb`, switched live from the tray's **Appearance** menu or by editing the file.
`Overlay` owns placement, fullscreen hiding and keep-on-top for both; only the
widget differs (`PillWidget` / `OrbWidget`, whose flags both come from
`OverlayWindow`).

`shout/orb.py` is the GLSL from the voice-powered-orb React component, ported.
It renders in an offscreen OpenGL context to a QImage that the plain translucent
window paints, so none of the window's measured Win32 behaviour depends on how Qt
composites OpenGL into a layered window. That was a design choice: a
QOpenGLWidget inside the overlay window was never tried.

At rest the orb is a 40x10 glass capsule (a see-through body inside an opaque
rim) whose rim colour circles. It is the one resting look that animates, so
`OrbWidget.resting()` drops the overlay to its idle tick (~21 frames/s measured).
Measured 17 Sep 2026 on the running app, 30s each: 2.3% of one core idle on the
pill, 3.2-4.7% on the orb. Every expanded state inflates it to a 72px ring.

The ring is **water**, not geometry (Gabe's call, 17 Sep — the crest it replaced
was a raised sine to a power, and at its shipped spikiness of 3 it read as a
star, while turning that knob DOWN pinched the troughs into a flower).

There are **two waters**, and since 17 Sep they carry the SAME crest count (9):
what tells the states apart is the shape, not the number. `wave()` is holding —
one swell of K crests leaning the way it travels, its neighbouring counts K±1
beating against it so no two crests match, and ripples capped at `RIPPLE_K`
lobes so a high count cannot read as a gear. `ripples()` is hands-free — the
same swell carrying two octaves of smaller, faster waves, which reads as texture
rather than as height. They turn opposite ways, and hands-free also breathes.
`probe_orb` measures the separation as the strongest wave between three and five
times the crest count, where the swell has no term at all: 0.154 hands-free
against 0.049 holding, with the swell drawing hands-free as its control (0.053).

Three things about the maths are load-bearing and were each measured:

- **Both waves divide by the sum of their own terms**, so each peaks at exactly
  1 and `height` means the tip in px. Dividing by 0.6 of the sum, as `wave()`
  did until 17 Sep, let the terms stack to 1.23x wherever they lined up — a few
  crests standing a quarter taller than their neighbours, which IS a star's
  point, and is what Gabe was seeing at a raised voice. Fixing it took a
  quarter off every crest at every level.
- **`ripples()`' octaves run at 2K+1 and 4K+1, never 2K and 4K.** At the exact
  multiples they are harmonics: they lock to the swell, every crest becomes the
  same peaked waveform, and it measured 6.77 tip over trough where a sine
  measures 1.00. Offset by one they beat against the swell instead. Measured
  1.00 — rounder than the holding swell's own 1.64. **If you ever "simplify"
  those to round multiples, the star comes straight back.**
- **`voice_drive()` is gated then raised to `VOICE_CURVE` (0.40), not square
  rooted.** The curve decides how much of your voice lands at arm's length
  rather than at a shout, and it is the ONLY knob that does: flattening the
  crests and lowering `push` both take the loud end down and the response with
  it. Taking a quarter off the crests without moving the curve dropped ordinary
  speech from 1.52x to 1.36x, under the gate's floor. At 0.40 it reads 1.57x.

The voice both raises the waves (`push`) and roughens them (`ROUGH`), since
steepness is what turns a ring of waves into a star — most of a loud orb's
energy has to arrive as finer waves, not taller ones. Raising `ROUGH` does NOT
buy back volume response, measured: it moves energy from the swell into the
sidebands, so the dominant wave gets *smaller*.
Transcribing has no crests, and one comet goes round a dimmed ring. Motion per
state is the `MOTION` table; phases accumulate, so a change of speed never jumps
the picture. If OpenGL fails at start or mid-render, the overlay logs it and
draws the pill, and the tray ticks what is actually on screen.

Every knob is a field of `orb.Tune` (defaults = the shipped look, asserted
against the pre-Tune literals by `probe_orb`). The **Orb** page edits it with a
preview that is the overlay's own Renderer and Choreography, and Save merges
only the fields that moved as `orb_tune`, which a running Shout follows. Save
never switches the look (Gabe's call, 17 Sep); the page's On screen switch does,
by writing `overlay_style` and polling the file so a tray pick moves it. The
page reads back ONLY the slider that moved: the shipped spins are not on slider
steps, so reading them all back quietly changes the look (`probe_lab` row, with
that mutation as its control). The overlay window is no longer a fixed 96px:
`Tune.win` and `Tune.bottom_pad` are derived from the loudest frame the tune can
draw (96px and 19px shipped, from a `PEAK` of exactly 1), which also fixed the
glow clipping at meter 1.0,
and `Overlay.set_orb_tune` rebuilds the widget when the size changes.

`harness/probe_orb.py` pins fullscreen detection off, because its rest rows need
the orb on screen. It reads crests off the rendered ring (the most opaque radius
at each degree, then the strongest wave in that profile), so **re-run it after
changing `MOTION`, `VOICE_CURVE` or the `Tune` defaults**: the ripple separation,
"ordinary speech visibly raises the waves" at meter 0.15 (0.68 -> 1.07px holding,
0.88 -> 1.65px hands-free, measured 17 Sep), the roundness ceiling and the comet
are all claims about the SHIPPED numbers. Two of its floors are **fractions of
`Tune.height`** rather than px, and that is deliberate: the 17 Sep normalisation
made the same tune draw a crest 23% shorter by construction, so a px floor
measured on the old scale would have failed a ring exactly as wavy as before.
What stops those rows passing on a smooth ring is that transcribing, which has
no crests, reads 0.42px against their 0.56px floor — the two rows are pinned
against each other. A tune Gabe saves from the Orb page is his taste and
is deliberately not gated. It asserts the resting frame rate, not CPU: the probe process's
own CPU time read 9% at rest against 7% expanded, so it measures the harness.

## Invented trailing phrases

Whisper was trained on a lot of subtitled video, so audio it cannot resolve into
words decodes to the boilerplate those subtitle files end with. Reported from
real use on 20 Sep 2026 at roughly one dictation in six: a trailing "We'll be
right back" after the last real word.

**Trailing silence is not the trigger.** Measured the same day across 62 cases —
synthetic room tone at four levels, digital silence, and real recorded noise
floor lifted from the quiet stretches of `jfk.wav`, appended to speech at tails
from 0.3s to 6s and gains to 12x — the hallucination count was **zero**, because
`vad_filter` removes the tail before the decoder sees it (every segment ended at
~2.0s of a 3.2s+tail clip). So the usual fix, trimming silence before
transcribing, would do nothing here. It takes a real *sound*: a breath, a lip
smack or a chair creak clears the VAD, reaches the decoder carrying no lexical
content, and comes back as an outro.

`transcribe.assemble()` drops it. The inclusion rule for `_BOILERPLATE` is
deliberate and load-bearing: **a phrase earns a place only if it names watching,
listening, a channel or a video, or is a bracketed sound tag or an attribution
line.** A missing real word is invisible corruption; a surviving junk phrase is
visible and one keystroke to delete, and that asymmetry is what sets the bar.
Generic calls to action are excluded however often Whisper invents them — "stay
tuned", "please subscribe" and "see you next time" are real marketing copy Gabe
dictates for the shop. So are "Thank you", "Bye" and "You", which are the most
common Whisper hallucinations of all. The fused-match patterns are anchored to a
trailing sentence boundary so they can never cut into a sentence.

`transcribe()` also logs one per-segment line per dictation — `nsp`
(`no_speech_prob`), `lp` (`avg_logprob`), `cr` and a character *count*. Numbers
only, never words, so README's promise about `shout.log` holds even if the strip
misfires; a dropped phrase is logged as a pattern **index**, not its text. Those
numbers exist so a confidence-based filter can be tuned on measured data rather
than guessed at. Note faster-whisper's own `no_speech_threshold` cannot do this
job: it is applied per 30-second window, so real speech pulls the window average
up over a junk tail.

**The phrase list never caught the commonest one, "Thank you."**, because it is
excluded by design. Measured 29 Oct 2026 over 1,736 logged dictations: only 6
phrase drops fired, all "we'll be right back", while 62 dictations ended in a
short segment sitting **alone in a later 30-second decode window** at
`avg_logprob` -0.47 to -1.1 (mostly 10 chars, the length of "Thank you.", every
one from a clip over 29s). That is the mechanism: when a long dictation's last
real segment ends just short of the clip, Whisper decodes the leftover sliver as
a window of its own, and with no words in it, it invents one. `_invented_tail()`
drops that segment when it is in a later window than the text before it
(`Segment.seek`), at most 25 chars, and under `TAIL_MAX_LOGPROB` (-0.45). The
line sits in a gap: every short own-window tail kept is at -0.40 or better. A
real "Thank you." spoken at the end of a long dictation decodes confidently and
survives. Refuted as a second signal: "the segment ends after the audio
does", since 135 real segments overshoot the clip end. Note `lp`/`nsp`/`cr` in
shout.log are **per window**, not per segment, which is why they repeat across a
line. The 62 were never labelled against real text (the words are not logged),
so the drop log line, `dropped an invented final window (lp=… n=…)`, is the
evidence to re-tune from if a real word ever vanishes.

**Question marks** come from `shout/questions.py`, applied after `assemble()`.
Whisper punctuates from intonation, so a flat question gets a full stop; this
reads the wording (inverted auxiliary, wh-word plus verb, a tag such as
", right", or "can you" after a lead-in clause). "do"/"have"/"don't" count only
before a personal pronoun, so orders like "Do it now" stay statements. A
sentence ending in "!", "?" or an ellipsis is never overruled. Gate:
`harness/probe_questions.py`, mostly near misses.

**Run-on, unpunctuated output** is a decoding MODE Whisper picks per window, not
a function of pauses: the same text read flat came back as one sentence with a
single final period, while other dictations in the same minute were perfectly
punctuated. `PUNCTUATION_PROMPT` (an `initial_prompt` of short punctuated
sentences) tips it back. Measured 29 Oct 2026 on 24 SAPI clips of real dictations
read flat: run-on 24/24 with no prompt, 12/24 with the shipped prompt, sentence
ends 21 -> 55; "Hello. Welcome to my lecture." only reached 22/24. Zero leakage
on silence, room tone and a breath; words unchanged bar a hyphen. Two of the six
texts never flipped under any prompt, so the prompt is a ceiling of about half,
and the rest would need a punctuation model. `without_timestamps` made no
difference. It conditions only the first 30s window (`condition_on_previous_text=False`
resets the prompt), and `warmup()` exercises it since it calls `transcribe()`.

**Re-run `harness/probe_tail.py` after touching `_BOILERPLATE` or the tail rule.** It requires
every pattern to be exercised by a case — exact, not a floor, because a floor
let 8 of 13 patterns ship untested on its first run. It needs no GPU.

## Commit guard

`hooks/pre-commit` refuses to commit recordings, transcript-shaped files, or any
staged blob over 1MB. It is version controlled rather than living in `.git/`, so
it is wired up with one command that **a fresh clone must run**:

```bash
git config core.hooksPath hooks
```

The gitignore is still the first line and still correct; the hook exists because
a gitignore is passive — it does not stop `git add -f`, and it does not stop a
recording that lands somewhere it was not expected. `harness/probe_repo.py`
proves it blocks, in a throwaway repo, with both controls: ordinary source must
still commit, and `audio/jfk.wav` must still be allowed. `--no-verify` skips it,
as it skips every hook; this stops accidents, not authors.

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

Observed 8 Sep 2026: leaving Shout running fails the **inject** gate specifically.

`probe_overlay` also moves the real mouse pointer to the centre of every screen,
~0.2s each, and puts it back, because since 10 Sep the pill follows the mouse's
monitor rather than the focused window's. Keep hands off the mouse while the
suite runs: a failing `screen N: the cursor could be moved there` row means
something else moved the pointer, not a placement bug. Only a screen WITHOUT
the focused window can tell the two rules apart, so one monitor prints SKIP.
It then puts a black fullscreen window, without focus, on that screen for ~2s,
to check the idle pill hides there and only there.

## Gotcha: run the gates from a real terminal, not a detached launcher

`probe_hook`, `probe_stuck` and `probe_overlay` all depend on the launching
process holding Windows **foreground/input rights**, which Windows grants to a
child of the foreground process. Run from the PowerShell tool via `& python
gates.py` the same tree reported **6/8** — the hook gate lost an assertion and
the overlay gate reported INCONCLUSIVE because its focus-theft control could not
demonstrate theft. Run from Bash, the identical commit reports 8/8. Nothing was
wrong with the code either time.

Two consequences. A 6/8 with those specific gates failing means *check how you
invoked it* before believing a regression. And the overlay gate failing safe —
INCONCLUSIVE rather than a false PASS — is the control doing its job, so do not
"fix" it by loosening the control.

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
- [2026-09-08] Tk overlay, four traps found by probing before writing any of it.
  (1) **`deiconify()` calls `SetForegroundWindow` unconditionally**, so it steals
  focus even with `WS_EX_NOACTIVATE` already applied before the first map — which
  would break dictation with the app's own UI, on an app that autostarts. The
  window is therefore created withdrawn and NEVER deiconified; visibility is
  `ShowWindow(SW_SHOWNOACTIVATE / SW_HIDE)` directly, and Tk keeps laying out and
  repainting perfectly well while believing it is withdrawn. (2) The FIRST
  `-alpha` or `-transparentcolor` call **wipes GWL_EXSTYLE**, because Tk rewrites
  it from its own cached copy when making the window layered; later calls are
  harmless. Set both during construction, before the flags, and re-assert the
  flags anyway. (3) `winfo_id()` is NOT the top-level hwnd even with
  `overrideredirect(True)` — Tk still wraps it, so every Win32 call needs
  `GetAncestor(GA_ROOT)`. (4) Tk **defers a geometry request on a window it
  thinks is withdrawn** until idle tasks run, so `_reposition()` must call
  `update_idletasks()` or the pill silently stays at 0,0 — which is exactly how
  it first failed. Use `MonitorFromWindow` + `rcWork`, not `SPI_GETWORKAREA`:
  the latter is the primary monitor's work area only and misplaces the pill on a
  second screen.
- [2026-09-08] The handoff's audio-cue mitigation ("play the cue before opening
  the mic; device start costs 50-200ms, which the cue spends being over") is
  **refuted**: `recorder.start()` costs 14-22ms on this machine, so the ordering
  buys ~15ms of a 112ms cue and the cue lands in the capture at ~130x ambient.
  The first gate I wrote asserted a band-energy ceiling and failed at 48000x —
  correctly measuring bleed, while asserting the wrong property. What actually
  matters is the transcript, and there the bleed is inert: bleed-then-speech is
  byte-identical to the same speech alone, and the bleed alone transcribes to ""
  because the VAD rejects a pure tone. `probe_cues.py` now asserts that, gated on
  a loud reference tone proving the speaker-to-mic path is live — without that
  control, headphones would make both assertions pass trivially. **If the tones
  are ever changed, re-run it**: the property being relied on is "not
  speech-like", which a longer or more complex cue could break.
- [2026-09-08] Two harness bugs that had nothing to do with the feature. A probe
  printing an em-dash in its LAST line killed `gates.py` with a cp1252
  `UnicodeEncodeError` *after every gate had already run* — the runner prints
  only that line, so no other probe had ever triggered it. And `gates.py` had no
  per-gate timeout: the positive control for the shutdown assertion (removing
  `tray.stop()`) hangs `probe_app` forever on pystray's non-daemon thread, which
  would have hung the whole suite with no output. Both fixed; the suite now
  forces utf-8 in both directions and reports a `TIMED OUT` row. Partial stdout
  from a killed child is still usually lost on Windows even unbuffered, so the
  timeout row has to name its own likely cause.

- [2026-09-08] A dead low-level hook cannot report itself: `listener.running` stays True
  because the pynput message loop is alive, it just never gets called. The detector has
  to observe the same reality by a different route — the watchdog's 20Hz
  `GetAsyncKeyState` poll seeing the chord held while the gesture machine reports
  nothing. If hotkeys ever "just stop working", check `shout.log` for reinstall lines
  before suspecting pynput.

- [2026-09-08] Shout kept dying because a Windows Terminal window titled "Shout"
  was open and Gabe kept closing it, reasonably assuming it was scaffolding.
  Cause: **uv builds a venv from trampoline shims and installed the CONSOLE one
  under both names** - `.venv/Scripts/pythonw.exe` is byte-identical to
  `python.exe` (same md5) and its PE subsystem is 3, not 2. So the "pythonw: no
  console window" comment in the installer was false: the trampoline allocated a
  conhost and spawned the base console `python.exe` as a child, and closing the
  terminal killed that child. Fix: `install_shortcuts.ps1` now installs
  `.venv/Scripts/shoutw.exe`, a copy of the real GUI-subsystem `pythonw.exe`
  with `python3*.dll` beside it, and asserts subsystem 2 both before and after
  copying. Python finds the venv from `pyvenv.cfg` one directory up and does not
  care what the exe is called, so `sys.prefix` still resolves to `.venv`. A
  separate name, not an overwrite: uv's trampoline is locked while Shout runs
  and `uv sync` would restore it anyway. Those files are gitignored, so
  **re-run the installer after any venv rebuild**. -> Never trust an
  interpreter's NAME to tell you whether it opens a console; read the PE
  subsystem (`e_lfanew` at 0x3c, subsystem at +0x5c). Corollary that cost a
  round trip here: the installer first verified the fix by capturing the new
  exe's stdout, which is always empty precisely BECAUSE it is GUI-subsystem - a
  check that passes by measuring nothing. Verify a windowed process through a
  file it writes, never through its pipeline output.

- [2026-09-08] Ported the pill and the tray from Tk+pystray to Qt. Three of the
  four Tk traps in the note above simply do not exist on Qt, measured in one
  recon probe before a line of it was written: `winId()` IS the top-level hwnd,
  `WA_ShowWithoutActivating` means `show()` never activates, and
  `WA_TranslucentBackground` is real per-pixel alpha rather than a chroma key —
  which is what the old jagged corners and missing shadow actually were. Qt's
  window flags produce WS_EX_NOACTIVATE / TOOLWINDOW / TRANSPARENT unaided; the
  gate still reads the bits off the real window, because a future Qt changing
  that mapping would present as dictation silently going nowhere. **Do that recon
  probe first on any toolkit swap** — it converted every "will Qt do X" into a
  measured yes/no for the cost of one call, including that Qt and Win32
  coordinates agree exactly here (dpr 1.0 on both monitors) and that CTranslate2
  loads fine in a Qt process.
- [2026-09-08] `QApplication.quit()` called from a WORKER thread does not end the
  event loop — `app.exec()` blocks forever, with no error. Use
  `QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)`.
  Same family: any `stop()` that a worker thread might call must only set a flag,
  with the real widget teardown deferred until after `exec()` returns, because a
  QWidget or QTimer touched from the wrong thread does not raise — it corrupts
  quietly. `probe_app` quits from a worker deliberately so this stays caught.
  **The in-probe watchdog did NOT fire on that hang**: the wedged thread held the
  GIL, so no other Python thread could run. A watchdog thread only covers hangs
  that release the GIL — `gates.py`'s subprocess timeout is the real backstop,
  and that layering is the point, not redundancy.
- [2026-09-08] Ran a long probe as `python -u probe.py | tail -40` and read the
  empty output as "the probe produced nothing", then spent two round trips
  hunting a hang that was in a completely different place than I thought.
  `tail` emits nothing until EOF, so piping a still-running process through it
  hides every line it has already printed. → Never pipe a probe you are waiting
  on through `tail`/`sort`/`wc`; redirect to a file and read the file. Related to
  the 2026-08-31 note about CLIs changing their output when piped, but a
  different mechanism: here the output was correct and simply withheld.

- [2026-09-08] The pill's dimensions are DERIVED, not typed in. Height is the one
  number; bar height, dot radius and halo are fractions of it, and the expanded
  width is the sum of the parts a state actually draws (dot inset + label gap +
  measured label + meter gap + 122px bar span + right inset). That is why
  deleting the word "Recording" shortened the recording pill from 268 to 174px
  by itself. Label widths come from `QFontMetrics` on the real font — never
  estimate one, it picks the width and a wrong guess clips a word with nothing
  to trace it to. The taskbar gap is height-independent for free: placement
  derives the window top from its height, so the pill shrinks upward from a
  fixed bottom edge. If you change HEIGHT, change nothing else.
  [2026-09-10] The idle pill is now its OWN shape, `COLLAPSED_W x COLLAPSED_H`
  (52x10, `IDLE_DOT_R = 0`, no dot), not a narrower full-height lozenge: height
  eases from 10 to HEIGHT on the same curve as width, bottom edge fixed, and the
  dot grows in from nothing. `probe_overlay` measures both heights and the shared
  bottom row off the rendered pixels, and asserts the body is neutral black
  with the old `(20,20,26)` body as its positive control (spread 5 vs 0).
  **Check the foreground before running the gates**: under a D3D-fullscreen
  game the two "idle/loading visible" rows fail by design (the pill hides), and
  the focus-theft control pulls focus out of the game for half a second.
- [2026-09-08] `probe_cues` reads the CONFIGURED voice, not the default — its
  band, note offsets and duration bounds are all derived from it. That is
  deliberate: the property it protects (the cue is inert in the transcript)
  belongs to the sound, so a gate pinned to the default would stay green while
  the app played something the VAD might not reject. **Re-run it after every
  save from the cue lab.** All six shipped presets are harmonic for the same
  reason; a noisy one (Tick, anything breathy) can break the property, and the
  failure is a stray word in your dictation, not a gate error.

- [2026-09-08] `probe_cues` reports **SKIPPED — control tone not heard** on this
  box whenever the default input is the wireless *Headset Microphone* while cues
  play out of *Speakers (Realtek)*: the two are not acoustically coupled, so the
  loud reference tone measures 1.00x ambient and the gate correctly refuses to
  claim the bleed assertions passed. That is the control working, not a
  regression — a SKIPPED verdict means the inertness of the configured voice is
  **unverified**, and 40/40 on the other rows does not cover it. To actually
  close it, point `output_device` at the headset (or select a mic that can hear
  the speakers) and re-run. Worth knowing before reading a SKIPPED as a pass.
- [2026-09-08] The cue lab's Delete left `cue_preset` naming a voice it had just
  removed, because it read the active name off `self.cfg` — a `Config` snapshot
  taken in `__init__`, which goes stale the instant the window's own Save writes
  the file. Caught by `probe_lab` on its first run. → In any editor window that
  both reads and writes a config file, a snapshot is only good for the initial
  render; every later question about what is CURRENTLY configured has to re-read
  the file. `self.voice.name` was the other tempting answer and is also wrong —
  deleting a voice you are merely *looking* at must leave the active one alone,
  which is now its own gate row.
- [2026-09-08] Restyled the cue lab to the Vendor Vault design language (Ember
  Dusk). Three Qt-specific findings, all found by measuring rather than looking.
  (1) **`QSlider:focus::handle:horizontal` is MISPARSED by Qt** — the form the
  docs imply, state before subcontrol, silently applies its declarations to the
  QSlider WIDGET, so a `border` there paints a box around every slider whether
  focused or not; stacked over eight rows it reads as a grid drawn on the panel.
  `QSlider::handle:horizontal:focus` is correct. `probe_lab` now lints the whole
  sheet for state-before-subcontrol — and its first version failed on the
  COMMENT that explains the bug, so it strips `/* */` before matching.
  (2) **A screenshot cannot see a window sized under its layout minimum.** At
  600x736 the lab looked perfect and twenty widgets were rendering 4-5px under
  their `minimumSizeHint` — legible at this DPI, clipping at another. Size a
  window FROM `sizeHint()`, never from a number that looked right, and sweep
  `child.height() < child.minimumSizeHint().height()` with the window mapped
  (`WA_DontShowOnScreen` + `show()`; an unmapped widget reports every child
  invisible, so the sweep passes by measuring nothing). Qt clamps a top-level to
  its layout minimum, which is why the control for that sweep needs
  `setFixedHeight` — `setMinimumSize(0,0)` does not stick, the layout re-imposes
  its own minimum on the next activation.
  (3) **VV's `--vv-text-muted` (#8a6f5a) fails WCAG AA on every surface here**
  (2.78-3.91:1) and `--vv-negative` fails on card fills. Ran the contrast sweep
  before writing any colour down, per the 2026-08-21 rule; labels use
  `--vv-text-secondary` instead and #8a6f5a survives only as the disabled colour.
  The gate asserts the floor with VV's own token as the negative control.
  Not verified: the combo popup's styling — `view().grab()` returns blank
  because the view only renders as a real popup, and showing one would put it on
  the user's desktop.

- [2026-09-09] Cues stayed on the speakers after a Windows switch to headphones,
  while the lab stayed on the headphones after a switch back. Cause:
  **PortAudio's `device=None` is an MME device NUMBER, and Windows renumbers
  MME devices so that 0 is always the current default** (`waveOutGetDevCapsW`
  before and after a switch), while PortAudio keeps the name it cached at init.
  So a stream OPENED after a switch lands on the new default under a stale name,
  and a stream already OPEN never moves. Cues and the lab each hold one output
  stream for their lifetime, so each stayed wherever the default was at launch.
  Fix: `shout.devices.follow_default()` opens the **MME Sound Mapper** (first
  MME device of each direction, found by position because the name is
  localized), which Windows re-routes live, both ways. `Cues` and `Recorder`
  both use it. Corollary: an explicit MME index in config is no pin either;
  after a switch, the index labelled "headphones" can open the speakers.
  `harness/probe_route.py` asserts it with a REAL switch of the default output
  AND input, restored in a `finally`, so **running the gates moves your audio
  and mic for ~3s**. Instruments: the output session peak meter per endpoint (a
  session lingers on every endpoint ever used, so only a non-zero peak counts),
  and for input the session STATE (a quiet room reads peak 0 either way).
  Positive control: a concurrent child holding the pinned pre-fix streams,
  which must stay behind. **The correction that matters:** I told Gabe the
  per-chord mic "won't follow a mic switch until restart". I had inferred that
  from PortAudio's source, which caches the default at init, and never tested a
  stream opened after a switch. The subject-side control gave FAIL 14/18, and
  the per-chord mic row was one of the rows that did NOT flip: the old code
  already followed there, via the renumbering. -> **When running a positive
  control, read WHICH rows flip, not just the verdict**; a row that passes on
  the pre-fix code falsifies your model of the bug on that path. Smaller traps:
  `Popen.pid` of the venv `python.exe` is uv's trampoline, so a child measured
  by PID must report `os.getpid()` itself (empty BASELINE rows were the tell);
  and the per-app audio PropertyStore in the registry holds an entry for every
  exe that ever played anywhere (375 here), so its entries are not overrides.

- [2026-09-10] Three cue-lab bugs, one of them a missing feature. (1) **Save
  "did nothing"**: it wrote `cue_preset` correctly, but `Shout.__init__`
  resolved the voice once and never re-read the file, while the lab's status
  line told Gabe to relaunch, which nobody reads. The log showed the app still on
  the old voice 20 minutes after the save. Now `watch_config()` polls the mtime
  on the GUI thread and re-applies the cue keys. It uses `Config.read()`, not
  `load()`: a file caught mid-write reads as all defaults under `load()`, and
  the control that swaps it back put the running app on 'blip' at 0.25. (2) **A
  click on a slider stepped 1%**, and whether a QSlider jumps is a property of
  the STYLE: `SH_Slider_AbsoluteSetButtons` is Left under Qt's default
  `windows11` style and Middle under Fusion, which the lab sets. `probe_lab` had
  been building its own bare QApplication, so it ran on windows11 and would
  have passed a jump the real lab never made. Caught by an in-probe control, a
  stock QSlider under the same sheet, which jumped too. Now
  `cue_lab.application()` (now `window.application()`) builds the app for both. The same mismatch means the
  layout sweep had been measuring windows11 geometry, not the lab's.
  (3) **The drag screech**: valueChanged per pixel, and `Cues.play()` restarts
  by design. `Slider.held` gates the audition to one play on release. It has to
  be a flag set before the press is handled: the jump emits valueChanged from
  inside the press, before `sliderPressed`, so `isSliderDown()` is still False
  there. Smaller: a status message that wraps to a THIRD line does not grow the
  window (the top-level minimum ignores heightForWidth), so the slider card
  loses 15px. That was pre-existing; it surfaced because the new rows left a
  long temp path on screen. The save message no longer carries the path, but a
  40-character voice name can still reach three lines. -> Before asserting
  anything about widget BEHAVIOUR in a probe, check that the probe builds the
  app the way `main()` does. I had read both and not connected them, and it was
  the stock-widget control that paid for it.

- [2026-09-10] The pre-open-source sweep found **the clipboard restore leaking
  private copies**. `_restore_later` put the previous clipboard back with
  `private=False`, so a password a manager had copied private (kept out of Win+V
  history and the cloud clipboard) came back as plain text 300ms after every
  dictation. The write path had carried the markers from day one, which is
  exactly what made the restore path easy to miss. Restore is now
  `private=True`, which costs nothing, since the original copy already made its
  own history entry if it was allowed one. `probe_inject`'s own cleanup had the
  same bug. The gate copies its sentinel private and asserts the marker
  survives; swapping in the pre-fix `inject.py` fails exactly that row, 10/11.
  -> Anything that borrows the clipboard must hand it back with the privacy it
  arrived with. Audit the restore path, not just the write.

- [2026-09-10] **The pill "hid behind windows"** an hour after launch, and
  Shout was not the cause. A z-order walk (`GetTopWindow` + `GW_HWNDNEXT`,
  reading `WS_EX_TOPMOST` off each window) put it at z=35 under two VS Code
  windows **with its topmost bit still set**. It was not alone: 22 topmost
  windows from four processes, explorer's own `Shell_SecondaryTrayWnd` among
  them, had been demoted below normal windows as one block. All were in band 1
  (`GetWindowBand`), so nothing structural blocks recovery, and
  `SetWindowPos(HWND_TOPMOST)` on the live pill moved it from z=35 to z=6
  without touching the foreground. `_keep_on_top()` now does that on the 0.5s
  fullscreen poll, but only when a visible, uncloaked, same-band, non-topmost
  window is above the pill, so it never fights other topmost windows for the
  top slot. It logs once per burial with the class and pid, never the title.
  Two traps: the bit cannot detect this (it stayed set the whole time), only
  the walk can; and `SetWindowLong` cannot set `WS_EX_TOPMOST` at all. The probe
  stages the burial with `HWND_NOTOPMOST` plus a plain window on top, because
  no public call reproduces bit-set-but-buried; its control switches recovery
  off and requires the pill to stay buried. **The cause is still unknown**, and
  `shout.log` now names the window on top each time it happens, so read that
  before theorising.

- [2026-09-10] **The fullscreen hide never worked on the second monitor.** It
  was `SHQueryUserNotificationState` alone, and a staged fullscreen window on
  the left monitor got ACCEPTS_NOTIFICATIONS (5) back, whether it was a Qt
  `showFullScreen()` or Chrome's `--start-fullscreen`, and even while it had
  focus. Nothing had caught it: the policy rows set `_fullscreen = True`
  directly, and the one real test was a game. Whether it answers BUSY on the
  PRIMARY monitor is untested. It now decides only the global states (D3D
  exclusive, presentation mode). `fullscreen_monitors()` says WHERE: per
  monitor, the first window in z-order that isn't tool, click-through,
  no-activate, cloaked, minimized or the wallpaper (Progman/WorkerW) must
  cover the whole `rcMonitor` with no WS_CAPTION. Measured: fullscreen Chrome
  is exactly the monitor rect with neither WS_CAPTION nor WS_THICKFRAME.
  Maximized Chrome and VS Code keep both, and overhang the monitor by 8px on
  three sides but stop at the taskbar. That is why the caption test exists: on
  a taskbar-less monitor the overhang covers everything. Two ordering rules
  keep the pill from flashing on a fullscreen screen: the mouse's monitor is
  compared against the cached set **every frame** (a 0.5s poll showed it there
  for up to half a second), and `_reposition()` runs **before** `show()`.
  Controls: the old global check fails exactly the two hide rows, and a
  mouse-monitor lookup cached for 0.5s fails only "never shown there on the
  way". -> Before trusting an OS state query for a behaviour, stage the exact
  case and read the query's own answer. A row that sets the flag by hand can
  never notice a query that doesn't fire.

- [2026-09-10] Raising the pill's rim from white at 10% (alpha 26) to full
  white exposed a leak that had always been there. `paintEvent` never reset the
  pen after stroking the rim, so the halo and the dot were outlined with it too,
  invisible at 10%. At full white they got a white ring. `setPen(NoPen)` now
  follows the rim. -> Before making a translucent paint value opaque, check
  what's drawn after it: a faint value can hide painter state leaking into
  later shapes.
  [2026-09-12] The rim is now **45% white** (alpha 115); the body stays 75%
  opaque. Dimming it blinded three `probe_overlay` measurements that had been
  calibrated at full white. 45% white over the 75% body over the probe's grey
  128 reads 132-133, so `body_rows`' "differs from grey by 40" test lost both
  rim rows and measured the idle sliver at 8px, not 10. A 45% ring over the red
  dot is nowhere near white, so the near-white ring test went blind, and the
  ">= 250" rim test would simply have failed. Painted rows now come from the
  ALPHA of a render onto nothing (220 on a rim row, 191 on the body). The rim's
  opacity is `(on - under) / (255 - under)` against the same pixel rendered
  with the rim at alpha 0 (0.448-0.453 depending on `pill_height`'s 0.01px
  dead-band, so the row allows +-0.02), and the ring row diffs the dot's
  neighbourhood against that rim-free render. Controls: the old solid rim fails
  only the rim row (0.987), and dropping the `setPen` fails only the ring row
  (51 pixels). -> A test that detects a paint value by threshold is calibrated
  to that value. Measure against a render without it instead, and the test
  survives the next tweak.

- [2026-09-12] **The pill never showed Transcribing in real use.** `_finish`
  set "working" once, but the watchdog calls `_dispatch()` on every 20ms TICK,
  `_dispatch()` always ends in `_publish_state()`, and `_tray_state()` had no
  term for a commit in flight. The pill went back to idle one tick into every
  commit, held or latched: a 194s passage spent 2.62s transcribing behind the
  idle sliver. Now `_queue_commit()` takes a count under `_state_lock`,
  `_finish`'s `finally` releases it, and `_tray_state()` says "working" while
  it is non-zero (capture still wins). No gate could see it: `probe_overlay`
  sets "working" by hand, and `probe_app`'s stub transcriber returned at once,
  so no commit was ever in flight across a tick. `probe_app` now holds its stub
  transcriber open, sends TICKs through `_dispatch` exactly as the watchdog
  does, and asserts the pill is 'working' at its 251px width after an unlatch
  and after a hold. Control: the committed `__main__.py` fails exactly those
  two rows (pill 'idle' at 52px) and nothing else. -> A state written once by a
  worker lasts only until the next republish. Derive it where the republisher
  looks, and give the gate a slow stub, because a fast one never overlaps.

- [2026-09-16] **The cues went silent and the cause is still unknown.** The
  running Shout had NO render session on any active endpoint, while a fresh
  process opening `Cues` the same way played on the default (the control).
  Refuted, each measured with a control: a default switch, disabling the
  endpoint the stream is on (Windows migrates it), a PS4 controller's USB audio
  arriving and leaving (`harness/probe_hotplug.py`: CONTROL kept playing), a
  second `Cues` opened and closed in the same process (the cue lab), and sleep
  (none during that process's life). Not examined: game launches (Auto HDR
  notices at 11:47:41 and 12:29:32) and HDMI endpoints flickering at monitor
  wake. `Cues` now runs a watcher thread that reopens the stream when
  `devices.default_output()` changes, or when the stream stops or stalls. It is
  proven on an aborted stream (reheard within 0.5s, while the no-watcher
  control stays silent) and did not misfire through a 20s GPU warmup. It is
  **not** proven against the real failure, whose signature is unknown. **When
  the cues go silent again, do not restart Shout first:** read its sessions
  (`probe_route.sessions(pid, RENDER)`), grep `shout.log` for `reopening`, and
  run `uvx py-spy dump --pid <shoutw pid> --native` (verified to attach here) to
  see whether the PortAudio thread is gone, blocked or still calling back.
  Restarting destroys the only evidence; that is exactly how it was lost this
  time, since the process had been quit before it could be dumped. Separately,
  **the controller takes the default MIC** (its earphone endpoint was already
  disabled). The endpoint is now disabled with
  `IPolicyConfig::SetEndpointVisibility`, which works unelevated, even on a
  not-present endpoint, and survived a replug with both defaults untouched.
  Endpoints are per USB port, so another port may bring it back. Harness traps
  from the same run: a child's first stdout line was a `cues ready` log line,
  not its PID, so the parent measured pid 230; a kill by path regex missed the
  parent launched with a relative path; and -48dBFS blips were plainly audible
  over minutes of waiting, while -90dBFS still reads on the session meter.
- [2026-09-17] **Smart App Control blocked a .pyd at boot and Shout came up
  with no model.** `av/video/stream.pyd` is unsigned, SAC (enforcing on this
  box, `VerifiedAndReputablePolicyState=1`) judges an unsigned file by cloud
  reputation, and Defender was still updating: its first successful cloud
  lookup was 10:38:31, the block 10:37:12. The same file loaded fine three
  minutes later, so it was transient. The tray, orb and hotkey all came up
  normally — only `_load_model` failed, with `ImportError: DLL load failed
  while importing stream: An Application Control policy has blocked this file.`
  Diagnose from the CodeIntegrity log, event 3077, which names the file and the
  process; the SAC state is that registry value. `_load_model` now retries a
  block over 5 minutes (`BLOCK_RETRY_S`) and reports anything else at once, as
  before. Two measured findings behind it. (1) **Retrying in the same process
  works, but only after forgetting the pure-Python modules the failed attempt
  left behind**: a package re-run on retry finds its submodules cached and
  never binds them to itself, so asyncio dies on `base_events` not defined.
  Compiled modules must STAY — Cython refuses to initialise one twice. (2) The
  blast radius is 58 unsigned compiled modules, stdlib included (uv's Python is
  unsigned too); `harness/probe_load.py` fakes a block where Python's own loader
  raises one, and the sweep over all 58 is 50 blocked-and-recovered + 8 optional
  accelerators whose libraries fall back silently. The classifier is anchored on
  the line copied out of shout.log, never on the injected text. **Still
  uncovered:** a block on anything imported at startup (PySide6, numpy,
  sounddevice) kills the process before the tray exists, and retry-in-place
  cannot help there.
- [2026-09-17] **An F11 editor fails rows in two gates at once.** With a
  `Chrome_WidgetWin_1` window (VS Code or Chrome) fullscreen on the monitor the
  mouse was on, `probe_overlay` went 63/70: its `idle` and `loading` persistence
  rows plus the five fullscreen-staging rows, which found that monitor instead of
  the staged one. `probe_app` failed its "nothing is fullscreen" precondition and
  the two rows behind it. Neither was a regression, since the pill hides at rest
  on a fullscreen screen by design: `fullscreen_state()` named the 3440x1440
  monitor, and the foreground window covered it exactly. It was proven by
  re-running the persistence rows with detection pinned off (all five states
  visible), beside a forced-fullscreen control (exactly `idle` and `loading`
  hidden). -> Before reading any
  overlay or app gate failure, run `fullscreen_state()` and `cursor_monitor()`
  once, and ask for the editor to leave fullscreen if they match.

- [2026-09-18] **Smart App Control blocked a second file at boot, and the retry
  built for it did not fire.** This time `ctranslate2.dll`, blocked on the
  `ctypes.CDLL` in `ctranslate2/__init__.py`, with a Windows "bad image" dialog
  (status 0xc0e90002) beside it; CodeIntegrity 3077 named the file at 10:53:39.
  `_load_model` reported at once instead of retrying, and `shout.log` has no
  `retrying in` line to show for it. Cause: **Windows' message for error 4551
  reaches Python in two spellings, and the classifier held the longer one.**
  `ctypes.FormatError(4551)` keeps the trailing period FormatMessage returns,
  while CPython strips trailing dots when it builds an `OSError` -- so the
  constant ended in `file.` and the logged `OSError` ended in `file`, and the
  substring test was False. The 17 Sep block arrived as an `ImportError`, which
  keeps the period, so the gate row anchored on that line passed throughout.
  `BLOCKED_BY_WINDOWS` is now `.rstrip(" .")` and `probe_load` carries both
  observed lines; the pre-fix constant fails the new row and only that row. The
  retry would have covered this: a fresh process loaded the same DLL 17s after
  the give-up. -> A classifier anchored on ONE real log line is only proof
  against the wording you have already seen. When the same OS condition can
  surface through two Python layers (import machinery vs `ctypes`, an exception
  vs a returned code), anchor on the shortest stable core of the message, and
  keep a row per observed spelling. Punctuation is not stable across those
  layers; the words are.

- [2026-09-20] **Smart App Control blocked `.venv/Scripts/python.exe` itself**,
  for a whole session, and it never cleared (three retries over ~20 minutes).
  This is a larger blast radius than the 17 and 18 Sep notes: not a `.pyd` or a
  DLL loaded *by* Python, but the interpreter, so nothing in `_load_model`'s
  retry can help and every gate invocation in CLAUDE.md's Commands block is
  dead. The running Shout was unaffected throughout — it had already started,
  and it runs `shoutw.exe`, a *different* file. That is also the way out:
  `shoutw.exe` is a copy of the real GUI-subsystem `pythonw.exe`, it sits in
  `.venv/Scripts` so it still resolves the venv from `pyvenv.cfg`, and SAC
  judged it separately. Cost: it is GUI-subsystem, so `print()` goes nowhere —
  a script driven this way must open its own output file and point `sys.stdout`
  at it, and PowerShell's `-RedirectStandardOutput` works for the ones that
  don't. → When the venv interpreter is blocked, do not wait it out and do not
  conclude the work is impossible; re-run through `shoutw.exe` and have the
  script write its own file. Worth noting the asymmetry for a future session:
  the app survives a block that makes the whole test suite unrunnable, so
  "Shout is fine" is not evidence that the toolchain is.

- [2026-09-20] Reproducing the trailing-hallucination with SYNTHETIC audio was
  a dead end, and the dead end was the finding. 62 cases of gaussian room tone
  and real recorded noise floor appended to speech produced zero hallucinations,
  because `vad_filter` cut every tail off before the decoder saw it — the
  segments all ended at ~2.0s of a clip up to 9s long. Two lessons. The obvious
  fix for a reported bug can be aimed at a mechanism that provably isn't
  running: "trim the trailing silence" would have shipped, changed nothing, and
  looked like a fix until the next report. And a probe that cannot reproduce a
  user's bug is not evidence the bug isn't real — it narrows the cause, which
  here moved it from "silence" to "a real sound that clears the VAD", and that
  is what decided the design. State the negative result as a result.
- [2026-09-08] A warmup step ran the model with `vad_filter=False` while production runs with it on. CUDA kernels were built and the model was resident, so warmup "worked" - but the VAD is a SEPARATE ONNX session with its own first-call cost, so the first real dictation of every session paid 644ms against a 230ms median. Invisible to every gate, because the thing being warmed and the thing being measured were different code paths. -> Any warmup, preload, prefetch or cache-priming step must execute the exact call production executes, flags included; route it through the same function rather than reimplementing a convenient approximation of it. Second lesson from the same session, same shape of invisibility: never name an attribute `_stop` on a `threading.Thread` subclass - it shadows `Thread._stop()`, which `join()` calls internally, and shutdown dies with `TypeError: 'Event' object is not callable` only when something finally joins. Before naming private state on any framework base class, check what the parent already owns.
- [2026-09-08] A probe reported `state=pending_latch` and I nearly rewrote correct code - the gesture machine was legitimately waiting out a 350ms double-tap window and the probe had only run for 300ms. The assertion was wrong, not the system. Related in the same run: a harness detail string announced "resampled from 44100Hz" when PortAudio had actually accepted 16kHz directly, i.e. it reported the path it ASSUMED rather than the path it TOOK. -> When a harness fails, diagnose before patching the subject, and prefer assertions about the property that matters ("it resolves with no further input") over a snapshot of internal state at an arbitrary instant, which is timing-coupled to the harness's own sleeps. And make every harness print the branch actually taken, measured from the object under test - a confident wrong label in green output gets believed for sessions.
- [2026-09-08] Wrote a gate asserting "every note is in tune" that compared the measured pitch against the same helper the synth builds from (`notes()`). A deliberate 20% detune inside that helper slipped past all 26 assertions — the expectation moved with the bug. Two rows anchored on the values the CONFIG declares (the voice's root, the gesture's ratio) caught it instantly. This is the 2026-08-26 "don't restate the constant you are checking" rule one level up: it is not enough to avoid retyping the number, the check must not READ it from the code path under test either. Ask of every assertion: if the thing I am testing were wrong, where would my expected value come from? If the answer is "the same function", the check is decorative. Second, cheaper lesson from the same session: a multi-replacement patch script must `assert s != before` PER replacement — one of five silently no-op'd on a typo in its `old` text and I only found it by reading the file back, and a single assert at the end would have passed on the other four.
