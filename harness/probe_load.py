"""Gate: a model load that Windows blocks is retried until it gets through.

On 17 Sep Smart App Control blocked av/video/stream.pyd at boot, while Defender
was still updating, and Shout came up with a tray and an orb and no model. The
same file loaded fine three minutes later. `Shout._load_model` now retries a
block, and this gate drives that real method.

A real block cannot be summoned on demand, so each child process raises one
where Python's own loader raises it: in `ExtensionFileLoader.create_module`, for
one named module, with Windows' own text for error 4551. The classifier is NOT
checked against that injected text, which would agree with itself: the first two
rows check it against the lines copied out of shout.log on 17 and 18 Sep. Those
two lines word the same block differently, and a classifier that matched only
the 17 Sep one shipped: it let the 18 Sep block through unretried.

Every child imports `shout.__main__` first, as the app does, so the modules the
fault can land on are the ones the model thread really loads. Measured over all
58 unsigned ones (the only files Smart App Control judges by reputation): 50
break the load and all 50 recover; the other 8 are optional accelerators whose
libraries fall back silently. `_overlapped` is here because it is the one that
needs `_forget_imports_since`: without it the retry dies on asyncio's
`base_events`, and the last control proves that.

Loads the real model in up to three children at once (~1.6GB of VRAM each).
"""
from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Copied from shout.log, 17 Sep 2026 10:37:12. A real block, not a built one.
OBSERVED = ("DLL load failed while importing stream: "
            "An Application Control policy has blocked this file.")
# Copied from shout.log, 18 Sep 2026 10:55:10: ctranslate2.dll, blocked at boot
# on the ctypes.CDLL that loads it. CPython strips the trailing period off a
# message it puts in an OSError, so this form is a period shorter than the one
# above -- which is exactly what the classifier missed.
OBSERVED_CDLL = ("[WinError 4551] "
                 "An Application Control policy has blocked this file")
JFK = "ask not what your country"


def child(spec: dict) -> dict:
    import importlib.machinery

    import shout.__main__ as app  # Shout's startup imports, as in the app
    from shout.config import Config

    text = ctypes.FormatError(spec["code"])
    loader = importlib.machinery.ExtensionFileLoader
    real_create = loader.create_module
    hits = []

    def create_module(self, mspec):
        if mspec.name == spec["target"] and (spec["blocks"] < 0
                                             or len(hits) < spec["blocks"]):
            hits.append(1)
            raise ImportError(f"DLL load failed while importing "
                              f"{mspec.name.rpartition('.')[2]}: {text}",
                              name=mspec.name, path=mspec.origin)
        return real_create(self, mspec)

    loader.create_module = create_module
    # The shipped number of retries, without the minutes between them.
    delays = spec.get("delays")
    app.BLOCK_RETRY_S = tuple(delays if delays is not None
                              else [0] * len(app.BLOCK_RETRY_S))
    if not spec.get("purge", True):
        app._forget_imports_since = lambda before: None

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logging.getLogger().addHandler(Capture(logging.WARNING))
    stub = SimpleNamespace(cfg=Config.load(), transcriber=None,
                           ready=threading.Event(), _stopping=False)
    notices, states = [], []
    stub.tray = SimpleNamespace(notify=notices.append)
    stub._set_state = states.append
    stub._publish_state = lambda: states.append("published")
    app.Shout._load_model(stub)

    out = {"ready": stub.ready.is_set(), "hits": len(hits), "notices": notices,
           "error_state": "error" in states,
           "retries": sum("retrying" in r.getMessage() for r in records),
           "last": ""}
    failures = [r for r in records if r.exc_info]
    if failures:
        exc = failures[-1].exc_info[1]
        out["last"] = f"{type(exc).__name__}: {exc}"[:90]
    if out["ready"]:
        from shout.transcribe import ROOT as ROOT_, load_wav
        out["text"] = stub.transcriber.transcribe(load_wav(ROOT_ / "audio" / "jfk.wav"))
    return out


