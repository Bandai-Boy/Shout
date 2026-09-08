"""Local meeting transcription. Nothing leaves this machine.

Usage:
    python scripts/transcribe_meeting.py audio/meeting.m4a
    python scripts/transcribe_meeting.py audio/meeting.m4a --model large-v3
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def to_wav(src: Path) -> Path:
    """16kHz mono 16-bit PCM. No denoising: enhancement preprocessing is
    measurably WORSE for Whisper WER (arXiv 2603.04710)."""
    dst = ROOT / "audio" / f"{src.stem}.16k.wav"
    if dst.exists() and dst.stat().st_mtime > src.stat().st_mtime:
        print(f"[prep] reusing {dst.name}")
        return dst
    print(f"[prep] {src.name} -> {dst.name} (16kHz mono)")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-y", str(dst)],
        check=True,
    )
    return dst


def probe(path: Path) -> None:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_name,sample_rate,channels,duration",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    print(f"[probe] {' '.join(out.split())}")
    if "channels=2" in out:
        print("[probe] NOTE: stereo source. If each speaker was on a separate "
              "channel, splitting channels beats diarization entirely.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", type=Path)
    ap.add_argument("--model", default="large-v3-turbo")
    ap.add_argument("--compute-type", default="float16")
    ap.add_argument("--language", default="en")
    args = ap.parse_args()

    src = args.audio if args.audio.is_absolute() else ROOT / args.audio
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        return 1

    probe(src)
    wav = to_wav(src)

    from faster_whisper import WhisperModel

    print(f"[load] {args.model} / {args.compute_type} on cuda")
    t0 = time.time()
    model = WhisperModel(args.model, device="cuda", compute_type=args.compute_type)
    print(f"[load] {time.time() - t0:.1f}s")

    print("[asr] transcribing...")
    t1 = time.time()
    segments, info = model.transcribe(
        str(wav),
        language=args.language,
        beam_size=5,
        vad_filter=True,
        # The documented fix for long-audio repetition cascades: with True, one
        # bad window poisons every window after it.
        condition_on_previous_text=False,
        word_timestamps=True,
    )

    stem = src.stem
    txt_path = ROOT / "transcripts" / f"{stem}.txt"
    ts_path = ROOT / "transcripts" / f"{stem}.timestamped.txt"
    txt_path.parent.mkdir(exist_ok=True)

    plain, stamped, n = [], [], 0
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        n += 1
        plain.append(text)
        stamped.append(f"[{seg.start:7.2f} -> {seg.end:7.2f}]  {text}")
        if n % 25 == 0:
            print(f"[asr] {n} segments, {seg.end / 60:.1f} min of audio...")

    elapsed = time.time() - t1
    dur = info.duration
    txt_path.write_text(" ".join(plain), encoding="utf-8")
    ts_path.write_text("\n".join(stamped), encoding="utf-8")

    print(f"\n[done] {n} segments | audio {dur / 60:.1f} min | "
          f"transcribe {elapsed:.1f}s | {dur / elapsed:.1f}x realtime")
    print(f"[out]  {txt_path}")
    print(f"[out]  {ts_path}")
    print("\nNEXT: review for PHI before this leaves the machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
