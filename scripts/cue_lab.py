"""Audition the cue sounds and save the one you like, under a name of its own.

    .venv/Scripts/shoutw.exe scripts/cue_lab.py

`shoutw.exe`, not `pythonw.exe`: uv installed its CONSOLE trampoline under both
names on this machine (PE subsystem 3, byte-identical to python.exe), so
pythonw would open a terminal window beside the lab. See the 8 Sep lab note.

Pick a material, drag the sliders, hear it immediately. Nothing is written until
you press Save, and Save only touches the cue keys in config.json — every other
setting is left as it is.

*The six materials are read-only.* Dragging a knob while one of them is selected
renames what you are editing, so Save creates a NEW entry in the list rather than
redefining the material you started from. That matters because the material is
the thing you navigate by: if tuning `marimba` overwrote `marimba`, the list
would slowly stop describing anything, and there would be no way back to the
sound you liked last week. Your own saved voices ARE editable in place — saving
over a name you created updates it, which is what editing your own work should
do — and Delete removes one.

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
transcript, and it is a property of the SOUND — every material here is harmonic
because the VAD rejects tones, and a noisy one could break it.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
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
        self.saved: dict[str, dict] = dict(self.cfg.cue_presets)
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice,
                                   self.saved)
        self.volume = float(self.cfg.cue_volume)
        self.cues = Cues(enabled=True, volume=self.volume,
                         device=self.cfg.output_device, voice=self.voice)
        self._loading = False
        self._build_ui()
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play=None)
        self._greet()

    def _greet(self) -> None:
        """A voice carried as overrides on a built-in predates named saving, and
        reselecting that built-in would silently discard it. Say so on open
        rather than letting the list quietly disagree with what is playing."""
        n = len(Voice._clean(self.cfg.cue_voice))
        if n and self.cfg.cue_preset in PRESETS:
            self.name.setText(self._free_name(self.cfg.cue_preset))
            self.status.setText(
                f"Playing {self.cfg.cue_preset!r} with {n} tweak(s) that are not "
                f"saved under a name — Save to keep them as "
                f"{self.name.text()!r}, or rename first.")

    # -- ui ------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Material"))
        self.preset = QtWidgets.QComboBox()
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

        named = QtWidgets.QHBoxLayout()
        named.addWidget(QtWidgets.QLabel("Save as"))
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("a name for this voice")
        named.addWidget(self.name, 1)
        self.delete = QtWidgets.QPushButton("Delete")
        self.delete.clicked.connect(self._delete)
        named.addWidget(self.delete)
        save = QtWidgets.QPushButton("Save to config")
        save.setDefault(True)
        save.clicked.connect(self._save)
        named.addWidget(save)
        outer.addLayout(named)

        actions = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #8ab;")
        actions.addWidget(self.status, 1)
        revert = QtWidgets.QPushButton("Revert")
        revert.clicked.connect(self._revert)
        actions.addWidget(revert)
        outer.addLayout(actions)

        self.resize(580, 500)

    def _rebuild_list(self) -> None:
        """Materials first, then your own voices under a separator. Rebuilt
        rather than appended to, so a delete cannot leave a stale row."""
        self._loading = True
        try:
            self.preset.clear()
            self.preset.addItems(list(PRESETS))
            if self.saved:
                self.preset.insertSeparator(self.preset.count())
                self.preset.addItems(sorted(self.saved))
        finally:
            self._loading = False

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
            self.name.setText(self.voice.name)
            for fieldname, _, lo, hi, _dp in KNOBS:
                value = getattr(self.voice, fieldname)
                self.sliders[fieldname].setValue(self._to_slider(value, lo, hi))
            self.sliders["_volume"].setValue(self._to_slider(self.volume, 0.0, 1.0))
        finally:
            self._loading = False
        self._label_values()
        self.delete.setEnabled(self.voice.name in self.saved)

    def _label_values(self) -> None:
        for fieldname, _, _lo, _hi, dp in KNOBS:
            self.values[fieldname].setText(f"{getattr(self.voice, fieldname):.{dp}f}")
        self.values["_volume"].setText(f"{self.volume:.2f}")

    def _read_widgets(self) -> None:
        fields = {f: self._from_slider(self.sliders[f].value(), lo, hi)
                  for f, _, lo, hi, _dp in KNOBS}
        self.voice = replace(self.voice, **fields)
        self.volume = self._from_slider(self.sliders["_volume"].value(), 0.0, 1.0)

    def _differs_from(self, base: Voice) -> bool:
        """A knob counts as moved only if it moved by more than one slider step.
        Reading a slider back gives a quantized value, so an exact comparison
        calls knobs you never dragged moved."""
        for fieldname, _, lo, hi, _dp in KNOBS:
            if abs(getattr(self.voice, fieldname) - getattr(base, fieldname)) \
                    > (hi - lo) / STEPS:
                return True
        return self.voice.partials != base.partials

    def _free_name(self, base: str) -> str:
        taken = set(PRESETS) | set(self.saved)
        for n in range(2, 100):
            if (candidate := f"{base} {n}") not in taken:
                return candidate
        return f"{base} copy"

    # -- events --------------------------------------------------------------

    def _load_named(self, name: str) -> Voice:
        return (Voice.from_saved(name, self.saved[name]) if name in self.saved
                and name not in PRESETS else PRESETS[name])

    def _on_preset(self, name: str) -> None:
        if self._loading or not name:
            return
        self.voice = self._load_named(name)
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText("")

    def _on_slider(self) -> None:
        if self._loading:
            return
        self._read_widgets()
        self._label_values()
        # Editing a material renames what you are editing, so Save adds an entry
        # instead of redefining a shipped sound. Only while the box still holds
        # the material's own name — once it is your name, it stays yours.
        if self.name.text().strip() in PRESETS and self._differs_from(
                PRESETS[self.name.text().strip()]):
            self.name.setText(self._free_name(self.name.text().strip()))
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
        self.saved = dict(self.cfg.cue_presets)
        self.voice = Voice.resolve(self.cfg.cue_preset, self.cfg.cue_voice,
                                   self.saved)
        self.volume = float(self.cfg.cue_volume)
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText("Reverted to what is saved.")

    # -- persistence ---------------------------------------------------------

    def _read(self) -> dict:
        """The file, raw. `self.cfg` is a snapshot from construction and goes
        stale the moment Save writes — so any question about what is CURRENTLY
        configured has to be asked of the file. Caught by probe_lab on its first
        run: Delete read the active preset off the snapshot and left config.json
        naming a voice it had just removed."""
        path = config_dir() / "config.json"
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, keys: dict) -> Path:
        """Merge into config.json rather than rewriting it — the file may hold
        settings this window knows nothing about, and Config.save() would write
        this process's defaults over every one of them."""
        path = config_dir() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = self._read()
        raw.update(keys)
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        return path

    def _voice_dict(self) -> dict:
        """The whole voice, not a diff against a material. `partials` is in here
        and is not a slider: a voice tuned out of marimba needs marimba's
        partials or it will not sound like the thing you tuned."""
        data = {k: v for k, v in asdict(self.voice).items() if k != "name"}
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in data.items()}

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            self.status.setText("Give the voice a name first.")
            return
        if name in PRESETS:
            if self._differs_from(PRESETS[name]):
                self.name.setText(self._free_name(name))
                self.status.setText(
                    f"{name!r} is a built-in material and is not redefinable. "
                    f"Renamed to {self.name.text()!r} — press Save again.")
                return
            keys = {"cue_preset": name, "cue_voice": {},
                    "cue_volume": round(self.volume, 3)}
            what = f"material {name!r}"
        else:
            existed = name in self.saved
            self.saved[name] = self._voice_dict()
            keys = {"cue_preset": name, "cue_voice": {},
                    "cue_presets": self.saved,
                    "cue_volume": round(self.volume, 3)}
            what = f"{name!r} ({'updated' if existed else 'new'})"
        path = self._write(keys)
        self.voice = replace(self.voice, name=name)
        self._rebuild_list()
        self._sync_widgets()
        self.status.setText(
            f"Saved {what} to {path}. Quit Shout from the tray and relaunch it, "
            f"then re-run harness/probe_cues.py.")

    def _delete(self) -> None:
        name = self.voice.name
        if name not in self.saved:
            self.status.setText("Only your own saved voices can be deleted.")
            return
        del self.saved[name]
        keys = {"cue_presets": self.saved}
        # Not `self.voice.name`: deleting a voice you are merely LOOKING at must
        # leave the active one alone, and not `self.cfg` either — see _read.
        if self._read().get("cue_preset") == name:
            keys |= {"cue_preset": "blip", "cue_voice": {}}
        self._write(keys)
        self.cfg = Config.load()
        self.voice = PRESETS["blip"]
        self._rebuild_list()
        self._sync_widgets()
        self._apply(play="start")
        self.status.setText(f"Deleted {name!r}. Now on 'blip'.")

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
