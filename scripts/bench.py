import os, sys, time, statistics
os.environ["HF_HUB_OFFLINE"] = "1"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cuda_dlls; cuda_dlls.enable()
from faster_whisper import WhisperModel

AUDIO, DUR, N = "audio/jfk.wav", 11.0, 3
def run(m):
    segs, _ = m.transcribe(AUDIO, beam_size=5)
    return "".join(s.text for s in segs)

for ct in ["int8_float16", "float16"]:
    t0 = time.time(); m = WhisperModel("large-v3-turbo", device="cuda", compute_type=ct, local_files_only=True)
    load = time.time() - t0
    t1 = time.time(); run(m); warm = time.time() - t1          # first call pays CUDA warmup
    times = []
    for _ in range(N):
        t = time.time(); run(m); times.append(time.time() - t)  # steady state
    med = statistics.median(times)
    print(f"{ct:14s} load={load:5.2f}s  FIRST-CALL={warm:6.2f}s  "
          f"warm-median={med:5.2f}s ({DUR/med:5.1f}x realtime)  "
          f"60min-meeting~{3600/(DUR/med)/60:.1f}min", flush=True)
    del m
