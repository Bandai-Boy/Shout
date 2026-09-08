"""Generate assets/shout.ico — a microphone glyph in the tray icon's blue.

Drawn at 256px and downscaled by Pillow into the standard icon sizes. Kept as a
script rather than a checked-in binary blob so the icon can be regenerated or
recoloured without hunting for whatever tool made it.

    .venv/Scripts/python.exe scripts/make_icon.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "shout.ico"

BLUE = (70, 130, 200, 255)      # matches tray.py's idle state
WHITE = (255, 255, 255, 255)
S = 256


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, S - 1, S - 1), radius=56, fill=BLUE)

    # capsule
    d.rounded_rectangle((100, 52, 156, 150), radius=28, fill=WHITE)
    # pickup arc
    d.arc((78, 92, 178, 176), start=0, end=180, fill=WHITE, width=14)
    # stand
    d.line((128, 176, 128, 200), fill=WHITE, width=14)
    d.line((100, 200, 156, 200), fill=WHITE, width=14)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    draw().save(OUT, format="ICO", sizes=sizes)
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(sizes)} sizes)")


if __name__ == "__main__":
    main()
