"""Audition the cue sounds and save the one you like.

    .venv/Scripts/shoutw.exe scripts/cue_lab.py

`shoutw.exe`, not `pythonw.exe`: uv installed its CONSOLE trampoline under both
names on this machine (PE subsystem 3, byte-identical to python.exe), so
pythonw would open a terminal window beside the lab. See the 8 Sep lab note.

Pick a material, drag the sliders, hear it immediately. Nothing is written until
you press Save, and Save only touches the cue keys in config.json — every other
setting is left as it is.

Two things this is deliberately NOT:

*Not its own synth.* It calls `shout.cues.build()`, the same function the app
calls, through the same `Cues` output stream. A lab with its own copy of the
oscillator would let you tune something the app then does not play — the same
shape of bug as a warmup that runs different code from the real path.

*Not a restart loop.* Auditioning by editing constants and restarting Shout costs
about fifteen seconds a try, which is far too slow to converge on taste; here a
slider move re-synthesizes and plays in a few milliseconds.

The knobs, roughly in the order they matter for "less bright":

    Root        the pitch everything is built from. This is the big one.
    Decay       0 is a held beep; higher is struck and ringing out, which is
                most of what separates "shrill" from "pleasant" at equal volume.
    Brightness  how much upper harmonic. 0 is a pure sine, the softest timbre
                there is; raise it for wood or glass character.
    Length      how long the notes ring. Decay makes long notes taper, not drone.
    Spread      how far apart the two notes are. Below 1 the interval narrows.
    Gap         silence between notes; what makes a blip read as two notes.

After saving: quit Shout from the tray and relaunch it, then re-run
`harness/probe_cues.py`. That gate asserts the cue does not change your
transcript, and it is a property of the SOUND — every preset here is harmonic
because the VAD rejects tones, and a noisy one could break it.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from shout.config import Config, config_dir  # noqa: E402
from shout.cues import (MAX_GESTURE_S, PRESETS, Cues, Voice,  # noqa: E402
                        build, duration_s, notes)

# (field, label, min, max, decimals) — sliders are integers, so each is scaled.
KNOBS = [
    ("root_hz", "Root", 150.0, 1200.0, 0),
    ("decay", "Decay", 0.0, 1.0, 2),
    ("bright", "Brightness", 0.0, 1.0, 2),
    ("length", "Length", 0.4, 3.0, 2),
    ("spread", "Spread", 0.4, 1.6, 2),
    ("gap_ms", "Gap", 0.0, 60.0, 0),
    ("gain", "Trim", 0.2, 1.0, 2),
]
STEPS = 1000


class Wave(QtWidgets.QWidget):
    """The gesture drawn end to end. Decay in particular is far easier to
    understand as a shape than as a number."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(76)
        self._audio = np.zeros(1, dtype=np.float32)

    def set_audio(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QtGui.QColor(22, 22, 28))
        cy = r.center().y() + 0.5
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 28), 1.0))
        p.drawLine(r.left(), int(cy), r.right(), int(cy))
        a = self._audio
        if len(a) < 2:
            p.end()
            return
        peak = max(1e-6, float(np.max(np.abs(a))))
        cols = max(1, r.width())
        edges = np.linspace(0, len(a), cols + 1).astype(int)
        p.setPen(QtGui.QPen(QtGui.QColor(120, 190, 255), 1.0))
        half = r.height() / 2.0 - 3.0
        for i in range(cols):
            seg = a[edges[i]:max(edges[i] + 1, edges[i + 1])]
            h = float(np.max(np.abs(seg))) / peak * half
            x = r.left() + i
            p.drawLine(QtCore.QPointF(x, cy - h), QtCore.QPointF(x, cy + h))
        p.end()


