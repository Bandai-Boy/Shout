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
.venv/Scripts/python.exe scripts/bench.py                    # throughput benchmark
.venv/Scripts/python.exe scripts/gpu_probe.py                # CUDA + compute-type smoke test
.venv/Scripts/python.exe scripts/transcribe_meeting.py <file>  # meeting pipeline
.venv/Scripts/shoutw.exe  scripts/cue_lab.py                 # audition the cue sounds
```

Normally you never type that: **right-click the tray icon -> Cue sounds...**
`shoutw.exe` for the lab, not `pythonw.exe` — see the 8 Sep lab note on uv's
console trampoline. The lab drives `shout.cues.build()` directly and merges the
cue keys into config.json rather than rewriting it. A running Shout polls that
file every 500ms and follows the **cue keys only**, so a Save plays from the
next chord with no restart; every other key still needs a relaunch.

The six materials in `cues.PRESETS` are **read-only**. A voice you tune is saved
under a name of its own into `cue_presets`, so the material stays as shipped and
stays selectable; editing a material renames what you are editing, and saving
under a material's name is refused. `Voice.resolve()` is the single place that
turns config into a voice and the only thing both readers use — `__main__.py`
and `probe_cues.py`. `harness/probe_lab.py` drives the real `Lab` against a
throwaway `APPDATA` and asserts the round trip: what the lab played is what the
app resolves.

**Re-run `harness/probe_cues.py` after saving a new voice:** that gate reads the
configured voice and asserts the cue stays inert in the transcript, which is a
property of the sound, not of the code.

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
  `cue_lab.application()` builds the app for both. The same mismatch means the
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
