# Handoff — 8 Sep 2026

## Session 1 is complete

Shout works. Hold Ctrl+Win, speak, release, text appears at the cursor. Double-tap
to latch hands-free, tap again to end. All six done-criteria are measured, the
manual real-app pass passed, and nothing is outstanding from the plan.

Full results are in [PLAN.md](PLAN.md); conventions and gotchas in
[CLAUDE.md](CLAUDE.md). Neither needs re-deriving.

```
.venv/Scripts/python.exe harness/gates.py     # 5 gates, 81 assertions, ~14s
.venv/Scripts/python.exe -m shout             # run it
```

Real-use latency: 177–333ms end-to-end against an 800ms budget. Vocabulary came
back accurate with no hotword biasing at all — Pokémon names and brand terms that
iOS dictation garbles were correct first try.

## The one real gap

**There is no way for Gabe to launch this himself.** It currently runs only
because a session backgrounded it, and this machine is shut down nightly, so it
will be gone tomorrow. Needs a shortcut (pythonw, no console window) plus
optionally a Startup-folder entry. Small job — see the 2026-09-05 Cardinal
launcher note in the global lab notes for the pattern and the traps
(`127.0.0.1` vs `localhost` latency, `MainWindowTitle` cannot enumerate windows).

Also worth raising: **this project is not a git repo.** There is now real code
with no version control.

## Do NOT build the session 2 list yet

The original session 2 scope was overlay, VAD tuning, hotword dictionaries,
settings UI, autostart, per-app paste keys. As of the manual pass, **none of it is
justified by observed friction**:

- Hotword dictionaries were the highest-value item and the vocabulary test came
  back clean. Building a dictionary now would be guessing at words that are not
  actually wrong.
- Per-app paste keys were for Windows Terminal, which is deliberately untested
  because terminals get commands, not prose.
- The elevated-window blackout turned out to be structurally moot (see PLAN.md).

The agreed sequencing was spine first, then a few days of real use, then features
against real friction. That is exactly where this is. **Next session should start
by asking what actually annoyed him**, not by opening this list.

## Open questions, to answer from use rather than analysis

- Does the 300–350ms latch window ever swallow two quick separate push-to-talks?
- Is `MIN_AUDIO_S = 0.25` the right floor for ignoring a stray tap?
- Which words, if any, eventually need a hotword entry?

## Not started

The meeting/PHI pipeline (`scripts/transcribe_meeting.py`) is untouched and
unrelated to the dictation app. The noScribe-vs-WhisperX diarization decision in
`RESEARCH.html` is still open.
