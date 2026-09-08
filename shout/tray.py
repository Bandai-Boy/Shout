"""Tray icon. The only feedback surface in session 1 — the overlay comes later."""
from __future__ import annotations

import logging

import pystray
from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# state -> (fill, ring)
_COLORS = {
    "loading": ((110, 110, 110, 255), None),
    "idle": ((70, 130, 200, 255), None),
    "recording": ((220, 60, 60, 255), None),
    "latched": ((220, 60, 60, 255), (255, 210, 60, 255)),
    "working": ((235, 170, 50, 255), None),
    "error": ((120, 30, 30, 255), None),
}

_LABELS = {
    "loading": "Shout — loading model",
    "idle": "Shout — ready",
    "recording": "Shout — recording",
    "latched": "Shout — recording (hands-free)",
    "working": "Shout — transcribing",
    "error": "Shout — error",
}


def _image(state: str) -> Image.Image:
    fill, ring = _COLORS.get(state, _COLORS["idle"])
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if ring:
        d.ellipse((2, 2, 62, 62), outline=ring, width=6)
        d.ellipse((16, 16, 48, 48), fill=fill)
    else:
        d.ellipse((10, 10, 54, 54), fill=fill)
    return img


class Tray:
    def __init__(self, on_quit) -> None:
        self._state = "loading"
        self.icon = pystray.Icon(
            "shout",
            _image("loading"),
            _LABELS["loading"],
            menu=pystray.Menu(pystray.MenuItem("Quit Shout", on_quit)),
        )

    def set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        try:
            self.icon.icon = _image(state)
            self.icon.title = _LABELS.get(state, "Shout")
        except Exception:
            log.exception("updating the tray icon")

    def notify(self, message: str, title: str = "Shout") -> None:
        try:
            self.icon.notify(message, title)
        except Exception:
            log.debug("tray notification failed", exc_info=True)

    def run(self) -> None:
        self.icon.run()

    def run_detached(self) -> None:
        """Run the tray on its own thread, leaving the main thread for Tk.

        pystray's win32 backend creates its window and pumps its message loop
        inside _run(), so both land on whichever thread calls it — detaching is
        the backend's own supported integration path, not a workaround.
        """
        self.icon.run_detached()

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass
