"""Gate: a question dictated flat gets its question mark, a statement does not.

Whisper punctuates from intonation, so a question said without a rising voice
comes back with a full stop. `shout.questions.punctuate` reads the wording
instead. This drives the real function, the one `transcribe()` calls.

What keeps the rows honest: every ADD row's input must differ from its expected
output (otherwise a do-nothing function passes it), and most KEEP rows are near
misses -- orders, statements and agreements that open on the very words the
rules look for. Every rule must fire at least once, exactly as probe_tail
requires of its patterns.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shout.questions import _RULES, punctuate  # noqa: E402

RSQUO = chr(0x2019)          # curly apostrophe, as Whisper usually emits it

# (label, dictated text, expected text)
ADD = [
    ("can you", "Can you check the logs.", "Can you check the logs?"),
    ("is there", "Is there a faster way to do this.", "Is there a faster way to do this?"),
    ("do you, after an opener", "Okay, do you think that will work.", "Okay, do you think that will work?"),
    ("does it", "Does it still break on the second monitor.", "Does it still break on the second monitor?"),
    ("would it, with a name", "Would Claude be able to handle that.", "Would Claude be able to handle that?"),
    ("negative auxiliary", "Isn't that the same bug as before.", "Isn't that the same bug as before?"),
    ("can't, curly", "Can" + RSQUO + "t we just cache it.", "Can" + RSQUO + "t we just cache it?"),
    ("have you", "Have you run the gates yet.", "Have you run the gates yet?"),
    ("what do", "What do you think about the orb.", "What do you think about the orb?"),
    ("what's", "What's the status on the import.", "What's the status on the import?"),
    ("why is", "Why is the pill hiding again.", "Why is the pill hiding again?"),
    ("how many", "How many cards are left in the box.", "How many cards are left in the box?"),
    ("what about", "What about the sealed product.", "What about the sealed product?"),
    ("which one", "Which one is faster.", "Which one is faster?"),
    ("any idea", "Any idea why the build failed.", "Any idea why the build failed?"),
    ("after a lead-in clause", "When you get a chance, can you look at the log.",
     "When you get a chance, can you look at the log?"),
    ("tag", "That runs before the hook, right.", "That runs before the hook, right?"),
    ("unpunctuated last sentence", "Can you ship it Monday", "Can you ship it Monday?"),
    ("only the question in the middle",
     "Pull the Charizard. Can you check the price. Then ship it.",
     "Pull the Charizard. Can you check the price? Then ship it."),
    ("two questions",
     "Is it done yet. What did the gate say.",
     "Is it done yet? What did the gate say?"),
    ("a decimal stays whole", "Is it version 3.5 or 4.", "Is it version 3.5 or 4?"),
]

# Statements that must survive byte for byte. Most open like a question.
KEEP = [
    ("an order with do", "Do it now."),
    ("an order with have", "Have a look at the logs."),
    ("an order with don't", "Don't touch the venv."),
    ("agreement, so do I", "So do I."),
    ("no subject: should be", "Should be fine."),
    ("no subject: will do", "Will do."),
    ("no subject: could be", "Could be worse."),
    ("nothing after the subject", "Did it."),
    ("what as a statement", "What I want is the blue one."),
    ("when as a statement", "When I get home, call me."),
    ("how as a statement", "How I fixed it is in the notes."),
    ("now is not an opener", "Now is the time to ship it."),
    ("an exclamation is the model's call", "Are you kidding me!"),
    ("already a question", "Is it done?"),
    ("an ellipsis is the model's call", "Can you..."),
    ("a domain is not a sentence end", "Ship it to vendorvault.dev today."),
    ("ordinary dictation", "Set the Base Set Blastoise to four hundred and ship it Monday."),
    ("a request that is not a question", "Let me know if the build is green."),
    ("right, not as a tag", "Turn right at the light."),
]


def main() -> int:
    rows = []

    def check(ok, name, detail=""):
        rows.append((bool(ok), name, detail))

    fired = set()
    for label, text, want in ADD:
        got, rules = punctuate(text)
        fired.update(rules)
        check(got == want and rules, f"adds: {label}", f"got {got!r} want {want!r}")
        check(text != want, f"CONTROL: {label} lacks the mark before punctuate", "")
    for label, text in KEEP:
        got, rules = punctuate(text)
        check(got == text and not rules, f"keeps: {label}", f"got {got!r} rules={rules}")

    check(len(ADD) >= 15, f"exercised {len(ADD)} add cases", "")
    check(len(KEEP) >= 15, f"exercised {len(KEEP)} keep cases", "")
    names = {name for name, _ in _RULES}
    missing = sorted(names - fired)
    check(not missing, f"all {len(names)} rules exercised", f"never fired: {missing}")

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if not ok and detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"question assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