def run_child(spec: dict) -> dict:
    proc = subprocess.run([sys.executable, __file__, "--child", json.dumps(spec)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(ROOT), timeout=150)
    for line in proc.stdout.splitlines():
        if line.startswith("CHILDROW "):
            return json.loads(line[len("CHILDROW "):])
    tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
    return {"crashed": tail[:90]}


def main() -> int:
    import shout.__main__ as app

    rows: list[tuple[bool, str, str]] = []

    def check(ok, name, detail=""):
        rows.append((bool(ok), name, detail))

    # -- the classifier, against the real message -------------------------------
    check(app.BLOCKED_BY_WINDOWS in OBSERVED,
          "Windows' 4551 text is in the ImportError logged on 17 Sep",
          repr(app.BLOCKED_BY_WINDOWS))
    check(app.BLOCKED_BY_WINDOWS in OBSERVED_CDLL,
          "Windows' 4551 text is in the ctypes.CDLL OSError logged on 18 Sep",
          repr(app.BLOCKED_BY_WINDOWS))
    missing_dll = ("DLL load failed while importing stream: "
                   + ctypes.FormatError(126))
    check(app.BLOCKED_BY_WINDOWS not in missing_dll,
          "a missing DLL is not mistaken for a block", missing_dll[-40:])

    total = len(app.BLOCK_RETRY_S)
    check(sum(app.BLOCK_RETRY_S) >= 180,
          "retries cover at least 3 minutes (17 Sep cleared within 3)",
          f"{sum(app.BLOCK_RETRY_S)}s over {total} retries")

    block = {"code": 4551}
    specs = {
        "stream_twice": {**block, "target": "av.video.stream", "blocks": 2},
        "overlapped": {**block, "target": "_overlapped", "blocks": 1},
        "ext": {**block, "target": "ctranslate2._ext", "blocks": 1},
        "fft": {**block, "target": "numpy.fft._pocketfft_umath", "blocks": 1},
        "forever": {**block, "target": "av.video.stream", "blocks": -1},
        "missing": {"code": 126, "target": "av.video.stream", "blocks": 1},
        "no_retry": {**block, "target": "av.video.stream", "blocks": 1,
                     "delays": []},
        "no_purge": {**block, "target": "_overlapped", "blocks": 1,
                     "purge": False},
    }
    with ThreadPoolExecutor(max_workers=3) as pool:
        res = dict(zip(specs, pool.map(run_child, specs.values())))

    def recovered(key, name, retries):
        r = res[key]
        ok = (r.get("ready") and JFK in r.get("text", "") and not r.get("notices")
              and not r.get("error_state") and r.get("retries") == retries
              and r.get("hits") == retries)
        check(ok, name, r.get("crashed") or
              f"retries={r.get('retries')} hits={r.get('hits')} "
              f"notices={r.get('notices')} text={r.get('text', '')[:32]!r}")

    recovered("stream_twice", "av/video/stream.pyd blocked twice: loads on the 3rd try", 2)
    recovered("overlapped", "_overlapped blocked once: asyncio re-imports and loads", 1)
    recovered("ext", "ctranslate2's engine blocked once: loads", 1)
    recovered("fft", "blocked inside load(), after the import: loads", 1)

    r = res["forever"]
    check(not r.get("ready") and r.get("retries") == total
          and r.get("hits") == total + 1 and r.get("error_state")
          and r.get("notices") == ["Windows blocked a file Shout needs — see shout.log"],
          "a block that never clears gives up after the shipped retries, and says so",
          r.get("crashed") or f"retries={r.get('retries')}/{total} "
          f"notices={r.get('notices')}")

    r = res["missing"]
    check(not r.get("ready") and r.get("retries") == 0 and r.get("error_state")
          and r.get("notices") == ["Model failed to load — see shout.log"],
          "any other import failure is reported at once, not retried",
          r.get("crashed") or f"retries={r.get('retries')} notices={r.get('notices')}")

    # -- controls: each proves a row above could fail ----------------------------
    r = res["no_retry"]
    check(not r.get("ready") and r.get("hits") == 1,
          "CONTROL: with no retries, one block leaves no model (17 Sep)",
          r.get("crashed") or f"ready={r.get('ready')} {r.get('last')}")
    r = res["no_purge"]
    check(not r.get("ready") and r.get("retries") == 1,
          "CONTROL: retrying without forgetting imports fails on _overlapped",
          r.get("crashed") or f"ready={r.get('ready')} {r.get('last')}")

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'PASS' if passed == len(rows) else 'FAIL'} {passed}/{len(rows)} "
          f"blocked-load assertions")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--child":
        print("CHILDROW " + json.dumps(child(json.loads(sys.argv[2]))), flush=True)
        raise SystemExit(0)
    raise SystemExit(main())
