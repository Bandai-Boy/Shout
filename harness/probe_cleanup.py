"""Gate: cleanup takes out what it should and never a real word.

TEXT rows (no GPU) drive `shout.cleanup` directly: the hesitation word list
(with real words that look like fillers as its controls), the gate that
decides whether the model runs, and the guard, which must accept real
corrections and refuse every other kind of edit.

MODEL rows start the real `Cleaner` (llama-server, the pinned Qwen3-4B) and run
clips spoken by Windows' own TTS through the production call,
`Transcriber.transcribe_both(audio, vocab, cleaner)`, then score words. Run-ons
are made by stripping the punctuation from what Whisper heard, which is exactly
what its run-on mode returns (SAPI reads too evenly to trigger it). Measured 19
Nov 2026, which is where the floors come from: 0 words lost and 0 invented over
44 cases, 46 of 48 correction/hesitation words removed, 8 -> 26 sentence ends on
the run-ons, 63ms / 288ms median model time short / long on the dictations
that reach the model (the long ones are run-ons, which speculation helps least,
see shout/cleanup.py SERVER_ARGS). Controls: the scorer
must see a dropped and an invented word; a model that swaps a word must be
refused; a stopped server must fail open; a Shout killed hard must take the
server with it, and the same kill without the job object must not; and a log
record holding a dictated word must be caught by the privacy row.

Know what covers the guard. With it gutted (`return out, None`), 13 rows fail:
the 12 guard rows and the swapped-word loop row. The end-to-end "no word
lost / invented" rows do NOT: the shipped model, with this prompt, makes no
word-level mistake on these clips even unguarded. Those rows are about the
MODEL; Qwen3-1.7B, tried on 19 Nov, lost 24 real words there unguarded.

Needs the GPU, the installed cleanup model (scripts/install.ps1) and about a
minute. Safe while Shout runs: it synthesizes no keys.
"""
from __future__ import annotations

import ctypes
import difflib
import logging
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harness"))
TMP = Path(tempfile.mkdtemp(prefix="shout-cleanup-"))
os.environ["APPDATA"] = str(TMP)          # never the live config.json

from shout import cleanup  # noqa: E402
from shout.cleanup import guard, needs_model, strip_fillers  # noqa: E402

rows: list[bool] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append(bool(ok))
    print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    return bool(ok)


# -- text -------------------------------------------------------------------

FILLERS = [
    ("Um, so I think we should, uh, move it to Friday.",
     "So I think we should move it to Friday."),
    ("Uhh the scanner is, umm, still wrong.", "The scanner is still wrong."),
    ("Er, can you check the prices?", "Can you check the prices?"),
    ("It goes in the front case, uhm, next to the binder.",
     "It goes in the front case next to the binder."),
    ("The bug is in the price fold. Um, add a test.",
     "The bug is in the price fold. Add a test."),
    # controls: real words that look like hesitations
    ("Take him to the ER now.", "Take him to the ER now."),
    ("The UM campus is closed.", "The UM campus is closed."),
    ("Hmm, the umbrella and the Umbreon are hers.",
     "Hmm, the umbrella and the Umbreon are hers."),
    ("Uhura was right, er is not a word.", "Uhura was right, is not a word."),
]

GATE = [
    ("Let's meet at 2, actually 3.", "correction"),
    ("Send it to Bob. Sorry, Rob.", "correction"),
    ("Email them on Monday, no, Tuesday.", "correction"),
    ("I guess we we could drop it.", "stutter"),
    ("so the plan for next week is simple monday we price the new collection tuesday "
     "we photograph everything and wednesday it all goes online.", "run-on"),
    ("Check the prices before we leave.", None),
    ("No, that is not the card I ordered.", None),
    ("The test failed on the second run. I think the cache is stale. Clear it and "
     "run the suite again. Then send me the log.", None),
    (" ".join(["word"] * (cleanup.MAX_WORDS + 1)) + " actually.", None),
]

