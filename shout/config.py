"""Settings, persisted as JSON under %APPDATA%/Shout/config.json."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Shout"


@dataclass
class Config:
    # --- model ---
    model: str = "large-v3-turbo"
    compute_type: str = "float16"
    device: str = "cuda"
    beam_size: int = 5
    language: str = "en"

    # --- audio ---
    # None = follow the Windows default mic, live. Read shout.devices before
    # setting an index: an MME index is a Windows device NUMBER, and Windows
    # renumbers them whenever the default changes.
    input_device: int | None = None
    # 0 = open the mic on chord-down and close on release (chosen: session 1).
    # >0 keeps the stream open with a rolling buffer of this length, which removes
    # first-syllable clipping at the cost of a permanent mic-in-use indicator.
    preroll_ms: int = 0

    # --- gestures (milliseconds) ---
    tap_max_ms: int = 300            # chord held longer than this is a deliberate hold
    latch_window_ms: int = 350       # release -> second press inside this = double-tap
    ptt_ceiling_s: int = 120         # hard stop for a held push-to-talk
    latch_ceiling_s: int = 600       # hard stop for a hands-free latched session

    # --- injection ---
    modifier_wait_ms: int = 2000     # max wait for Ctrl/Win to be released before paste
    clipboard_restore_ms: int = 300  # delay before restoring the previous clipboard
    restore_clipboard: bool = True

    # --- feedback ---
    cues: bool = True                # audio blips on start / stop / latch
    cue_volume: float = 0.25         # 0.0-1.0, amplitude of the synthesized tones
    # How those three gestures sound. `cue_preset` names a material in
    # shout.cues.PRESETS; `cue_voice` overrides individual fields on top of it.
    # Both are what `scripts/cue_lab.py` writes when you save an audition.
    cue_preset: str = "blip"
    cue_voice: dict = field(default_factory=dict)
    # Voices saved under a name of their own, name -> the voice's full field
    # dict. `cue_preset` may name one of these instead of a built-in, which is
    # how a tuned material survives without shadowing the material it came from.
    cue_presets: dict = field(default_factory=dict)
    # None = follow the Windows default output, live. Same caveat on indexes as
    # input_device; see shout.devices.
    output_device: int | None = None
    overlay: bool = True             # the state pill above the taskbar

    # --- diagnostics ---
    log_level: str = "INFO"

    @classmethod
    def load(cls) -> "Config":
        try:
            return cls.read()
        except (OSError, json.JSONDecodeError):
            return cls()

    @classmethod
    def read(cls) -> "Config":
        """load() without the fallback to defaults. A file caught mid-write does
        not parse, and a caller that already holds a config should keep it
        rather than put every setting back to its default for that moment."""
        path = config_dir() / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> Path:
        path = config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path
