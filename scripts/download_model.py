"""Download the speech model once, then prove it works the way Shout runs it.

    .venv/Scripts/python.exe scripts/download_model.py

This is the only part of Shout that touches the network. The app sets
HF_HUB_OFFLINE=1 and loads with local_files_only=True, so it cannot download
anything itself, and a fresh install cannot load a model until this has run.
scripts/install.ps1 runs it for you.

It fetches whatever `model` config.json names (large-v3-turbo by default),
loads it through the same Transcriber the app uses, and transcribes the JFK
reference clip. Getting the words right is the pass condition. Speed is only
reported: it depends on the GPU, and a slower card still dictates fine.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# The one process allowed online. shout.transcribe only setdefault()s the
# offline flag, so this wins. Importing shout.transcribe before faster_whisper
# also runs cuda_dlls.enable() first, which the CUDA DLL search depends on.
os.environ["HF_HUB_OFFLINE"] = "0"
# Without Developer Mode, Windows will not let huggingface_hub symlink its cache,
# and it says so in a long warning mid-install. It stores plain files instead,
# which for one download takes no extra space, so the warning only alarms.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from shout.config import Config  # noqa: E402
from shout.transcribe import Transcriber, load_wav  # noqa: E402
from faster_whisper.utils import download_model  # noqa: E402

EXPECTED = ("And so, my fellow Americans, ask not what your country can do for you, "
            "ask what you can do for your country.")


def words(text: str) -> list[str]:
    """Case and punctuation dropped: those can vary by GPU, and a comparison
    that failed on a comma would reject an install that works."""
    return re.sub(r"[^a-z ]", "", text.lower()).split()


def main() -> int:
    cfg = Config.load()
    print(f"Downloading the {cfg.model!r} model (one time; large-v3-turbo is 1.6 GB)...")
    t0 = time.perf_counter()
    path = download_model(cfg.model)
    print(f"  done in {time.perf_counter() - t0:.0f}s: {path}")

    print(f"Loading it offline on {cfg.device} ({cfg.compute_type}), the way Shout does.")
    print("  The first GPU run in a process can take ~30s while CUDA builds its kernels.")
    tr = Transcriber(cfg)
    try:
        load_s = tr.load()
        warm_s = tr.warmup()
    except Exception as e:
        print(f"\nFAILED to load the model on {cfg.device}: {type(e).__name__}: {e}")
        if cfg.device == "cuda":
            print("Shout needs an NVIDIA GPU (GTX 16-series / RTX 20-series or newer) and a "
                  "current driver. See Requirements in the README.")
        return 1

    t0 = time.perf_counter()
    text = tr.transcribe(load_wav(ROOT / "audio" / "jfk.wav"))
    ms = (time.perf_counter() - t0) * 1000
    if words(text) != words(EXPECTED):
        print(f"\nFAILED: the reference clip came back as {text!r}")
        return 1
    print(f"  loaded in {load_s:.1f}s, first-run warmup {warm_s:.1f}s, "
          f"then 11s of speech transcribed in {ms:.0f}ms")
    print("OK: the model is installed and transcribes correctly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
