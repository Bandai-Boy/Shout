"""Pre-commit guard: refuse to stage recordings or transcripts.

The gitignore already excludes `audio/*` and `transcripts/`, and until now that
was the whole defence. It is a PASSIVE one: it protects the paths that were
thought of, and nothing else. It does not stop `git add -f`, and it does not
stop a recording or a transcript that lands somewhere it was not expected — a
stray .txt in the repo root, a scratch .wav dropped beside jfk.wav while
debugging, a copy made under a new name. All three are plausible; all three
sail straight past a gitignore.

That gap only started to matter when this repo gained a remote. Locally a
mistake is recoverable — `git filter-repo` and it is genuinely gone. Once
pushed it is not: purging a blob from GitHub needs a history rewrite *and*
support intervention to clear cached views, and anything that already fetched
it keeps its copy. The asymmetry is the entire reason this file exists.

Deliberately tight rather than clever. Every rule here is a rule about a PATH or
a SIZE, because those are decidable. Sniffing prose to guess whether a .md
"looks like a transcript" would produce false positives on RESEARCH.md, PLAN.md
and this project's own handoffs — and a guard that cries wolf is a guard that
gets bypassed, which is worse than no guard.

What it does NOT do, stated plainly so nobody mistakes it for more than it is:
`git commit --no-verify` skips it, as it skips every hook. This stops accidents.
It does not stop a determined author, and nothing in git can.
"""
from __future__ import annotations

import subprocess
import sys

# Recordings, and the container formats a recording arrives in.
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma",
             ".webm", ".mp4", ".mkv", ".mov", ".avi", ".aiff", ".aif"}
# Transcript formats. `.txt` is included because this repo has no legitimate use
# for one — every document here is .md or .html — so the rule costs nothing and
# catches the most likely accident.
TEXT_EXT = {".txt", ".srt", ".vtt", ".sbv", ".ass", ".tsv"}

# The one recording that belongs here: a public benchmark clip the warmup and
# smoke gates both depend on. Exact path, not a pattern.
ALLOWED = {"audio/jfk.wav"}

MAX_BYTES = 1_048_576      # a staged blob bigger than this is not source


def staged_paths() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def staged_size(path: str) -> int:
    """Size of the blob as STAGED, not as it sits on disk — they differ when a
    file is modified after `git add`, and the staged one is what would ship."""
    try:
        return int(subprocess.run(["git", "cat-file", "-s", f":{path}"],
                                  capture_output=True, text=True,
                                  check=True).stdout.strip())
    except (subprocess.CalledProcessError, ValueError):
        return 0


def violation(path: str) -> str | None:
    low = path.lower()
    if low in {a.lower() for a in ALLOWED}:
        return None
    ext = low[low.rfind("."):] if "." in low.rsplit("/", 1)[-1] else ""
    if low.startswith("transcripts/"):
        return "lives under transcripts/, which may contain PHI"
    if "transcript" in low:
        return "has 'transcript' in its path"
    if low.startswith("audio/"):
        return "is under audio/ and is not the allowlisted benchmark clip"
    if ext in AUDIO_EXT:
        return f"is a recording ({ext})"
    if ext in TEXT_EXT:
        return f"is a transcript-shaped file ({ext})"
    size = staged_size(path)
    if size > MAX_BYTES:
        return f"is {size / 1_048_576:.1f}MB staged — too big to be source"
    return None


def main() -> int:
    bad = [(p, why) for p in staged_paths() if (why := violation(p))]
    if not bad:
        return 0
    print("\n  COMMIT BLOCKED - this repo has a remote, and a push cannot be "
          "taken back.\n", file=sys.stderr)
    for path, why in bad:
        print(f"    {path}\n        {why}", file=sys.stderr)
    print("\n  Audio and transcripts stay on this machine. If one of these is "
          "genuinely\n  source, add its exact path to ALLOWED in hooks/guard.py "
          "and say why.\n", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
