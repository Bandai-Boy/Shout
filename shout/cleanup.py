"""Cleanup: hesitations out, self-corrections applied, run-ons punctuated.

Three layers, cheapest first. See CLAUDE.md, "Cleanup", for the measurements.

1. `strip_fillers` removes standalone um / uh / er, always. A word list, so it
   cannot touch a real word, and it costs nothing.
2. `needs_model` decides whether the dictation needs the model at all: a
   correction phrase ("actually", "sorry", "I mean", ...), a stutter, or a
   run-on with almost no sentence ends. Most dictations need none of that, and
   they paste with no added delay and Whisper's punctuation untouched.
3. A local Qwen3-4B, served by llama.cpp's `llama-server` on loopback, rewrites
   the text, and `guard` decides what of that survives. The final text is
   REBUILT FROM THE INPUT'S OWN TOKENS: the model only chooses which tokens
   are kept, the punctuation after each, and the first-letter case of plain
   words. A deletion must be a stutter or a short span that ends in a
   correction phrase. Anything else (a new word, a changed word, a contraction,
   a long deletion) and the text pastes as it was heard.

Fails open everywhere: no server, a timeout, an error or a refusal all paste
the text after layer 1. A missing real word is invisible corruption and a
leftover "actually" is one keystroke, which is what sets every choice here.

Nothing here logs words: only counts, timings and the KIND of a refusal.
"""
from __future__ import annotations

import ctypes
import difflib
import json
import logging
import re
import socket
import subprocess
import threading
import time
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("shout.cleanup")

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "tools" / "llama.cpp" / "llama-server.exe"
MODEL_REPO = "unsloth/Qwen3-4B-Instruct-2507-GGUF"
MODEL_FILE = "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"
MODEL_REVISION = "a06e946bb6b655725eafa393f4a9745d460374c9"

# The model is skipped on anything longer: its time grows with the length, and
# the measured ceiling (296ms) was taken on dictations under 70 words.
MAX_WORDS = 200
TIMEOUT_S = 1.0
START_TIMEOUT_S = 120

# --- layer 1: hesitations ---------------------------------------------------

# um, umm, uh, uhh, uhm, er, erm. Not "hmm" (often meant), not "like" or "you
# know" (usually real words), and never an all-caps token: ER and UM are words.
_FILLER = re.compile(r"(?:u+h+m*|u+m+|e+r+m*)", re.I)
_TRAIL = re.compile(r"^(.*?)([.,!?;:]*)$")


def _is_filler(core: str) -> bool:
    return bool(_FILLER.fullmatch(core)) and not (core.isupper() and len(core) > 1)


def strip_fillers(text: str) -> tuple[str, int]:
    """(text without hesitations, how many were removed)."""
    out: list[str] = []
    n = 0
    cap_next = False
    for tok in text.split():
        core, trail = _TRAIL.match(tok).groups()
        if _is_filler(core):
            n += 1
            # "should, uh, move": the comma before the filler goes with it.
            # "the case, um. Next": the sentence end it carried survives.
            if out and out[-1][-1:] == "," and trail.startswith(","):
                out[-1] = out[-1][:-1]
            elif trail[:1] in (".", "!", "?") and out:
                out[-1] = out[-1].rstrip(",;:") + trail[0]
            if not out or out[-1][-1:] in (".", "!", "?"):
                cap_next = True
            continue
        if cap_next and core[:1].islower():
            tok = tok[0].upper() + tok[1:]
        cap_next = False
        out.append(tok)
    return " ".join(out), n


# --- layer 2: does it need the model? -----------------------------------------

_TERMS = re.compile(
    r"\b(?:actually|sorry|i mean|i meant|no wait|wait no|scratch that|or rather|"
    r"rather|make that|correction)\b|,\s*no\s*,", re.I)
_STUTTER = re.compile(r"\b(\w+)\W+\1\b", re.I)
# Whisper's run-on mode drops inner punctuation altogether, commas included, so
# the test counts every mark: a long sentence with commas in it is not a run-on.
RUN_ON_WORDS = 15          # words per punctuation mark above which it is a run-on
RUN_ON_MIN = 16            # and only at this length or more


def ends(text: str) -> int:
    return len(re.findall(r"[.!?](?:\s|$)", text))


def needs_model(text: str) -> str | None:
    """Why this text should go to the model, or None to leave it alone."""
    n = len(text.split())
    if n == 0 or n > MAX_WORDS:
        return None
    if _TERMS.search(text):
        return "correction"
    if _STUTTER.search(text):
        return "stutter"
    marks = len(re.findall(r"[.,;:!?](?:\s|$)", text))
    if n >= RUN_ON_MIN and n / max(marks, 1) > RUN_ON_WORDS:
        return "run-on"
    return None


# --- layer 3: the guard ---------------------------------------------------------

EDIT_TERMS = [t.split() for t in (
    "actually", "sorry", "i mean", "i meant", "no wait", "wait no", "no", "wait",
    "scratch that", "or rather", "rather", "make that", "correction")]
MAX_REPARANDUM = 5
MAX_STUTTER = 3


def _key(core: str) -> str:
    return re.sub(r"[^a-z0-9']", "", core.lower().replace("’", "'"))


def _tokens(text: str) -> list[tuple[str, str, str]]:
    """(core, trailing punctuation, comparison key) per word; a token with no
    letters or digits (a lone dash) is not a word and is dropped."""
    out = []
    for t in text.split():
        core, trail = _TRAIL.match(t).groups()
        k = _key(core)
        if k:
            out.append((core, trail, k))
    return out


def _ends_in_term(span: list[str]) -> int:
    return max((len(t) for t in EDIT_TERMS
                if len(t) <= len(span) and span[-len(t):] == t), default=0)


def _deletion_ok(ks: list[str], i: int, j: int) -> bool:
    """Is deleting ks[i:j] a stutter or a correction? Checked at every
    equivalent position, since "the left case no wait the right case" can be
    aligned as deleting either "the left case no wait" or "left case no wait
    the", and only the first ends in the correction phrase."""
    n = j - i
    spans = [(i, j)]
    a, b = i, j
    while a > 0 and ks[a - 1] == ks[b - 1]:
        a, b = a - 1, b - 1
        spans.append((a, b))
    a, b = i, j
    while b < len(ks) and ks[a] == ks[b]:
        a, b = a + 1, b + 1
        spans.append((a, b))
    for a, b in spans:
        span = ks[a:b]
        if n <= MAX_STUTTER and ks[b:b + n] == span:
            return True
        t = _ends_in_term(span)
        if t and 1 <= n - t <= MAX_REPARANDUM:
            return True
    return False


def guard(src: str, out: str) -> tuple[str, str | None]:
    """(text to paste, None) when the model's edit is accepted, else (src, the
    kind of edit that was refused). The kind is a word like "insert", never the
    words themselves, so it can be logged."""
    s, o = _tokens(src), _tokens(out)
    if not s:
        return src, None
    ks, ko = [t[2] for t in s], [t[2] for t in o]
    kept: dict[int, int] = {}
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, ks, ko, autojunk=False).get_opcodes():
        if tag in ("replace", "insert"):
            return src, tag
        if tag == "delete" and not _deletion_ok(ks, i1, i2):
            return src, "delete"
        if tag == "equal":
            kept.update({i1 + d: j1 + d for d in range(i2 - i1)})
    words = []
    for i, (core, _, _) in enumerate(s):
        if i not in kept:
            continue
        ocore, otrail, _ = o[kept[i]]
        # The model may move a word's first letter between cases, and only on a
        # plain word: "tcg" stays "tcg" even if the model wrote "TCG", and a
        # name with inner capitals keeps them.
        plain = core[:1].isalpha() and core[1:] == core[1:].lower()
        if plain and ocore[:1].isalpha() and ocore[1:] == ocore[1:].lower():
            core = ocore[:1] + core[1:]
        if core == "i":
            core = "I"
        words.append(core + otrail)
    if not words:
        return src, "delete"
    text = " ".join(words)
    if text[-1] in ",;:":
        text = text[:-1]
    if text[-1] not in ".!?" and src.rstrip()[-1:] in (".", "!", "?"):
        text += src.rstrip()[-1]
    return text, None


# --- the model --------------------------------------------------------------------

SYSTEM = """You clean up dictated text. You may only do three things:
1. Delete stutters (a word repeated by accident).
2. When the speaker corrects themselves with a phrase like "actually", "sorry", "I mean", "no wait", "scratch that", "or rather" or "no", delete the wrong part and the correction phrase, and keep the replacement.
3. Fix punctuation and capitalisation, splitting run-on text into sentences.
Never add, reword, reorder or replace a word. Never change a number or a spelling. Never join or split words: keep "I am" as "I am" and "don't" as "don't". Words like "actually", "sorry", "like", "I mean", "no" and "you know" are usually real words: delete them only when they mark a self-correction. If nothing needs fixing, return the text exactly as given. Output only the text."""

SHOTS = [
    ("The meeting is on Tuesday, sorry, Wednesday at noon.",
     "The meeting is on Wednesday at noon."),
    ("I actually liked the second one better, sorry about that.",
     "I actually liked the second one better, sorry about that."),
    ("we shipped the order this morning it should arrive on friday let me know if it doesn't.",
     "We shipped the order this morning. It should arrive on Friday. Let me know if it doesn't."),
    ("Put it on the top shelf, no, the bottom shelf.", "Put it on the bottom shelf."),
]

# Goes through the model and is accepted by the guard: warmup proves both.
WARMUP_TEXT = "Leave the box by the front door, sorry, the side door."
WARMUP_WANT = "Leave the box by the side door."

SERVER_ARGS = [
    "-ngl", "99",              # every layer on the GPU
    "-c", "4096", "-np", "1",
    # The answer is mostly a copy of the question, which is what n-gram lookup
    # drafts well. Same text out (greedy decoding verifies every draft); on the
    # dictations that reach the model, short 132 -> 63ms median, long 364 ->
    # 288ms. Run-ons gain least: each inserted mark breaks the draft. CLAUDE.md.
    "--spec-type", "ngram-simple",
    "--spec-ngram-simple-size-n", "2", "--spec-ngram-simple-size-m", "16",
]


@dataclass(frozen=True)
class Cleaned:
    text: str          # what to paste
    heard: str         # what came in
    fillers: int       # hesitations removed by the word list
    why: str | None    # why the model was asked, None if it was not
    model_ms: float    # time in the model call, 0 if none
    refused: str | None  # the kind of edit the guard refused, or a failure


