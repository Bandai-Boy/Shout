"""Give a dictated question its question mark.

Whisper punctuates from intonation, so a question said flat -- which is how
most people dictate -- comes back with a full stop. This reads the wording
instead: English marks a question in its first few words, by putting the verb
before the subject ("can you", "is there") or by opening on a wh-word that is
followed by a verb ("what do", "where's").

The rules are deliberately narrow, because the common near misses share those
first words: "Do it now" and "Have a look" are orders, "What I want is the blue
one" and "When I get home, call me" are statements, "So do I" is agreement,
"Should be fine" has no subject at all. So "do", "have" and "don't" count only
before a personal pronoun, every inversion needs a word after its subject, and a
wh-word needs a verb after it, never a pronoun. A wrong question mark is
visible and one keystroke to fix, and so is a missing one, which is the
behaviour without this; that is why nothing here tries to guess at intent.

Only the keyword groups ignore case. A capitalised word after the verb is read
as a name ("Is Claude ..."), which needs the rest to stay case-sensitive, or
every word would count as a name. Written without literal typographic
characters or backslash escapes, per the lab notes.
"""
from __future__ import annotations

import re

_APOS = "['" + chr(0x2019) + "]"           # straight or curly
_ELLIPSIS = chr(0x2026)

# Openers that come before the question proper: "So, can you ...", "Okay quick
# question, what's ...". "Now" and "then" are left out on purpose: "Now is the
# time" would become a question.
_LEAD = (r"(?:(?i:so|and|but|also|okay|ok|alright|hey|well|um|uh|oh|yeah|"
         r"actually|wait|quick question|one more thing|claude)\b,?\s+)*")

_PRONOUN = r"(?i:i|you|we|they)\b"
_SUBJECT = (r"(?:(?i:i|you|we|they|he|she|it|there|this|that|these|those|"
            r"any(?:one|body|thing)|some(?:one|body|thing)|every(?:one|body|thing)|"
            r"nothing|the|a|an|my|your|our|their|his|her|its|any|all|some|each|both)\b"
            r"|[A-Z]\w*)")                 # a capitalised name: "Is Claude ..."
_NOT = r"(?:n" + _APOS + r"t)?"
# Auxiliaries that open a question before any subject. "do", "have", "had" and
# "don't" open orders too ("Do it", "Have a look", "Don't touch it"), so they
# count only before a personal pronoun, which an order never has.
_AUX_ANY = (r"(?i:(?:is|are|was|were|am|does|did|can|could|will|would|should|"
            r"shall|may|might|must|has)" + _NOT
            + r"|can" + _APOS + r"t|won" + _APOS + r"t)")
_AUX_PRONOUN = r"(?i:(?:do|have|had)" + _NOT + r")"

_INVERTED = (r"(?:" + _AUX_ANY + r"\s+" + _SUBJECT
             + r"|" + _AUX_PRONOUN + r"\s+" + _PRONOUN + r")")

_WH = r"(?:what|why|where|when|who|whose|which|how)"
_WH_QUESTION = (
    r"(?i:" + _WH + _APOS + r"(?:s|re|d|ll|ve)\b"             # what's, where'd
    + r"|" + _WH + r"\s+(?:" + _AUX_ANY + r"|" + _AUX_PRONOUN + r")\b"
    + r"|how\s+(?:many|much|long|often|far|come|about|big|old|soon|else)\b"
    + r"|what\s+(?:about|if|else)\b"
    + r"|why\s+not\b"
    + r"|whose\b"
    + r"|which\s+(?:one|ones|of)\b"
    + r"|any\s+(?:idea|ideas|thoughts|chance)\b)"
)

_RULES = (
    ("inverted", re.compile(r"\A" + _LEAD + _INVERTED + r"\s+\w")),
    ("wh", re.compile(r"\A" + _LEAD + _WH_QUESTION)),
    # A question after a lead-in clause: "When you get a chance, can you ...".
    ("after comma", re.compile(
        r",\s+(?i:can|could|would|will|should|do|did)\s+" + _PRONOUN + r"\s+\w")),
    ("tag", re.compile(
        r",\s*(?i:right|correct|or not|isn" + _APOS + r"t (?:it|that)|aren"
        + _APOS + r"t (?:they|you)|don" + _APOS + r"t you think)\W*\Z")),
)

# A sentence ends at punctuation followed by a space or the end of the text, so
# "3.5" and "vendorvault.dev" stay whole.
_SENTENCE = re.compile(r".*?(?:[.!?" + _ELLIPSIS + r"]+(?=\s|\Z)|\Z)", re.DOTALL)


def _rule(sentence: str) -> str | None:
    core = sentence.strip()
    for name, rx in _RULES:
        if rx.search(core):
            return name
    return None


def punctuate(text: str) -> tuple[str, list[str]]:
    """Turn each question's full stop (or missing stop) into a question mark.

    Returns the text and the names of the rules that fired, which is all a
    caller may log. A sentence ending in "!", "?" or an ellipsis is left alone:
    the model heard something there, and it is not overruled.
    """
    out: list[str] = []
    fired: list[str] = []
    for m in _SENTENCE.finditer(text):
        sentence = m.group(0)
        if not sentence:
            continue
        body = sentence.rstrip()
        trailing = sentence[len(body):]
        if body.endswith(".") and not body.endswith(".."):
            stem = body[:-1]
        elif body and body[-1].isalnum() and m.end() == len(text):
            stem = body                    # the last sentence, left unpunctuated
        else:
            out.append(sentence)
            continue
        name = _rule(stem)
        if name:
            fired.append(name)
            out.append(stem + "?" + trailing)
        else:
            out.append(sentence)
    return "".join(out), fired
