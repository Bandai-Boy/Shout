"""Gate: invented subtitle boilerplate is dropped, real dictation is not.

Whisper decodes a chunk it cannot resolve into words as the outro its subtitle
training data ends with, so a breath or a lip smack after the last word comes
back as "We'll be right back". Reported from real use on 20 Sep 2026 at roughly
one dictation in six.

Trailing SILENCE is NOT the trigger, measured the same day: across 62 cases of
synthetic room tone and real recorded noise floor appended to speech, at tails
from 0.3s to 6s and gains to 12x, the count of hallucinations was zero, because
vad_filter removes the tail before the decoder sees it. It takes a real sound.
That is why this gate is a table over `assemble` rather than a model run: the
audio that provokes it cannot be synthesized, so the only honest thing to pin
is what the text path does once the model has already invented something.

This drives the real `shout.transcribe.assemble`, the same function the real
`transcribe()` calls, not a copy of its rules.

Two things keep the rows from passing vacuously. Every DROP row is also run
through a plain join, which must still contain the junk -- otherwise the case
never carried any. And the KEEP rows are mostly near misses: real sentences
holding the very words the patterns match, which pass only because the patterns
are anchored to a trailing sentence boundary.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shout.transcribe import _BOILERPLATE, assemble  # noqa: E402

RSQUO = chr(0x2019)          # curly apostrophe, as Whisper usually emits it


def seg(text):
    return SimpleNamespace(text=text)


def segs(*texts):
    return [seg(t) for t in texts]


# (label, segments, expected text). Segment texts carry the leading space
# faster-whisper puts on every segment, so the join is the real one.
DROP = [
    ("its own trailing segment",
     segs(" And that is the whole list.", " Thank you for watching."),
     "And that is the whole list."),
    ("the reported phrase",
     segs(" Ship it to the Bellevue store.", " We'll be right back."),
     "Ship it to the Bellevue store."),
    ("curly apostrophe",
     segs(" Ship it to the Bellevue store.", " We" + RSQUO + "ll be right back."),
     "Ship it to the Bellevue store."),
    ("fused onto the last segment",
     segs(" Pull the Charizard and the two Umbreons. Thanks for watching!"),
     "Pull the Charizard and the two Umbreons."),
    ("repeated twice",
     segs(" Grade them all.", " Thank you for watching.", " Thanks for watching."),
     "Grade them all."),
    ("nothing but the hallucination",
     segs(" Thank you for watching."),
     ""),
    ("a sound tag",
     segs(" Box is sealed.", " [Music]"),
     "Box is sealed."),
    ("an attribution line",
     segs(" Box is sealed.", " Subtitles by the Amara.org community"),
     "Box is sealed."),
    ("no sentence punctuation before it",
     segs(" Box is sealed", " Thanks for watching"),
     "Box is sealed"),
    ("see you in the next video",
     segs(" That is everything.", " I'll see you in the next video."),
     "That is everything."),
    # One per remaining pattern: an untested pattern is dead weight, and a typo
    # inside one would otherwise never surface.
    ("thanks for listening",
     segs(" Invoice is sent.", " Thanks for listening."),
     "Invoice is sent."),
    ("we will, spelled out",
     segs(" Invoice is sent.", " We will be right back."),
     "Invoice is sent."),
    ("like and subscribe",
     segs(" Invoice is sent.", " Like and subscribe."),
     "Invoice is sent."),
    ("subscribe to my channel",
     segs(" Invoice is sent.", " Subscribe to my channel."),
     "Invoice is sent."),
    ("see you in the next one",
     segs(" Invoice is sent.", " See you in the next one."),
     "Invoice is sent."),
    ("transcribed by",
     segs(" Invoice is sent.", " Transcribed by Elizabeth Park."),
     "Invoice is sent."),
    ("a bare amara line",
     segs(" Invoice is sent.", " Amara.org"),
     "Invoice is sent."),
    ("a parenthesised sound tag",
     segs(" Invoice is sent.", " (Applause)"),
     "Invoice is sent."),
]

# Real dictation that must survive byte for byte. Most are near misses.
KEEP = [
    ("the phrase mid-sentence",
     segs(" Thanks for watching the stream, I'll post the pull list tonight."),
     "Thanks for watching the stream, I'll post the pull list tonight."),
    ("a real sign-off",
     segs(" Alright, see you next time."),
     "Alright, see you next time."),
    ("stay tuned is real copy",
     segs(" New singles drop Friday. Stay tuned."),
     "New singles drop Friday. Stay tuned."),
    ("please subscribe is real copy",
     segs(" We send a drop alert every week. Please subscribe."),
     "We send a drop alert every week. Please subscribe."),
    ("subscribe to something that is not a channel",
     segs(" Please subscribe to the newsletter."),
     "Please subscribe to the newsletter."),
    ("we'll be right back, continued",
     segs(" Tell them we'll be right back after the break."),
     "Tell them we'll be right back after the break."),
    ("bare thank you is never touched",
     segs(" Thank you."),
     "Thank you."),
    ("bare bye is never touched",
     segs(" Bye."),
     "Bye."),
    ("bare you is never touched",
     segs(" You."),
     "You."),
    ("ordinary dictation",
     segs(" Set the Base Set Blastoise to four hundred and ship it Monday."),
     "Set the Base Set Blastoise to four hundred and ship it Monday."),
]


def main() -> int:
    rows = []

    def check(ok, name, detail=""):
        rows.append((bool(ok), name, detail))

    for label, s, want in DROP:
        got, dropped = assemble(s)
        check(got == want and dropped,
              f"drops: {label}", f"got {got!r} want {want!r} patterns={dropped}")
        # Control: the case must actually have carried junk, or the row above
        # would pass on a function that does nothing.
        plain = "".join(x.text for x in s).strip()
        check(plain != want,
              f"CONTROL: {label} carries junk before assemble", f"plain={plain!r}")

    for label, s, want in KEEP:
        got, dropped = assemble(s)
        check(got == want and not dropped,
              f"keeps: {label}", f"got {got!r} want {want!r} patterns={dropped}")

    # Non-vacuity: count what actually ran, not the size of the population.
    check(len(DROP) >= 10, f"exercised {len(DROP)} drop cases", "")
    check(len(KEEP) >= 10, f"exercised {len(KEEP)} keep cases", "")
    check(len(_BOILERPLATE) >= 12, f"{len(_BOILERPLATE)} patterns compiled", "")

    # Every pattern must be reachable: one that never fires is dead weight and
    # a typo in it would otherwise be invisible forever. Exact, not a floor --
    # a floor let 8 of 13 patterns ship untested on this gate's first run.
    fired = set()
    for _, s, _ in DROP:
        fired.update(assemble(s)[1])
    missing = sorted(set(range(len(_BOILERPLATE))) - fired)
    check(not missing, f"all {len(_BOILERPLATE)} patterns exercised",
          f"never fired: {[_BOILERPLATE[i] for i in missing]}")

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if not ok and detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"boilerplate assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
