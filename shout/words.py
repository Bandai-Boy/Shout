"""Your words: names Whisper should listen for, and fixes it should always make.

Two lists, both kept in config.json and both edited from the Words page and
from Fix on the Recent dictations page:

- `words` are passed to Whisper as `hotwords`, which biases decoding toward
  them in EVERY 30s window (initial_prompt reaches only the first, because
  condition_on_previous_text is off). Measured 19 Nov 2026 on TTS clips of card
  and tool names: 13 of 36 right without hints, 30 of 36 with them.
- `replacements` rewrite what Whisper heard into what you meant, after the
  fact. They fire every time, which hints cannot promise.

Two measured traps decide how the hints are sent (see CLAUDE.md, "Your words"):

- Hints switch Whisper into a mode that writes a whole 30s window as one
  segment with no inner timestamps. faster-whisper then resumes at the 30s
  mark, and any speech between the last word it wrote and that mark is never
  transcribed: 9 words lost on a 32s clip. `word_timestamps=True` resumes at
  the last word actually written instead, which closed it on every clip.
  Transcriber turns it on only when there are hints, so an empty list decodes
  exactly as before.
- A comma-separated list cost the final full stop on two clips; the same words
  as short sentences did not. Hence ". " between them.

Hints share Whisper's 448-token context with the punctuation prompt and the
words being decoded, and a long list dilutes them: 18 names in 210 characters
scored 16 of 18, the same plus 60 more (956 characters) scored 13. So the hint
text is capped at HINT_BUDGET characters, newest words first, which means a fix
you just made is always heard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config, merge

HINT_BUDGET = 250
_SEP = ". "


def _clean(term) -> str:
    """One form for every word, heard text and replacement: spacing collapsed,
    and the punctuation a drag-selection picks up at either end dropped, so a
    fix saved from "Pikachu," also fires before a full stop, and Remove finds
    what Save stored."""
    return " ".join(str(term).split()).strip(" .,;:!?")


def _key(term) -> str:
    return _clean(term).casefold()


@dataclass(frozen=True)
class Vocab:
    words: tuple[str, ...] = ()
    # (heard, write) pairs, as the user typed them.
    fixes: tuple[tuple[str, str], ...] = ()

    @classmethod
    def resolve(cls, words, replacements) -> "Vocab":
        """config.json -> a Vocab. Tolerates anything a hand edit can put there:
        blanks, duplicates differing only in case, a fix that changes nothing,
        or the wrong type altogether."""
        seen: set[str] = set()
        kept: list[str] = []
        for w in words if isinstance(words, (list, tuple)) else ():
            w = _clean(w)
            if w and w.casefold() not in seen:
                seen.add(w.casefold())
                kept.append(w)
        fixes: list[tuple[str, str]] = []
        heard_seen: set[str] = set()
        items = replacements.items() if isinstance(replacements, dict) else ()
        for heard, write in items:
            heard, write = _clean(heard), _clean(write)
            if heard and write and heard != write and heard.casefold() not in heard_seen:
                heard_seen.add(heard.casefold())
                fixes.append((heard, write))
        return cls(tuple(kept), tuple(fixes))

    def heard(self) -> tuple[str, ...]:
        """The words that fit HINT_BUDGET, newest first in priority, returned in
        list order."""
        out: list[str] = []
        used = 0
        for w in reversed(self.words):
            cost = len(w) + (len(_SEP) if out else 1)    # the trailing "."
            if used + cost > HINT_BUDGET:
                continue                                 # a shorter one may fit
            out.append(w)
            used += cost
        return tuple(reversed(out))

    def hint(self) -> str | None:
        kept = self.heard()
        return _SEP.join(kept) + "." if kept else None

    def apply(self, text: str) -> tuple[str, int]:
        """Rewrite every fix, whole words only, in one pass: what one fix writes
        is never fed to another. Longest heard text wins where two overlap.

        Case: a fix whose replacement has a capital in it is written exactly as
        typed (a name is a name at any position). An all-lowercase replacement
        takes the capital of what it replaces, so a fix at the start of a
        sentence still starts the sentence."""
        if not self.fixes or not text:
            return text, 0
        table = {h.casefold(): w for h, w in self.fixes}
        alts = sorted(table, key=len, reverse=True)
        pattern = re.compile(
            r"(?<!\w)(?:" + "|".join(r"\s+".join(map(re.escape, a.split()))
                                     for a in alts) + r")(?!\w)",
            re.IGNORECASE)
        count = 0

        def swap(m: re.Match) -> str:
            nonlocal count
            # IGNORECASE matches a few letters whose casefold is not the
            # table's (Turkish dotted I, for one). Leave those as heard rather
            # than raise: an exception here loses the whole dictation.
            write = table.get(" ".join(m.group(0).split()).casefold())
            if write is None:
                return m.group(0)
            count += 1
            if write != write.lower() or not m.group(0)[:1].isupper():
                return write
            return write[:1].upper() + write[1:]

        return pattern.sub(swap, text), count


# -- config.json -------------------------------------------------------------
# Every edit re-reads the file. A snapshot goes stale the moment anything else
# saves (the cue lab's Delete bug, 8 Sep), and Config.read() rather than load()
# because load() answers a half-written file with the defaults, and saving on
# top of that would wipe both lists. A read error reaches the caller instead.

def stored() -> tuple[list[str], dict[str, str]]:
    try:
        cfg = Config.read()
    except (AttributeError, TypeError) as e:     # valid JSON, but not an object
        raise ValueError(f"config.json is not a settings object: {e}") from e
    words = [str(w) for w in cfg.words] if isinstance(cfg.words, list) else []
    reps = ({str(k): str(v) for k, v in cfg.replacements.items()}
            if isinstance(cfg.replacements, dict) else {})
    return words, reps


def save(words: list[str], replacements: dict[str, str]) -> None:
    merge({"words": words, "replacements": replacements})


def add_word(word: str) -> str:
    """Add a word, or move it to the end if it is there already: the end is
    newest, and newest is what the hint budget keeps."""
    word = _clean(word)
    if not word:
        return ""
    words, reps = stored()
    words = [w for w in words if _key(w) != word.casefold()] + [word]
    save(words, reps)
    return word


def remove_word(word: str) -> None:
    words, reps = stored()
    save([w for w in words if _key(w) != _key(word)], reps)


def add_fix(heard: str, write: str) -> bool:
    """Shout heard `heard`; you meant `write`. Saves the fix and, when `write`
    looks like a name (it has a capital in it), also listens for it, so the
    next one is more likely right the first time. A lowercase fix ("gonna" ->
    "going to") is a matter of style, not of hearing, and would only spend the
    hint budget.

    Recent shows text AFTER the fixes, so the word you select may be one an
    earlier fix wrote (cache -> cash, then "cash" -> "Cash App"). Fixes run in
    one pass, so a new fix on that output would never fire; the earlier fix is
    pointed at the new spelling instead, and that one does.

    Returns whether the word was added to the list."""
    heard, write = _clean(heard), _clean(write)
    if not heard or not write or heard == write:
        return False
    words, reps = stored()
    reps = {k: (write if _key(v) == heard.casefold() else v)
            for k, v in reps.items() if _key(k) != heard.casefold()}
    reps[heard] = write
    listen = write != write.lower()
    if listen:
        words = [w for w in words if _key(w) != write.casefold()] + [write]
    save(words, reps)
    return listen


def remove_fix(heard: str) -> None:
    words, reps = stored()
    save(words, {k: v for k, v in reps.items() if _key(k) != _key(heard)})