# (input, model output, accepted?, expected text when accepted)
GUARD = [
    ("Put the slabs in the left case, no wait, the right case.",
     "Put the slabs in the right case.", True, "Put the slabs in the right case."),
    ("Send the invoice to Bob. Sorry, Rob.", "Send the invoice to Rob.", True,
     "Send the invoice to Rob."),
    ("Let's meet at 2, actually 3, on Thursday.", "Let's meet at 3 on Thursday.", True,
     "Let's meet at 3 on Thursday."),
    ("I guess we we could drop it by $5.", "I guess we could drop it by 5 dollars", False, ""),
    ("I guess we we could drop it by $5.", "I guess we could drop it by 5.", True,
     "I guess we could drop it by $5."),
    ("the order shipped today it arrives friday",
     "The order shipped today. It arrives Friday.", True,
     "The order shipped today. It arrives Friday."),
    ("Check just tcg, then update VendorVault.",
     "Check just TCG, then update vendorvault.", True,
     "Check just tcg, then update VendorVault."),
    # refusals
    ("Let's meet at 2, actually 3, on Thursday.", "Let's meet at three on Thursday.",
     False, ""),
    ("Send the invoice to Bob.", "Send the invoice to Rob.", False, ""),
    ("No, that is not the card I ordered.", "That is not the card I ordered.", False, ""),
    ("Actually, I think the first one was fine.", "I think the first one was fine.",
     False, ""),
    ("I am going to send them back.", "I'm going to send them back.", False, ""),
    ("I like the blue one more than the red one.",
     "I like the blue one more than the red.", False, ""),
    ("We sold the big red binder full of cards, sorry, the box.", "We sold the box.",
     False, ""),
    ("Do not change the layout.", "Change the layout.", False, ""),
    ("It was fine.", "It was really fine.", False, ""),
]


def text_rows() -> None:
    print("text")
    for src, want in FILLERS:
        got, _ = strip_fillers(src)
        check(got == want, f"hesitations: {src[:44]!r}", "" if got == want else repr(got))
    for src, want in GATE:
        got = needs_model(src)
        check(got == want, f"model needed: {want or 'no'} for {src[:40]!r}",
              "" if got == want else f"got {got}")
    for src, out, accept, want in GUARD:
        text, refused = guard(src, out)
        ok = (refused is None) == accept and (text == want if accept else text == src)
        check(ok, f"guard {'accepts' if accept else 'refuses'}: {out[:44]!r}",
              "" if ok else f"refused={refused} text={text!r}")


# -- model --------------------------------------------------------------------