class Lab(QtWidgets.QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Shout — cue lab")
        self.cfg = Config.load()
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice)
        self.volume = float(self.cfg.cue_volume)
        self.cues = Cues(enabled=True, volume=self.volume,
                         device=self.cfg.output_device, voice=self.voice)
        self._loading = False
        self._build_ui()
        self._sync_widgets()
        self._apply(play=None)

    # -- ui ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Material"))
        self.preset = QtWidgets.QComboBox()
        self.preset.addItems(list(PRESETS))
        self.preset.currentTextChanged.connect(self._on_preset)
        top.addWidget(self.preset, 1)
        self.auto = QtWidgets.QCheckBox("Play on change")
        self.auto.setChecked(True)
        top.addWidget(self.auto)
        outer.addLayout(top)

        self.wave = Wave()
        outer.addWidget(self.wave)
        self.readout = QtWidgets.QLabel()
        self.readout.setStyleSheet("color: #9aa; font-family: Consolas, monospace;")
        outer.addWidget(self.readout)

        grid = QtWidgets.QGridLayout()
        grid.setVerticalSpacing(4)
        self.sliders: dict[str, QtWidgets.QSlider] = {}
        self.values: dict[str, QtWidgets.QLabel] = {}
        rows = KNOBS + [("_volume", "Volume", 0.0, 1.0, 2)]
        for row, (fieldname, label, lo, hi, dp) in enumerate(rows):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            s.setRange(0, STEPS)
            s.valueChanged.connect(self._on_slider)
            grid.addWidget(s, row, 1)
            v = QtWidgets.QLabel()
            v.setMinimumWidth(56)
            v.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                           | QtCore.Qt.AlignmentFlag.AlignVCenter)
            v.setStyleSheet("color: #9aa; font-family: Consolas, monospace;")
            grid.addWidget(v, row, 2)
            self.sliders[fieldname] = s
            self.values[fieldname] = v
        grid.setColumnStretch(1, 1)
        outer.addLayout(grid)

        play = QtWidgets.QHBoxLayout()
        for name, text in (("start", "Start"), ("stop", "Stop"),
                           ("latch", "Latch")):
            b = QtWidgets.QPushButton(text)
            b.clicked.connect(lambda _=False, n=name: self._play(n))
            play.addWidget(b)
        seq = QtWidgets.QPushButton("All three")
        seq.clicked.connect(self._play_all)
        play.addWidget(seq)
        outer.addLayout(play)

        actions = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #8ab;")
        actions.addWidget(self.status, 1)
        revert = QtWidgets.QPushButton("Revert")
        revert.clicked.connect(self._revert)
        actions.addWidget(revert)
        save = QtWidgets.QPushButton("Save to config")
        save.setDefault(True)
        save.clicked.connect(self._save)
        actions.addWidget(save)
        outer.addLayout(actions)

        self.resize(560, 460)

    # -- slider <-> voice ----------------------------------------------------

    @staticmethod
    def _to_slider(value: float, lo: float, hi: float) -> int:
        return int(round((value - lo) / (hi - lo) * STEPS))

    @staticmethod
    def _from_slider(pos: int, lo: float, hi: float) -> float:
        return lo + (hi - lo) * pos / STEPS

    def _sync_widgets(self) -> None:
        """Push the current voice into the widgets without re-triggering."""
        self._loading = True
        try:
            i = self.preset.findText(self.voice.name)
            self.preset.setCurrentIndex(i if i >= 0 else 0)
            for fieldname, _, lo, hi, _dp in KNOBS:
                value = getattr(self.voice, fieldname)
                self.sliders[fieldname].setValue(self._to_slider(value, lo, hi))
            self.sliders["_volume"].setValue(self._to_slider(self.volume, 0.0, 1.0))
        finally:
            self._loading = False
        self._label_values()

    def _label_values(self) -> None:
        for fieldname, _, _lo, _hi, dp in KNOBS:
            self.values[fieldname].setText(f"{getattr(self.voice, fieldname):.{dp}f}")
        self.values["_volume"].setText(f"{self.volume:.2f}")

    def _read_widgets(self) -> None:
        fields = {f: self._from_slider(self.sliders[f].value(), lo, hi)
                  for f, _, lo, hi, _dp in KNOBS}
        self.voice = replace(self.voice, **fields)
        self.volume = self._from_slider(self.sliders["_volume"].value(), 0.0, 1.0)

    # -- events --------------------------------------------------------------

    def _on_preset(self, name: str) -> None:
        if self._loading:
            return
        self.voice = PRESETS[name]
        self._sync_widgets()
        self._apply(play="start")

    def _on_slider(self) -> None:
        if self._loading:
            return
        self._read_widgets()
        self._label_values()
        self._apply(play="start" if self.auto.isChecked() else None)

    def _apply(self, play: str | None) -> None:
        self.cues.set_volume(self.volume)
        self.cues.set_voice(self.voice)
        self.wave.set_audio(build("latch", self.cues.rate, self.volume, self.voice))
        longest = max(duration_s(n, self.voice) for n in ("start", "stop", "latch"))
        peak = float(np.max(np.abs(build("start", self.cues.rate, self.volume,
                                         self.voice))))
        over = "  OVER THE BLIP CEILING" if longest > MAX_GESTURE_S else ""
        notes_hz = "  ".join(f"{f:.0f}Hz" for f, _ in notes("latch", self.voice))
        self.readout.setText(
            f"notes {notes_hz}    longest gesture {longest * 1000:4.0f}ms"
            f"    peak {peak:.3f}{over}")
        if play:
            self._play(play)

    def _play(self, name: str) -> None:
        self.cues.play(name)

    def _play_all(self) -> None:
        """start, stop, then latch, spaced so they do not cut each other off —
        `play()` replaces whatever is sounding."""
        delay = 0.0
        for name in ("start", "stop", "latch"):
            QtCore.QTimer.singleShot(int(delay * 1000),
                                     lambda n=name: self.cues.play(n))
            delay += duration_s(name, self.voice) + 0.25

    def _revert(self) -> None:
        self.cfg = Config.load()
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice)
        self.volume = float(self.cfg.cue_volume)
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText("Reverted to what is saved.")

    def _save(self) -> None:
        """Merge into config.json rather than rewriting it — the file may hold
        settings this window knows nothing about, and Config.save() would write
        this process's defaults over every one of them."""
        base = PRESETS[self.preset.currentText()]
        # A knob counts as touched only if it moved by more than one slider step.
        # Reading a slider back gives a quantized value, so an exact comparison
        # records knobs you never dragged as overrides and the saved preset stops
        # meaning what it says.
        overrides = {}
        for fieldname, _, lo, hi, _dp in KNOBS:
            value, was = getattr(self.voice, fieldname), getattr(base, fieldname)
            if abs(value - was) > (hi - lo) / STEPS:
                overrides[fieldname] = round(value, 4)
        path = config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            raw = {}
        raw.update({"cue_preset": base.name, "cue_voice": overrides,
                    "cue_volume": round(self.volume, 3)})
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        self.status.setText(
            f"Saved {base.name!r}"
            + (f" +{len(overrides)} tweak(s)" if overrides else "")
            + f" to {path}. Quit Shout from the tray and relaunch it, "
              f"then re-run harness/probe_cues.py.")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.cues.close()
        super().closeEvent(event)


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    lab = Lab()
    lab.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
