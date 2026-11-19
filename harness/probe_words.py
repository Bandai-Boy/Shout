"""Gate: your words are heard, fixed, and cost nothing else.

Two halves. The TEXT rows drive `shout.words` directly: whole-word fixes, the
case rule, one pass with no chains, the hint budget, and the config edits
against a throwaway APPDATA. The same table is also run through a plain
str.replace, which must fail the rows that exist to catch it, or those rows
were never testing anything.

The MODEL rows drive the real `Transcriber.transcribe` over clips spoken by
Windows' own TTS (David), made fresh each run, so no audio is committed.
Measured 19 Nov 2026, which is what the floors below come from:

- card and tool names right: 10 of the 29 in these clips with no words, 25
  with them (13 -> 30 of 36 over a wider set that ran too long for a gate)
- hints alone made Whisper write a 30s window as one segment, and the 3s of
  speech between its last word and the 30s mark was never transcribed (9 words
  on the 32s clip). word_timestamps closes it. The positive control is the
  production call with word_timestamps forced off, and it must lose words on
  that clip, or the "no words lost" rows cannot see the bug they exist for.
- sentence ends: 44 either way; nothing at all on silence, room tone, a breath.

Needs the GPU and about a minute. Safe while Shout runs: it synthesizes no keys.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
TMP = Path(tempfile.mkdtemp(prefix="shout-words-"))
os.environ["APPDATA"] = str(TMP)          # never the live config.json

import numpy as np  # noqa: E402

from shout import words  # noqa: E402
from shout.config import Config, config_dir  # noqa: E402
from shout.words import HINT_BUDGET, Vocab  # noqa: E402

rows: list[bool] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append(bool(ok))
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    return bool(ok)


# -- text -------------------------------------------------------------------

# (name, fixes, text, expected). `naive` marks the rows a plain str.replace
# must FAIL: whole words, case, one pass, longest first.
CASES = [
    ("a fix rewrites its word", {"cache": "cash"}, "count the cache tonight",
     "count the cash tonight", False),
    ("whole words only: Mew never touches Mewtwo", {"Mew": "Mew ex"},
     "Mewtwo and Mew", "Mewtwo and Mew ex", True),
    ("a name is written exactly as typed, at any position",
     {"tseg player": "TCGplayer"}, "Tseg player is up, check tseg player.",
     "TCGplayer is up, check TCGplayer.", True),
    ("a lowercase fix keeps the capital that starts a sentence",
     {"cache": "cash"}, "Cache sales were good.", "Cash sales were good.", True),
    ("one pass: what one fix writes is never fixed again",
     {"a": "b", "b": "c"}, "a b", "b c", True),
    ("the longest heard text wins where two overlap",
     {"vendor": "Vendor", "vendor vault": "VendorVault"},
     "vendor vault and vendor", "VendorVault and Vendor", True),
    ("spacing inside a phrase does not matter",
     {"tseg player": "TCGplayer"}, "on tseg  player", "on TCGplayer", True),
    ("punctuation next to a word does not stop a fix",
     {"cache": "cash"}, "the cache, then cache.", "the cash, then cash.", False),
    ("regex characters in what was heard are literal",
     {"c++": "C++", "a.b": "AB"}, "c++ and axb and a.b", "C++ and axb and AB", False),
    ("text with no fix in it comes back untouched",
     {"cache": "cash"}, "nothing to see", "nothing to see", False),
    ("punctuation dragged into a selection is not part of the fix",
     {"Pikachu,": "Picachu"}, "Pikachu, hi and Pikachu.", "Picachu, hi and Picachu.", True),
    ("a letter whose case-fold differs never crashes a dictation",
     {"i": "eye"}, "\u0130 and i", "\u0130 and eye", False),
    ("final sigma folds to sigma", {"\u03c3": "x"}, "\u03c2 \u03c3", "x x", False),
]


def naive(fixes: dict, text: str) -> str:
    for heard, write in fixes.items():
        text = text.replace(heard, write)
    return text


def text_rows() -> None:
    print("text")
    for name, fixes, text, want, _ in CASES:
        got, n = Vocab.resolve([], fixes).apply(text)
        check(got == want, name, f"{text!r} -> {got!r}" if got != want else "")
    flips = [name for name, fixes, text, want, must in CASES
             if must and naive(fixes, text) == want]
    check(not flips, "control: a plain str.replace fails every row marked to catch it",
          f"passed anyway: {flips}" if flips else "")
    dotted = "\u0130"
    check(re.search(r"(?<!\w)i(?!\w)", dotted, re.IGNORECASE) is not None
          and dotted.casefold() != "i",
          "control: IGNORECASE matches a dotted I that no table key folds to "
          "(what raised KeyError before)")
    check(Vocab().apply("x") == ("x", 0) and Vocab().hint() is None,
          "an empty list fixes nothing and sends no hint")

    junk = Vocab.resolve("notalist", ["also", "wrong"])
    check(junk == Vocab(), "a hand-edited config of the wrong types resolves to nothing")
    v = Vocab.resolve([" Ogerpon ", "ogerpon", "", "Gyarados."],
                      {"x": "x", "Cache": "cash", "cache": "money"})
    check(v.words == ("Ogerpon", "Gyarados") and v.fixes == (("Cache", "cash"),),
          "blanks, case duplicates and fixes that change nothing are dropped",
          f"{v.words} {v.fixes}")

    long_old = "Awakening of the New Era Booster Display " * 2
    # 25 names fill 199 characters, which leaves no room for the 81-character
    # word and room for "Enel".
    many = [long_old.strip(), "Enel"] + [f"Name{i:02d}" for i in range(25)]
    v = Vocab.resolve(many, {})
    hint = v.hint() or ""
    kept = v.heard()
    check(len(hint) <= HINT_BUDGET and kept[-1] == "Name24",
          f"the hint fits {HINT_BUDGET} characters and keeps the newest word",
          f"{len(hint)} chars, newest kept {kept[-1]!r}")
    check(long_old.strip() not in kept and "Enel" in kept,
          "an old word too long for the room left is skipped, a short older one still fits",
          f"kept {len(kept)} of {len(many)}")
    check(hint.endswith(".") and ", " not in hint and hint.count(". ") == len(kept) - 1,
          "the hint is short sentences, not a comma list (the comma list cost "
          "full stops)", hint[:60])


def config_rows() -> None:
    print("config.json")
    path = config_dir() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"cue_preset": "glass", "overlay_style": "orb"}),
                    encoding="utf-8")
    listened = words.add_fix("tseg player", "TCGplayer")
    lower = words.add_fix("cache", "cash")
    raw = json.loads(path.read_text(encoding="utf-8"))
    check(listened and not lower and raw["words"] == ["TCGplayer"],
          "a fix to a name is also listened for; a lowercase fix is not",
          f"words={raw['words']}")
    check(raw["replacements"] == {"tseg player": "TCGplayer", "cache": "cash"},
          "both fixes are saved", str(raw["replacements"]))
    check(raw["cue_preset"] == "glass" and raw["overlay_style"] == "orb",
          "and every other key in the file is kept")
    words.add_word("Ogerpon")
    words.add_word("tcgplayer")
    check(json.loads(path.read_text(encoding="utf-8"))["words"] == ["Ogerpon", "tcgplayer"],
          "adding a word again moves it to newest instead of listing it twice")
    words.add_fix("Cache", "money")
    reps = json.loads(path.read_text(encoding="utf-8"))["replacements"]
    check(reps == {"tseg player": "TCGplayer", "Cache": "money"},
          "a new fix for the same heard text replaces the old one", str(reps))
    words.remove_fix("cache")
    words.remove_word("OGERPON")
    raw = json.loads(path.read_text(encoding="utf-8"))
    check(raw["replacements"] == {"tseg player": "TCGplayer"} and raw["words"] == ["tcgplayer"],
          "remove matches without regard to case", f"{raw['words']} {raw['replacements']}")
    words.add_fix("Pikachu.", "Picachu.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    check(raw["replacements"].get("Pikachu") == "Picachu" and "Picachu" in raw["words"],
          "a fix saved from a selection with a full stop is stored without it",
          f"{raw['words']} {raw['replacements']}")
    words.remove_word("Picachu")
    check("Picachu" not in json.loads(path.read_text(encoding="utf-8"))["words"],
          "and the word Remove names is the word Save stored")
    words.add_fix("cache", "cash")
    words.add_fix("cash", "Cash App")
    reps = json.loads(path.read_text(encoding="utf-8"))["replacements"]
    v = Vocab.resolve([], reps)
    check(v.apply("count the cache")[0] == "count the Cash App",
          "fixing the output of an earlier fix re-points that fix, so it fires",
          str(reps))
    check(words.add_word("...") == "" and "" not in
          json.loads(path.read_text(encoding="utf-8"))["words"],
          "a word that is only punctuation is never stored")
    before = path.read_text(encoding="utf-8")
    path.write_text("[1, 2]", encoding="utf-8")     # valid JSON, not settings
    try:
        words.add_word("Enel")
        raised = False
    except ValueError:
        raised = True
    check(raised and path.read_text(encoding="utf-8") == "[1, 2]",
          "a config.json that is not a settings object is refused, untouched")
    path.write_text("{", encoding="utf-8")          # caught mid-write
    try:
        words.add_fix("enel", "Enel")
        raised = False
    except ValueError:
        raised = True
    check(raised and path.read_text(encoding="utf-8") == "{",
          "a half-written config.json is refused, never saved over with empty lists")
    path.write_text(before, encoding="utf-8")


# -- model ------------------------------------------------------------------

TARGETS = ["Umbreon", "Charizard", "Prismatic Evolutions", "TCGplayer", "VendorVault",
           "Roronoa Zoro", "Collectr", "Terapagos", "PriceCharting", "Ogerpon",
           "Gyarados", "Supabase", "Boa Hancock", "Enel", "JustTCG", "Paldean Fates",
           "Eevee", "Shrouded Fable"]
SHORT = {
    "v1": "Pull the Umbreon and the Charizard from the Prismatic Evolutions case.",
    "v2": "Check the price on TCGplayer and then update it in VendorVault.",
    "v3": "The Roronoa Zoro alt art sold for forty dollars at the show.",
    "v4": "Collectr says the Terapagos is up, but PriceCharting disagrees.",
    "v5": "Add the Ogerpon and Gyarados to the Supabase table tonight.",
    "v6": "Boa Hancock and Enel are the two leaders I want to stock.",
    "v7": "JustTCG returned the Paldean Fates prices, so sync them now.",
    "v8": "Eevee promos from Shrouded Fable go in the front binder.",
}
# (rate, text). "long" is the clip whose second window lost 9 words to hints
# without word timestamps; it holds none of the names on purpose.
LONG = {
    "long": (2, "Okay so here is the full plan for the weekend show. We need to pack the "
             "sealed product on Thursday night and load the car on Friday morning before "
             "work. The binders go in the blue tote and the slabs go in the hard case with "
             "the foam. I want the price tags printed by Wednesday so we are not rushing. "
             "At the table we put the high end slabs at the front and the bulk boxes at the "
             "back corner. If anyone asks about trades we only take sealed product or graded "
             "cards above a hundred dollars. Bring the card reader and a backup battery and "
             "plenty of change for cash sales. After the show we count the inventory and "
             "update everything in the system the same night."),
    "L1": (0, "Alright so for the show this weekend I want the Umbreon and the Charizard "
           "slabs in the front case. The Prismatic Evolutions booster bundles go on the left "
           "side of the table. Check TCGplayer for the current prices before we leave and "
           "update everything in VendorVault so the tags match. If the Terapagos promo is "
           "still sitting there on Sunday we can drop it by five dollars. Bring the Gyarados "
           "and the Ogerpon binders too because people kept asking about them last time. "
           "After the show we count the cash and log it the same night."),
    "L3": (0, "I want to change how the scanner works in the app. Right now it takes a photo "
           "and then waits for the match, which feels slow. Instead it should show a spinner "
           "on the card and let me keep scanning. When the match comes back from Supabase it "
           "fills in the name and the price. If JustTCG does not know the card, flag it in "
           "yellow so I can fix it later. Also the Paldean Fates cards keep matching to the "
           "wrong set, so look at that first. Do not change anything else about the layout. "
           "Run the gates when you are done and tell me what failed."),
}
PS = r"""
param([string]$OutDir, [string]$ListFile)
Add-Type -AssemblyName System.Speech
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($line in Get-Content -Encoding UTF8 $ListFile) {
    if (-not $line.Trim()) { continue }
    $parts = $line.Split('|', 3)
    $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $s.SelectVoice('Microsoft David Desktop')
    $s.Rate = [int]$parts[1]
    $s.SetOutputToWaveFile((Join-Path $OutDir ($parts[0] + '.wav')), $fmt)
    $s.Speak($parts[2])
    $s.Dispose()
}
"""


def make_clips() -> dict[str, tuple[str, Path]]:
    out = TMP / "clips"
    out.mkdir()
    script, listing = TMP / "tts.ps1", TMP / "clips.txt"
    script.write_text(PS, encoding="utf-8")
    spec = {k: (0, t) for k, t in SHORT.items()} | LONG
    listing.write_text("\n".join(f"{k}|{r}|{t}" for k, (r, t) in spec.items()),
                       encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(script), "-OutDir", str(out), "-ListFile", str(listing)],
                   check=True, capture_output=True, timeout=120)
    return {k: (t, out / f"{k}.wav") for k, (_, t) in spec.items()}


def recall(ref: str, hyp: str) -> float:
    norm = lambda s: re.findall(r"[a-z0-9]+", s.lower())   # noqa: E731
    a, b = norm(ref), norm(hyp)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(m.size for m in sm.get_matching_blocks()) / len(a)


def ends(text: str) -> int:
    return len(re.findall(r"[.!?](?:\s|$)", text))


def model_rows() -> None:
    print("model")
    from shout.transcribe import Transcriber, load_wav

    try:
        clips = make_clips()
    except (OSError, subprocess.SubprocessError) as e:
        check(False, "Windows TTS made the test clips", f"{type(e).__name__}: {e}")
        return
    t = Transcriber(Config())
    t.load()
    calls: list[dict] = []
    real = t.model.transcribe

    def spy(audio, **kw):
        calls.append(kw)
        return real(audio, **kw)

    t.model.transcribe = spy
    yours = Vocab.resolve(TARGETS, {})
    none = Vocab()
    t.transcribe(load_wav(clips["v1"][1]), none)          # pays the first GPU op
    t.transcribe(load_wav(clips["v1"][1]), yours)          # and the alignment's

    calls.clear()
    t.transcribe(load_wav(clips["v1"][1]), none)
    check(calls[-1].get("hotwords") is None and calls[-1].get("word_timestamps") is False,
          "an empty list decodes exactly as before: no hint, no word timestamps",
          f"hotwords={calls[-1].get('hotwords')!r} wts={calls[-1].get('word_timestamps')}")
    t.transcribe(load_wav(clips["v1"][1]), yours)
    check(calls[-1].get("hotwords") == yours.hint() and calls[-1].get("word_timestamps") is True,
          "with words, the hint is sent and word timestamps are on")

    audio = {k: load_wav(p) for k, (_, p) in clips.items()}
    out = {k: (t.transcribe(a, none), t.transcribe(a, yours)) for k, a in audio.items()}

    def names(i: int) -> tuple[int, int]:
        got = total = 0
        for k, (ref, _) in clips.items():
            for w in TARGETS:
                if w.lower() in ref.lower():
                    total += 1
                    got += w.lower() in out[k][i].lower()
        return got, total

    (base, total), (heard, _) = names(0), names(1)
    check(total - base >= 10,
          "precondition: without your words Whisper misses most of these names",
          f"{base} of {total} right")
    check(heard >= 0.8 * total and heard >= base + 10,
          "with your words it gets them (measured 25 of 29)",
          f"{base} -> {heard} of {total}")

    for k in LONG:
        ref = clips[k][0]
        r0, r1 = recall(ref, out[k][0]), recall(ref, out[k][1])
        check(r1 >= r0 - 0.01, f"{k}: no spoken word is lost to the hints",
              f"words kept {r0:.3f} without, {r1:.3f} with")
    e0 = sum(ends(o[0]) for o in out.values())
    e1 = sum(ends(o[1]) for o in out.values())
    check(e1 >= e0, "punctuation is unchanged (sentence ends)", f"{e0} -> {e1}")

    # Control: the production call with ONE change, word timestamps off. It
    # must lose words on the long clip, as measured, or the rows above are
    # blind to the bug they exist for.
    def no_wts(a, **kw):
        kw["word_timestamps"] = False
        return real(a, **kw)

    t.model.transcribe = no_wts
    blind = t.transcribe(audio["long"], yours)
    t.model.transcribe = spy
    r0 = recall(clips["long"][0], out["long"][0])
    rb = recall(clips["long"][0], blind)
    check(rb < r0 - 0.03,
          "control: hints WITHOUT word timestamps lose words on the long clip",
          f"words kept {r0:.3f} -> {rb:.3f}")

    sr = 16000
    rng = np.random.default_rng(1)
    breath = rng.standard_normal(sr // 2).astype(np.float32)
    breath = (np.convolve(breath, np.ones(8) / 8, mode="same")
              * np.hanning(len(breath)) * 0.08).astype(np.float32)
    quiet = {
        "silence": np.zeros(3 * sr, np.float32),
        "room tone": (rng.standard_normal(3 * sr) * 0.003).astype(np.float32),
        "a breath": np.concatenate([np.zeros(sr, np.float32), breath,
                                    np.zeros(sr, np.float32)]),
    }
    leaks = {k: t.transcribe(a, yours) for k, a in quiet.items()}
    check(not any(leaks.values()), "your words are never invented out of silence, "
          "room tone or a breath", "" if not any(leaks.values()) else str(leaks))
    jfk = load_wav(ROOT / "audio" / "jfk.wav")
    check(t.transcribe(jfk, none) == t.transcribe(jfk, yours),
          "speech holding none of your words comes back the same")

    fixed = t.transcribe(audio["L1"], Vocab.resolve(TARGETS, {"cache": "cash"}))
    unfixed = out["L1"][1]
    check("cache" in unfixed.lower() and "count the cash" in fixed.lower(),
          "a fix applies to the real transcript (Whisper hears 'cash' as 'cache' here)",
          f"{'cache' in unfixed.lower()=}")


def main() -> int:
    text_rows()
    config_rows()
    model_rows()
    passed = sum(rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          "your words: heard, fixed, and nothing lost")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
