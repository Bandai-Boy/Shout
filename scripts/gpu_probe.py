import os, sys, time
os.environ["HF_HUB_OFFLINE"] = "1"
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import cuda_dlls
print("cuda dll dirs added:", cuda_dlls.enable())
import ctranslate2
print("ctranslate2:", ctranslate2.__version__)
print("cuda_device_count:", ctranslate2.get_cuda_device_count())
from faster_whisper import WhisperModel
GT = "and so my fellow americans ask not what your country can do for you ask what you can do for your country"
norm = lambda s: " ".join("".join(c for c in s.lower() if c.isalnum() or c.isspace()).split())
for ct in ["float16", "int8_float16"]:
    try:
        t0 = time.time(); m = WhisperModel("large-v3-turbo", device="cuda", compute_type=ct, local_files_only=True); load = time.time()-t0
        t1 = time.time()
        segs, info = m.transcribe("audio/jfk.wav", beam_size=5)
        text = "".join(s.text for s in segs); tr = time.time()-t1
        ok = "EXACT-MATCH" if norm(text) == GT else "DIFFERS"
        print(f"OK   {ct:14s} load={load:6.2f}s transcribe={tr:6.2f}s rtf={11.0/tr:6.1f}x  {ok}", flush=True)
        print(f"     {text.strip()}", flush=True)
        del m
    except Exception as e:
        print(f"FAIL {ct:14s} {type(e).__name__}: {str(e)[:160]}", flush=True)