# (spoken, target). None: the target is what Whisper heard, nothing may go.
CASES = {
    "F1": ("Um, so I think we should, uh, move the meeting to Friday.",
           "So I think we should move the meeting to Friday."),
    "F2": ("Uhh the scanner is, umm, still matching the wrong set.",
           "The scanner is still matching the wrong set."),
    "F3": ("Er, can you check the prices before we leave?",
           "Can you check the prices before we leave?"),
    "F4": ("I guess we, uh, we could drop it by five dollars.",
           "I guess we could drop it by five dollars."),
    "F5": ("Uh, the Umbreon slab goes in the front case, um, next to the Charizard.",
           "The Umbreon slab goes in the front case next to the Charizard."),
    "F6": ("Okay so, um, run the gates and, uh, tell me what failed.",
           "Okay so run the gates and tell me what failed."),
    "C1": ("Let's meet at two, actually three, on Thursday.",
           "Let's meet at three on Thursday."),
    "C2": ("Send the invoice to Bob, sorry, Rob.", "Send the invoice to Rob."),
    "C3": ("Put the slabs in the left case, no wait, the right case.",
           "Put the slabs in the right case."),
    # Whisper itself drops "I mean six" here (measured), so it is a control.
    "C4": ("I need five, I mean six, booster boxes for the show.", None),
    "C5": ("The show starts Saturday, scratch that, Sunday at ten.",
           "The show starts Sunday at ten."),
    "C6": ("Drop the price to forty, or rather thirty five dollars.",
           "Drop the price to $35."),
    "C7": ("Email the buyer on Monday, no, Tuesday morning.",
           "Email the buyer on Tuesday morning."),
    "C8": ("We need the blue sleeves, I mean the black sleeves, for the binders.",
           "We need the black sleeves for the binders."),
    "N1": ("I like the blue one more than the red one.", None),
    "N2": ("You know the answer better than I do.", None),
    "N3": ("Actually, I think the first version was fine.", None),
    "N4": ("Sorry for the delay, the cards shipped late.", None),
    "N5": ("I mean what I say about the prices.", None),
    "N6": ("Two plus three is five, so the total is right.", None),
    "N7": ("We sold two, then three more on Sunday.", None),
    "N8": ("It was like a dream, honestly.", None),
    "N9": ("No, that is not the card I ordered.", None),
    "N10": ("Wait for the price to drop before you buy it.", None),
    "N11": ("He said sorry, but the box was already opened.", None),
    "N12": ("It was actually cheaper at the show than online.", None),
    "N13": ("Check TCGplayer and JustTCG, then update VendorVault.", None),
    "N14": ("The order was ten, not twelve, so refund two.", None),
    "R1": ("Alright so for the show this weekend I want the Umbreon and the Charizard "
           "slabs in the front case. The booster bundles go on the left side of the table. "
           "Check the current prices before we leave and update everything so the tags "
           "match. If the promo is still sitting there on Sunday we can drop it by five "
           "dollars.", None),
    "R2": ("I want to change how the scanner works in the app. Right now it takes a photo "
           "and then waits for the match, which feels slow. Instead it should show a "
           "spinner on the card and let me keep scanning. When the match comes back it "
           "fills in the name and the price. Do not change anything else about the layout.",
           None),
    "R3": ("Hey just wanted to let you know the package arrived today. Two of the cards had "
           "soft corners so I am going to send them back. The rest look great. Can you "
           "send a return label when you get a chance. Thanks again for the quick shipping.",
           None),
    "R4": ("The test failed on the second run but not the first. I think the cache is "
           "stale after the rebuild. Clear it and run the whole suite again. If it still "
           "fails send me the log and do not push anything until we know why.", None),
    "R5": ("Remind me to order more sleeves tomorrow. We are almost out of the black ones "
           "and the clear ones. Also check if the top loaders came in. The last box was "
           "short by about fifty.", None),
    "R6": ("So the plan for next week is simple. Monday we price the new collection. "
           "Tuesday we photograph everything. Wednesday it all goes online. Thursday and "
           "Friday we pack orders and ship them out.", None),
    "M1": ("Um so for Saturday we need the tables set up by eight, actually seven thirty, "
           "because the doors open at nine. Uh, bring the card mats and the price tags. "
           "The cash box goes under the main table.",
           "So for Saturday we need the tables set up by 7.30 because the doors "
           "open at nine. Bring the card mats and the price tags. The cash box goes under "
           "the main table."),
    "M2": ("Okay, uh, the bug is in the price fold. It rounds the total down, sorry, up, "
           "when it should not round at all. Um, add a test for it before you fix it.",
           "Okay, the bug is in the price fold. It rounds the total up when it should not "
           "round at all. Add a test for it before you fix it."),
}
# Words that must never reach shout.log. Picked from the clips above.
SECRET = ["invoice", "sleeves", "binders", "thursday", "scanner", "umbreon",
          "charizard", "meeting", "slabs", "package", "spinner", "photograph"]

NUM = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve".split())}
NUM |= {"thirty": "30", "forty": "40", "fifty": "50"}


def norm(s: str) -> list[str]:
    return [NUM.get(w, w) for w in re.findall(r"[a-z0-9']+", s.lower())]


def unmatched(a: list[str], b: list[str]) -> list[str]:
    """Words of a not matched, in order, by b."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    hit = {i for m in sm.get_matching_blocks() for i in range(m.a, m.a + m.size)}
    return [w for i, w in enumerate(a) if i not in hit]


def score(heard: str, target: str, out: str) -> tuple[list, list, int, int]:
    """(real words lost, words invented, should-go words, of which removed)."""
    h, t, o = norm(heard), norm(target), norm(out)
    keep = [w for w in t if w not in unmatched(t, h)]    # target words Whisper wrote
    go = unmatched(h, t)
    lost = unmatched(keep, o)
    invented = unmatched(o, h)
    left = [w for w in unmatched(o, keep) if w not in invented]
    return lost, invented, len(go), len(go) - len(left)


def flatten(s: str) -> str:
    """Whisper's run-on mode: the same words, no inner punctuation."""
    s = re.sub(r"[.,?!;:](?=\s|$)", "", s).strip()
    return s[:1].upper() + s[1:] + "."