class _Job:
    """A Windows job object that kills what is in it when Shout's handle
    closes, so the server cannot outlive Shout, however Shout ends."""

    def __init__(self) -> None:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        self.handle = k32.CreateJobObjectW(None, None)

        class Basic(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("c", ctypes.c_size_t),
                        ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                        ("f", ctypes.c_size_t), ("g", wintypes.DWORD),
                        ("h", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", ctypes.c_uint64 * 6),
                        ("p", ctypes.c_size_t), ("q", ctypes.c_size_t),
                        ("r", ctypes.c_size_t), ("s", ctypes.c_size_t)]

        info = Extended()
        info.Basic.LimitFlags = 0x2000          # KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        self._k32 = k32

    def add(self, proc: subprocess.Popen) -> None:
        if not self._k32.AssignProcessToJobObject(self.handle,
                                                  wintypes.HANDLE(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Cleaner:
    """Owns the llama-server process. `clean()` is safe to call at any time
    from the commit thread: before `start()` finishes, after a crash, or with
    cleanup off, it returns the hesitation-free text and nothing else."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.ready = False
        self._proc: subprocess.Popen | None = None
        self._job: _Job | None = None
        self._port = 0
        # Never through a proxy: the server is on loopback.
        self._http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Blocking: launch the server, wait for it, warm it through clean().
        Logs and returns on any failure; cleanup then stays at layer 1."""
        if not self.enabled:
            log.info("cleanup is off")
            return
        try:
            from huggingface_hub import hf_hub_download
            model = hf_hub_download(MODEL_REPO, MODEL_FILE, revision=MODEL_REVISION,
                                    local_files_only=True)
        except Exception as e:
            log.warning("cleanup model is not downloaded (%s); run scripts\\install.ps1",
                        type(e).__name__)
            return
        if not SERVER.exists():
            log.warning("llama-server is not installed; run scripts\\install.ps1")
            return
        t0 = time.perf_counter()
        try:
            self._port = _free_port()
            self._proc = subprocess.Popen(
                [str(SERVER), "-m", model, "--host", "127.0.0.1",
                 "--port", str(self._port), *SERVER_ARGS],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                self._job = _Job()
                self._job.add(self._proc)
            except OSError as e:
                log.warning("cleanup server not in a job object (%s)", e)
            threading.Thread(target=self._drain, name="cleanup-log",
                             daemon=True).start()
            while not self._healthy():
                if self._proc.poll() is not None:
                    log.warning("cleanup server exited with %s", self._proc.returncode)
                    return
                if time.perf_counter() - t0 > START_TIMEOUT_S:
                    log.warning("cleanup server did not start in %ds", START_TIMEOUT_S)
                    return
                time.sleep(0.2)
        except OSError:
            # Smart App Control blocking the unsigned exe lands here.
            log.exception("cleanup server could not be launched")
            return
        load_s = time.perf_counter() - t0
        self.ready = True
        warm = self.clean(WARMUP_TEXT)
        if warm.text != WARMUP_WANT:
            log.warning("cleanup warmup came back wrong (refused=%s); cleanup is off",
                        warm.refused)
            self.ready = False
            return
        log.info("cleanup ready: server up in %.1fs, warmup %.0fms",
                 load_s, warm.model_ms)

    def stop(self) -> None:
        self.ready = False
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _drain(self) -> None:
        """The server's own output goes to shout.log only until it is
        listening (load and GPU lines, no dictation), then is read and dropped:
        an error path could print a request, and a full pipe would stall it."""
        listening = False
        for raw in self._proc.stdout:
            if listening:
                continue
            line = raw.decode("utf-8", "replace").rstrip()
            if "error" in line.lower() or "CUDA" in line or "listening" in line:
                log.info("llama-server: %s", line[:200])
            listening = "listening" in line

    def _healthy(self) -> bool:
        try:
            with self._http.open(f"http://127.0.0.1:{self._port}/health",
                                 timeout=1) as r:
                return r.status == 200
        except OSError:
            return False

    # -- the pass -----------------------------------------------------------

    def _ask(self, text: str) -> str:
        msgs = [{"role": "system", "content": SYSTEM}]
        for a, b in SHOTS:
            msgs += [{"role": "user", "content": a},
                     {"role": "assistant", "content": b}]
        msgs.append({"role": "user", "content": text})
        body = json.dumps({"messages": msgs, "temperature": 0,
                           "max_tokens": 2 * len(text.split()) + 64,
                           "cache_prompt": True}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self._port}/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with self._http.open(req, timeout=TIMEOUT_S) as r:
            return json.load(r)["choices"][0]["message"]["content"].strip()

    def clean(self, text: str) -> Cleaned:
        try:
            stripped, n = strip_fillers(text)
        except Exception:
            log.exception("filler strip failed")
            stripped, n = text, 0
        if not self.enabled:
            return Cleaned(text, text, 0, None, 0.0, None)
        why = needs_model(stripped)
        if why is None or not self.ready:
            return Cleaned(stripped, text, n, None, 0.0, None)
        t0 = time.perf_counter()
        try:
            out = self._ask(stripped)
        except Exception as e:
            ms = (time.perf_counter() - t0) * 1000
            return Cleaned(stripped, text, n, why, ms, type(e).__name__)
        ms = (time.perf_counter() - t0) * 1000
        final, refused = guard(stripped, out)
        return Cleaned(final, text, n, why, ms, refused)
