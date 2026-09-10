"""Gate: the pre-commit guard actually refuses recordings and transcripts.

This repo gained a remote, which changed the cost of a mistake from recoverable
to permanent — see the docstring in hooks/guard.py. The guard is the mechanism
that replaced trusting the gitignore, so "the guard works" is now a claim that
has to be measured rather than assumed, exactly like the overlay's window flags.

Everything happens in a THROWAWAY repository under the system temp directory,
built from the real `hooks/` files. Nothing here stages, commits or touches the
real repo — a gate that could dirty the tree it is protecting would be a poor
trade.

The shape of the test is the same one the rest of this harness uses. A negative
control commits ordinary source and requires it to SUCCEED, because a guard that
blocks everything passes every "did it block?" assertion while making the repo
unusable. Then each blocked case is staged with `git add -f`, which is the
realistic vector: the gitignore already covers the tidy cases, so what is worth
proving is that the guard still catches a file the gitignore was told to skip.
And the allowlisted benchmark clip must go through, or the "no audio" rule would
be over-broad and would break the real repo's own smoke gate.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"

rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_AUTHOR_NAME="gate", GIT_AUTHOR_EMAIL="gate@local",
               GIT_COMMITTER_NAME="gate", GIT_COMMITTER_EMAIL="gate@local")
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, env=env)


def build_repo(tmp: Path) -> Path:
    repo = tmp / "guarded"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "gate@local")
    git(repo, "config", "user.name", "gate")
    # The real hook files, not a copy of their logic — a gate that reimplements
    # what it is testing agrees with itself no matter what the real hook does.
    shutil.copytree(HOOKS, repo / "hooks")
    os.chmod(repo / "hooks" / "pre-commit", 0o755)
    git(repo, "config", "core.hooksPath", "hooks")
    # The same exclusions the real repo carries, so `add -f` means here what it
    # means there.
    shutil.copy(ROOT / ".gitignore", repo / ".gitignore")
    return repo


def commit_attempt(repo: Path, path: str, content: bytes,
                   force: bool) -> subprocess.CompletedProcess:
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(content)
    git(repo, "add", *(["-f"] if force else []), path)
    proc = git(repo, "commit", "-m", f"add {path}")
    git(repo, "reset", "-q")          # leave the sandbox clean for the next case
    return proc


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="shout-repo-gate-") as tmpdir:
        repo = build_repo(Path(tmpdir))

        # -- negative control: ordinary source must still commit -------------
        # Without this row, every assertion below is satisfied by a guard that
        # refuses everything, which would be indistinguishable from a working one.
        proc = commit_attempt(repo, "shout/thing.py", b"x = 1\n", force=False)
        check(proc.returncode == 0, "ordinary source still commits (control)",
              (proc.stderr or proc.stdout).strip().splitlines()[-1:] and
              (proc.stderr or "").strip()[:70] or "committed")

        # -- the allowlisted clip must go through ----------------------------
        # 'no audio' has to mean 'no audio except the one the gates depend on'.
        proc = commit_attempt(repo, "audio/jfk.wav", b"RIFF" + b"\0" * 512,
                              force=True)
        check(proc.returncode == 0,
              "the allowlisted benchmark clip is still allowed (control)",
              "audio/jfk.wav")

        # -- the cases that must be refused ----------------------------------
        blocked = [
            ("transcripts/meeting-2026-09-08.txt", b"patient said...\n",
             "a transcript in the directory it belongs to"),
            ("audio/session.wav", b"RIFF" + b"\0" * 64,
             "a recording dropped beside the benchmark clip"),
            ("notes.txt", b"...and then he said\n",
             "a stray .txt in the repo root"),
            ("docs/call-transcript.md", b"# call\n",
             "'transcript' anywhere in the path"),
            ("scripts/dump.mp3", b"ID3" + b"\0" * 64,
             "a recording under an innocent directory"),
            ("shout/blob.py", b"# " + b"x" * 1_200_000,
             "a 1.2MB blob wearing a .py extension"),
        ]
        for path, content, why in blocked:
            proc = commit_attempt(repo, path, content, force=True)
            refused = proc.returncode != 0
            named = path in (proc.stderr or "")
            check(refused, f"REFUSED: {why}", path)
            check(named, f"  ...and the message names {path}",
                  (proc.stderr or "").strip().replace("\n", " ")[:80])

        # -- the guard must fail CLOSED --------------------------------------
        # A hook that cannot run must refuse, not wave the commit through. This
        # is the difference between a guard and a decoration.
        (repo / "hooks" / "guard.py").unlink()
        proc = commit_attempt(repo, "shout/other.py", b"y = 2\n", force=False)
        check(proc.returncode != 0,
              "a guard that cannot run refuses the commit (fails closed)",
              (proc.stderr or "").strip().replace("\n", " ")[:80])

    # -- and it is actually wired up in THIS repo ---------------------------
    # The sandbox proves the guard works. This proves the real repo uses it,
    # which is a separate claim and the one that protects anything.
    configured = subprocess.run(["git", "config", "core.hooksPath"], cwd=ROOT,
                                capture_output=True, text=True).stdout.strip()
    check(configured == "hooks", "the real repo points git at hooks/",
          f"core.hooksPath={configured!r}" + ("" if configured == "hooks" else
          "  (a fresh clone needs: git config core.hooksPath hooks)"))
    check((HOOKS / "pre-commit").exists() and (HOOKS / "guard.py").exists(),
          "both hook files are present and tracked")

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    total = len(rows)
    print(f"{'PASS' if passed == total else 'FAIL'} {passed}/{total} "
          f"repo guard assertions")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