def leaks(records: list[logging.LogRecord]) -> list[str]:
    return [w for r in records for w in SECRET if w in r.getMessage().lower()]


def make_clips() -> dict[str, Path]:
    from probe_words import PS
    out = TMP / "clips"
    out.mkdir()
    script, listing = TMP / "tts.ps1", TMP / "clips.txt"
    script.write_text(PS, encoding="utf-8")
    listing.write_text("\n".join(f"{k}|0|{s}" for k, (s, _) in CASES.items()),
                       encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(script), "-OutDir", str(out), "-ListFile", str(listing)],
                   check=True, capture_output=True, timeout=300)
    return {k: out / f"{k}.wav" for k in CASES}


def alive(pid: int) -> bool:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)       # QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k32.GetExitCodeProcess(h, ctypes.byref(code))
    k32.CloseHandle(h)
    return code.value == 259                      # STILL_ACTIVE


def orphan_after_kill(job: bool) -> tuple[bool, str]:
    """Start a Cleaner in a child, kill the child hard, and report whether its
    server is still running. The child reports its OWN pid: the venv's
    python.exe is uv's trampoline, so Popen.pid is not the process to kill."""
    child = subprocess.Popen([sys.executable, __file__, "--child",
                              "job" if job else "nojob"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    me = server = None
    t0 = time.time()
    for line in child.stdout:
        if line.startswith("CHILD "):
            me, server = map(int, line.split()[1:3])
            break
        if time.time() - t0 > 90:
            break
    if not server:
        child.kill()
        return False, "child never reported a server"
    os.kill(me, 9)                                # TerminateProcess, no cleanup
    child.wait(10)
    deadline = time.time() + 5
    while alive(server) and time.time() < deadline:
        time.sleep(0.2)
    survived = alive(server)
    if survived:
        os.kill(server, 9)
    return survived, f"server pid {server}"


def child(mode: str) -> None:
    if mode == "nojob":
        class NoJob:
            def __init__(self):
                raise OSError("job object disabled by the probe's control")
        cleanup._Job = NoJob
    c = cleanup.Cleaner()
    c.start()
    print(f"CHILD {os.getpid()} {c._proc.pid if c._proc else 0}", flush=True)
    time.sleep(120)


def model_rows() -> None:
    print("model")
    from shout import cuda_dlls
    cuda_dlls.enable()
    from shout.config import Config
    from shout.transcribe import Transcriber, load_wav
    from shout.words import Vocab

    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logging.getLogger().addHandler(Capture())
    logging.getLogger().setLevel(logging.INFO)

    c = cleanup.Cleaner()
    c.start()
    if not check(c.ready, "the cleanup server starts and warms up through clean()",
                 "is it installed? scripts/install.ps1"):
        return
    try:
        clips = make_clips()
        t = Transcriber(Config())
        t.load()
        t.warmup()
        none = Vocab()
        results, timing = {}, []
        for k, path in clips.items():
            text, heard = t.transcribe_both(load_wav(path), none, c)
            results[k] = (heard, text)
        # run-ons: Whisper's own words with the punctuation gone
        for k in [k for k in CASES if k[0] in "RM"]:
            flat = flatten(results[k][0])
            r = c.clean(flat)
            results[k + "flat"] = (flat, r.text)
        for k, (heard, out) in results.items():
            r = c.clean(heard)                    # for the timing only
            if r.why:
                timing.append((r.model_ms, len(heard.split())))

        lost, invented, go, removed, bad = 0, 0, 0, 0, []
        for k, (heard, out) in results.items():
            target = CASES[k.replace("flat", "")][1]
            if target is None or k[0] in "NR":
                target = heard if "flat" not in k else results[k.replace("flat", "")][0]
            l, i, g, r = score(heard, target, out)
            lost += len(l)
            invented += len(i)
            go += g
            removed += r
            if l or i:
                bad.append(f"{k}: lost {l} invented {i}")
        check(lost == 0, "no real word lost, over every case", "; ".join(bad))
        check(invented == 0, "no word invented, over every case")
        check(removed >= 44, f"corrections and hesitations removed: {removed} of {go}",
              "floor 44 (measured 46 of 48)")
        e_in = sum(cleanup.ends(results[k][0]) for k in results if "flat" in k)
        e_out = sum(cleanup.ends(results[k][1]) for k in results if "flat" in k)
        check(e_out >= 22, f"run-ons punctuated: {e_in} -> {e_out} sentence ends",
              "floor 22 (measured 26)")
        controls = [k for k in CASES if k[0] == "N"]
        same = [k for k in controls if norm(results[k][0]) == norm(results[k][1])]
        check(len(same) == len(controls),
              f"real 'actually', 'sorry', 'I mean', 'no', 'like' left alone: "
              f"{len(same)} of {len(controls)}")
        h, o = results["C1"]
        check(h != o and norm(o) == norm("Let's meet at 3 on Thursday"),
              "transcribe_both returns what was heard beside what is pasted",
              f"{o!r}")

        # scorer controls: it must see a dropped word and an invented one
        l, i, _, _ = score("We sold two then three more", "We sold two then three more",
                           "We sold two more")
        check(l == ["then", "3"], "control: the scorer sees a dropped word", str(l))
        l, i, _, _ = score("We sold two", "We sold two", "We sold twenty two")
        check(i == ["twenty"], "control: the scorer sees an invented word", str(i))

        # guard in the loop: a model that swaps a word is refused
        real_ask = c._ask
        c._ask = lambda text: text.replace("Bob", "Rob").replace(", sorry,", "")
        r = c.clean("Send the invoice to Bob, sorry, Rob.")
        c._ask = real_ask
        check(r.refused in ("replace", "insert")
              and r.text == "Send the invoice to Bob, sorry, Rob.",
              "a model that changes a word is refused and the text pastes as heard",
              f"refused={r.refused}")

        short = [ms for ms, n in timing if n <= 20]
        long_ = [ms for ms, n in timing if n > 20]
        ms_s, ms_l = statistics.median(short), statistics.median(long_)
        check(ms_s <= 150, f"model time, short dictations: median {ms_s:.0f}ms "
              f"(n={len(short)}, max {max(short):.0f})", "ceiling 150 (measured 63)")
        check(ms_l <= 400, f"model time, long dictations: median {ms_l:.0f}ms "
              f"(n={len(long_)}, max {max(long_):.0f})", "ceiling 400 (measured 288)")

        found = leaks(records)
        check(not found and len(records) > 20,
              f"shout.log holds no dictated words ({len(records)} records read)",
              str(sorted(set(found))))
        planted = logging.LogRecord("x", logging.INFO, "", 0, "cleanup: the invoice",
                                    None, None)
        check(leaks([planted]) == ["invoice"], "control: a record holding a word is caught")
    finally:
        c.stop()

    r = c.clean("Let's meet at 2, actually 3.")
    check(r.text == "Let's meet at 2, actually 3." and r.why is None,
          "a stopped server fails open: hesitations only, no wait")
    c.ready = True                                   # as if a crash went unnoticed
    t0 = time.perf_counter()
    r = c.clean("Uh, let's meet at 2, actually 3.")
    waited = time.perf_counter() - t0
    check(r.text == "Let's meet at 2, actually 3." and r.refused is not None
          and waited < cleanup.TIMEOUT_S + 0.5,
          f"a dead server fails open within the timeout ({waited * 1000:.0f}ms, "
          f"{r.refused})")

    survived, detail = orphan_after_kill(job=True)
    check(not survived, "Shout killed hard takes the cleanup server with it", detail)
    survived, detail = orphan_after_kill(job=False)
    check(survived, "control: without the job object the server outlives Shout", detail)


def main() -> int:
    text_rows()
    model_rows()
    passed = sum(rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          "cleanup: removes what it should, never a real word")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--child"]:
        child(sys.argv[2])
    else:
        sys.exit(main())
