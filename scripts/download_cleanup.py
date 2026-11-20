"""Download the cleanup model and its server once, then prove they work.

    .venv/Scripts/python.exe scripts/download_cleanup.py

Like download_model.py, one of the only processes allowed online. It fetches
llama.cpp's prebuilt Windows CUDA server (a pinned release, checked against
the SHA-256 written here) into tools/llama.cpp, and Qwen3-4B-Instruct
(Q4_K_M, a pinned revision, also checked) into the Hugging Face cache. Then it
starts the server the way Shout does and cleans one sentence: the pass
condition is the exact expected text. scripts/install.ps1 runs it for you.

To move to a newer llama.cpp or model: change the pins below AND in
shout/cleanup.py, then re-run harness/probe_cleanup.py. See CLAUDE.md.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from shout import cleanup  # noqa: E402

RELEASE = "b12555"
BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{RELEASE}/"
ZIPS = {
    f"llama-{RELEASE}-bin-win-cuda-13.5-x64.zip":
        "491610ff21b852ddcc8ed2ce4611345a3e805530f0e718706d249bc4656299fb",
    "cudart-llama-bin-win-cuda-13.5-x64.zip":
        "c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba",
}
MODEL_SHA256 = "3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def install_server() -> bool:
    dest = cleanup.SERVER.parent
    stamp = dest / "release.txt"
    if cleanup.SERVER.exists() and stamp.exists() and stamp.read_text().strip() == RELEASE:
        print(f"  llama.cpp {RELEASE} already installed in {dest}")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        unpacked = Path(tmp) / "unpacked"
        for name, want in ZIPS.items():
            path = Path(tmp) / name
            print(f"  downloading {name}...")
            with urllib.request.urlopen(BASE + name, timeout=60) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            got = sha256(path)
            if got != want:
                print(f"\nFAILED: {name} has SHA-256 {got}, expected {want}. Not installed.")
                return False
            with zipfile.ZipFile(path) as z:
                z.extractall(unpacked)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(unpacked, dest)
    stamp.write_text(RELEASE)
    print(f"  installed llama.cpp {RELEASE} in {dest}")
    return True


def install_model() -> str | None:
    from huggingface_hub import hf_hub_download
    print(f"  fetching {cleanup.MODEL_FILE} (one time, 2.5 GB)...")
    t0 = time.perf_counter()
    path = hf_hub_download(cleanup.MODEL_REPO, cleanup.MODEL_FILE,
                           revision=cleanup.MODEL_REVISION)
    got = sha256(Path(path))
    if got != MODEL_SHA256:
        print(f"\nFAILED: the model has SHA-256 {got}, expected {MODEL_SHA256}.")
        return None
    print(f"  done in {time.perf_counter() - t0:.0f}s: {path}")
    return path


def main() -> int:
    if not install_server() or not install_model():
        return 1
    print("Starting it the way Shout does, and cleaning one sentence...")
    c = cleanup.Cleaner(enabled=True)
    try:
        c.start()
        if not c.ready:
            print("\nFAILED: the cleanup server did not start (see above). Dictation "
                  "still works without it; cleanup then only removes um and uh.")
            return 1
        r = c.clean(cleanup.WARMUP_TEXT)
        if r.text != cleanup.WARMUP_WANT:
            print(f"\nFAILED: expected {cleanup.WARMUP_WANT!r}, got {r.text!r}")
            return 1
        print(f"  {cleanup.WARMUP_TEXT!r}\n  -> {r.text!r} in {r.model_ms:.0f}ms")
    finally:
        c.stop()
    print("OK: cleanup is installed and works on this GPU.")
    return 0


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO, format="  %(message)s")
    raise SystemExit(main())
