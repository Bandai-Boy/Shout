"""Gate: the Shout window. The cue lab writes a config that resolves back to the
voice it played, and the recent dictations page gives back what you said.

The lab is now the only way a voice comes into existence, and it hands its work
to the app through a file. Every way that can go wrong is silent: the app plays
a sound nobody auditioned, and `probe_cues` — which reads the same config —
then measures that wrong sound and passes. Nothing surfaces until a cue sounds
different from the one you saved, weeks later, with no way to tell whether the
lab wrote it wrong or the app read it wrong.

So the row that carries this gate is the round trip: construct the real `Lab`
against a throwaway `APPDATA`, press its real Save, then resolve the file exactly
as `shout/__main__.py` does and compare the resulting `Voice` field for field
against the one the lab had in hand. A control proves that comparison is not
vacuous.

The rest is the promise that made named presets worth building: **a material is
never redefined.** Tuning `marimba` must leave `marimba` in the list sounding
like marimba, or the six names slowly stop meaning anything and there is no way
back to a sound you liked. That is asserted from both directions — editing a
material renames what you are editing, and saving under a material's name with
changes is refused.

Then the two halves of "Save does what it says". The sliders: a click on the
track jumps there, and a drag auditions once, on release, rather than once per
value change. That stacked restarts of the cue into a screech. The drag row
carries a precondition that the drag really moved the slider, because "no plays
during the drag" passes just as well on a drag that never happened. And the app:
a real `Shout`, built from the same throwaway config and left running, must be
playing the lab's saved voice within a poll or two, with no restart. That was
missing entirely until 10 Sep. The lab wrote the file and the app only read it
at launch, so Save looked like it did nothing.

The recent page is the way back to a paste that landed nowhere, so its rows are
about getting the RIGHT words back: a dictation appears without anything
refreshing the page, Copy on an older row copies that row and not the newest,
a clipped long take still copies whole, the copy is marked private, and the
list stops at its size. The clipboard it borrows is put back, marked private.

The widget is mapped with `WA_DontShowOnScreen`, which runs the real layout
without ever creating a visible window — this is an ordinary activating window,
so a plain `show()` would steal focus from whatever is in front. Layout
assertions need that mapping: an unmapped widget reports every child invisible,
so a "nothing is clipped" sweep over it passes by measuring nothing.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before any config read: every path below must land in the throwaway tree, not
# in the real %APPDATA%\Shout\config.json that the running app is using.
TMP = Path(tempfile.mkdtemp(prefix="shout_lab_"))
os.environ["APPDATA"] = str(TMP)
CONFIG = TMP / "Shout" / "config.json"

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from shout import inject as inj  # noqa: E402
from shout import window as ui  # noqa: E402
from shout.__main__ import CONFIG_POLL_MS, Shout  # noqa: E402
from shout.config import Config  # noqa: E402
from shout.cues import PRESETS, Voice  # noqa: E402
from shout.recent import SIZE, Recent  # noqa: E402

inj.user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]

# The config a pre-named-presets session left behind: a material plus an override
# layer, which is what Gabe's real config held on 8 Sep. Opening the lab on one
# of these is the migration case, so it is the state this gate starts from.
LEGACY = {
    "log_level": "DEBUG",           # a key the lab knows nothing about
    "cue_preset": "blip",
    "cue_voice": {"root_hz": 369.45, "decay": 0.694, "bright": 0.074,
                  "length": 0.7588, "gap_ms": 5.52},
    "cue_volume": 0.523,
}

rows: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    rows.append((bool(ok), name, detail))
    return bool(ok)


def write_config(data: dict) -> None:
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_config() -> dict:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


E = QtCore.QEvent.Type
LEFT, NOBUTTON = QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.NoButton


def mouse(widget, kind, x: float, button, buttons) -> None:
    """A real QMouseEvent sent straight to the widget, so nothing moves the
    user's actual cursor."""
    pos = QtCore.QPointF(x, widget.height() / 2.0)
    QtWidgets.QApplication.sendEvent(widget, QtGui.QMouseEvent(
        kind, pos, widget.mapToGlobal(pos), button, buttons,
        QtCore.Qt.KeyboardModifier.NoModifier))


def drag(widget, x0: float, x1: float, steps: int = 25) -> None:
    mouse(widget, E.MouseButtonPress, x0, LEFT, LEFT)
    for i in range(1, steps + 1):
        mouse(widget, E.MouseMove, x0 + (x1 - x0) * i / steps, NOBUTTON, LEFT)


def release(widget, x: float) -> None:
    mouse(widget, E.MouseButtonRelease, x, LEFT, NOBUTTON)


def pump(app, seconds: float, until=lambda: False) -> float | None:
    """Run the event loop for up to `seconds`; the elapsed time once `until`
    holds, else None."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        if until():
            return time.perf_counter() - t0
        time.sleep(0.01)
    return None


def same_sound(a: np.ndarray, b: np.ndarray) -> bool:
    """Within 1% of peak, sample for sample. Not exact: Save rounds the voice to
    4 decimals and the volume to 3."""
    return len(a) == len(b) and float(np.max(np.abs(a - b))) <= 0.01 * float(
        np.max(np.abs(b)))


def clipboard_is_private() -> bool:
    """The marker that keeps a copy out of Clipboard History and the cloud."""
    fmt = inj.user32.RegisterClipboardFormatW(inj._EXCLUDE)
    return bool(fmt) and bool(inj.user32.IsClipboardFormatAvailable(fmt))


def squeezed(w) -> list[tuple[str, int, int]]:
    """Every visible widget rendering under the height it needs. For wrapped
    text that is the height at its CURRENT width: a wrapped label's
    minimumSizeHint is taken at some other width, and read 17px for a label
    that needed 85."""
    out = []
    for c in w.findChildren(QtWidgets.QWidget):
        if not c.isVisible():
            continue
        need = c.minimumSizeHint().height()
        if c.hasHeightForWidth():
            need = max(need, c.heightForWidth(c.width()))
        if need > 0 and c.height() < need:
            out.append((c.objectName() or type(c).__name__, c.height(), need))
    return out


def as_app_sees_it() -> Voice:
    """Resolve the file the way `shout/__main__.py` does, and only that way."""
    cfg = Config.load()
    return Voice.resolve(cfg.cue_preset, cfg.cue_voice, cfg.cue_presets)


def contrast(fg: str, bg: tuple[float, float, float]) -> float:
    def chan(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def lum(c) -> float:
        r, g, b = (chan(x) for x in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    c = fg.lstrip("#")
    a, b = lum(tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def style_checks() -> None:
    """Two things the window's look depends on that no screenshot review catches
    reliably, and one that a screenshot cannot measure at all."""
    C, QSS = ui.C, ui.QSS

    # A state written BEFORE its subcontrol is silently misparsed by Qt: the
    # declarations land on the widget instead. `QSlider:focus::handle:horizontal`
    # painted a 1px border around every slider, focused or not, which read as a
    # grid drawn over the knobs. Bisected 8 Sep; this is the general form.
    # Comments first: the QSS carries a comment naming the misparsed selector,
    # and a lint that reads its own explanation of a bug reports the bug forever.
    live = re.sub(r"/\*.*?\*/", "", QSS, flags=re.S)
    bad = re.findall(r"Q\w+(?:#\w+)?:[a-z-]+::[\w-]+", live)
    check(not bad, "no QSS selector puts a state before its subcontrol",
          f"offenders: {bad}" if bad else "state follows subcontrol throughout")

    for selector in ("QComboBox:focus", "QLineEdit:focus", "QPushButton:focus",
                     "QSlider::handle:horizontal:focus"):
        check(selector in QSS, f"{selector} keeps a visible focus ring")

    # Contrast, against every background this window actually composites: the
    # base, the base under its lightest blob, a translucent card over that, and
    # the input surface. Light text is worst off over the LIGHTEST of those.
    def over(fg, alpha, bg):
        return tuple(alpha * f + (1 - alpha) * b for f, b in zip(fg, bg))

    deep = (0x1a, 0x0f, 0x2e)
    blob = over((100, 60, 180), 0.20, deep)
    tiers = {"base": deep, "under blob": blob,
             "card fill": over((255, 255, 255), 0.06, blob),
             "input surface": (0x3d, 0x22, 0x66)}
    for role in ("text", "label", "negative"):
        worst = min(tiers, key=lambda t: contrast(C[role], tiers[t]))
        ratio = contrast(C[role], tiers[worst])
        check(ratio >= 4.5, f"{role} text ({C[role]}) clears AA on every surface",
              f"worst is {worst} at {ratio:.2f}:1")
    # Control: the check must be able to fail. VV's own --vv-text-muted is the
    # token this palette had to reject, so it is the honest negative case.
    muted = max(contrast("#8a6f5a", bg) for bg in tiers.values())
    check(muted < 4.5, "and would reject VV's own #8a6f5a muted token (control)",
          f"best case {muted:.2f}:1, below the 4.5 floor everywhere")
    check(C["disabled"] == "#8a6f5a",
          "which therefore survives only as the disabled colour")
    # The selected tab is the one surface tinted with the accent. Its tint is
    # read from the token, not retyped here, so a stronger tint fails this row.
    *rgb, alpha = (float(v) for v in re.findall(r"[\d.]+", C["tab"]))
    ratio = contrast(C["text"], over(rgb, alpha, tiers["card fill"]))
    check(ratio >= 4.5, "the selected tab's text clears AA on its accent tint",
          f"{C['tab']} over the switch: {ratio:.2f}:1")


def main() -> int:
    style_checks()
    write_config(LEGACY)
    app = ui.application(sys.argv[:1])
    recent = Recent()
    win = ui.Window(recent, page="sounds")
    lab = win.lab
    plays: list[str] = []
    lab.cues.play = lambda name, *_a, **_k: plays.append(name)  # count, silently
    win.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    win.show()
    app.processEvents()
    check(win.stack.currentWidget() is lab,
          "precondition: the window opened on the page it was asked for",
          type(win.stack.currentWidget()).__name__)

    # -- opening on a legacy config ------------------------------------------
    check(abs(lab.voice.root_hz - 369.45) < 0.01,
          "the lab opens playing the voice the config describes",
          f"root {lab.voice.root_hz:.2f}Hz")
    check(lab.name.text() == "blip 2",
          "an unnamed tweaked material arrives pre-named, not as 'blip'",
          f"name box says {lab.name.text()!r}")
    check("not" in lab.status.text() and "saved" in lab.status.text(),
          "and says out loud that those tweaks are unsaved",
          lab.status.text()[:70])

    # -- the round trip ------------------------------------------------------
    played = lab.voice
    lab._save()
    raw = read_config()
    check(raw.get("cue_preset") == "blip 2" and raw.get("cue_voice") == {},
          "Save names the voice and clears the override layer",
          f"cue_preset={raw.get('cue_preset')!r} cue_voice={raw.get('cue_voice')}")
    check("blip 2" in raw.get("cue_presets", {}),
          "the voice is stored under its own name",
          f"cue_presets keys: {sorted(raw.get('cue_presets', {}))}")
    check(raw.get("log_level") == "DEBUG",
          "unrelated settings survive the write (merge, not rewrite)",
          f"log_level={raw.get('log_level')!r}")

    resolved = as_app_sees_it()
    check(resolved == Voice(name="blip 2", **{k: v for k, v in
                                              vars(played).items() if k != "name"}),
          "THE ROUND TRIP: the app resolves the exact voice the lab played",
          "identical" if resolved.root_hz == played.root_hz else
          f"{resolved.root_hz:.2f}Hz vs {played.root_hz:.2f}Hz")
    # Control: that comparison must be capable of failing. The material the
    # voice was tuned out of is the nearest wrong answer available.
    check(as_app_sees_it() != PRESETS["blip"],
          "and would notice if the app fell back to the material (control)",
          f"resolved {resolved.root_hz:.0f}Hz vs blip {PRESETS['blip'].root_hz:.0f}Hz")

    # -- the material is never redefined -------------------------------------
    check(PRESETS["blip"].root_hz == 660.0,
          "the material itself is untouched in memory",
          f"blip root {PRESETS['blip'].root_hz:.0f}Hz")
    names = [lab.preset.itemText(i) for i in range(lab.preset.count())]
    check("blip" in names and "blip 2" in names,
          "both the material and the saved voice are in the list",
          " / ".join(n for n in names if n))
    lab.preset.setCurrentText("blip")
    check(lab.voice.root_hz == 660.0,
          "selecting the material still gives the material",
          f"root {lab.voice.root_hz:.0f}Hz")

    lab.preset.setCurrentText("marimba")
    check(lab.name.text() == "marimba", "selecting a material offers its own name",
          f"name box says {lab.name.text()!r}")
    lab.sliders["root_hz"].setValue(lab.sliders["root_hz"].value() - 120)
    check(lab.name.text() == "marimba 2",
          "editing a material renames what you are editing",
          f"name box says {lab.name.text()!r}")

    lab.name.setText("marimba")                 # insist on the material's name
    lab._save()
    check(read_config().get("cue_preset") == "blip 2",
          "saving over a material is refused, and changes nothing",
          f"cue_preset is still {read_config().get('cue_preset')!r}")
    check(lab.name.text() == "marimba 2" and "not redefinable" in lab.status.text(),
          "the refusal renames it for you and says why",
          lab.status.text()[:70])

    lab._save()
    saved = read_config()["cue_presets"]["marimba 2"]
    check([tuple(p) for p in saved["partials"]] == list(PRESETS["marimba"].partials),
          "a saved voice keeps its material's partials, which are not a slider",
          f"{saved['partials']}")
    check(as_app_sees_it().partials == PRESETS["marimba"].partials,
          "and the app resolves those partials, not blip's",
          f"{as_app_sees_it().partials}")

    # -- delete --------------------------------------------------------------
    lab._delete()
    raw = read_config()
    check("marimba 2" not in raw["cue_presets"] and "blip 2" in raw["cue_presets"],
          "Delete removes one voice and leaves the others",
          f"cue_presets keys: {sorted(raw['cue_presets'])}")
    check(raw["cue_preset"] == "blip",
          "and does not leave cue_preset naming a voice that is gone",
          f"cue_preset={raw['cue_preset']!r}")
    # The other half, and the reason Delete must ask the FILE rather than either
    # `self.voice.name` or a snapshot: deleting a voice you are only looking at
    # must leave the active one alone.
    lab.name.setText("spare")
    lab._save()
    lab.preset.setCurrentText("blip 2")
    lab._save()                                 # active = 'blip 2'
    lab.preset.setCurrentText("spare")
    lab._delete()
    raw = read_config()
    check(raw["cue_preset"] == "blip 2" and "spare" not in raw["cue_presets"],
          "deleting a voice that is not the active one leaves cue_preset alone",
          f"cue_preset={raw['cue_preset']!r}, keys {sorted(raw['cue_presets'])}")

    lab.delete.setEnabled(True)                 # reachable only by forcing it
    lab.voice = PRESETS["wood"]
    lab._delete()
    check("Only your own" in lab.status.text(),
          "a material cannot be deleted even if the button is forced",
          lab.status.text()[:60])

    # -- sliders: jump to the click, audition on release ---------------------
    steps = ui.STEPS
    lab.auto.setChecked(True)
    lab.preset.setCurrentText("soft")
    s = lab.sliders["decay"]                    # range 0..1, so value/steps IS decay
    s.setValue(200)
    plays.clear()
    x = 0.8 * s.width()
    mouse(s, E.MouseButtonPress, x, LEFT, LEFT)
    on_press = len(plays)
    release(s, x)
    check(abs(s.value() - 0.8 * steps) <= 0.03 * steps,
          "a click on the track jumps the handle to the click",
          f"clicked at 80% of {s.width()}px: 200 -> {s.value()}")
    check(on_press == 0 and plays == ["start"],
          "and auditions once, on release rather than on press",
          f"{on_press} play(s) on press, {len(plays)} after release")
    # Control: the same click, same sheet, on the stock widget the lab used to
    # have. It must only page toward the click, or the row above measures a
    # jump that any slider would have made.
    stock = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
    stock.setRange(0, steps)
    stock.setValue(200)
    stock.setStyleSheet(ui.QSS)
    stock.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    stock.resize(s.width(), s.height())
    stock.show()
    app.processEvents()
    mouse(stock, E.MouseButtonPress, 0.8 * stock.width(), LEFT, LEFT)
    release(stock, 0.8 * stock.width())
    check(stock.value() == 200 + stock.pageStep(),
          "where a stock QSlider under the same sheet only steps toward it (control)",
          f"200 -> {stock.value()}, pageStep {stock.pageStep()}")
    stock.close()

    # From a fixed value, not wherever the click above left it: on the pre-fix
    # code that was 210, and a drag from there to 20% moved four steps.
    s.setValue(round(0.8 * steps))
    changes: list[int] = []
    on_change = changes.append
    s.valueChanged.connect(on_change)
    plays.clear()
    start = s.value()
    x0, x1 = s.width() * start / steps, 0.2 * s.width()
    before_label = lab.values["decay"].text()
    drag(s, x0, x1)
    during, mid_label, mid_synth = len(plays), lab.values["decay"].text(), \
        lab.cues.voice.decay
    release(s, x1)
    check(start - s.value() > 0.4 * steps and len(changes) >= 20,
          "precondition: the drag really moved the slider",
          f"{len(changes)} value changes, {start} -> {s.value()}")
    check(during == 0, "dragging does not audition on every step (the screech)",
          f"{during} play(s) across {len(changes)} value changes")
    check(mid_label != before_label and mid_label == f"{s.value() / steps:.2f}"
          and abs(mid_synth - s.value() / steps) < 1e-9,
          "mid-drag, the readout and the synth already follow the knob",
          f"label {before_label} -> {mid_label}, synth decay {mid_synth:.3f}")
    check(plays == ["start"] and abs(lab.cues.voice.decay - s.value() / steps) < 1e-9,
          "letting go auditions exactly once, as the knob now reads",
          f"{len(plays)} play(s), decay {lab.cues.voice.decay:.3f}")

    lab.auto.setChecked(False)
    plays.clear()
    was = s.value()
    drag(s, 0.2 * s.width(), 0.6 * s.width())
    release(s, 0.6 * s.width())
    check(not plays and s.value() != was,
          "with Play on change off, a drag stays silent",
          f"{was} -> {s.value()}, {len(plays)} play(s)")
    lab.auto.setChecked(True)

    plays.clear()
    for _ in range(3):
        QtWidgets.QApplication.sendEvent(s, QtGui.QKeyEvent(
            E.KeyPress, QtCore.Qt.Key.Key_Right, QtCore.Qt.KeyboardModifier.NoModifier))
    check(len(plays) == 3, "arrow keys still audition every step",
          f"{len(plays)} play(s) for 3 key presses")
    s.valueChanged.disconnect(on_change)

    # -- the running app follows Save -----------------------------------------
    # A real Shout on the same throwaway config, started the way run() starts
    # it. Nothing is pressed and its cues are never played.
    shout = Shout(Config.load())
    shout.watch_config()
    lab.preset.setCurrentText("glass")
    lab.sliders["root_hz"].setValue(lab.sliders["root_hz"].value() - 90)
    lab.name.setText("follow me")
    # Wait out the first poll, which reads the file whatever its stamp. A Save
    # before it would pass the row below on a watch that only ever reads once.
    pump(app, 3.0, lambda: shout._config_stamp is not None)
    before, was_named = shout.cues.samples["start"].copy(), shout.cues.voice.name
    lab._save()
    took = pump(app, 3.0, lambda: shout.cues.voice.name == "follow me")
    check(took is not None and took < 1.5,
          "a RUNNING app switches to a voice the lab saves, with no restart",
          f"{was_named!r} -> {shout.cues.voice.name!r} after {took * 1000:.0f}ms, "
          f"polling every {CONFIG_POLL_MS}ms" if took is not None else
          f"still {shout.cues.voice.name!r} after 3s")
    heard, auditioned = shout.cues.samples["start"], lab.cues.samples["start"]
    check(shout.cues.rate == lab.cues.rate and same_sound(heard, auditioned),
          "and plays exactly what the lab auditioned",
          f"{len(heard)} vs {len(auditioned)} samples at {shout.cues.rate}Hz")
    check(not same_sound(before, auditioned),
          "which the same comparison tells apart from its old voice (control)",
          f"old voice {was_named!r}: {len(before)} samples")

    # A file caught mid-write. Config.load() reads it as all defaults, which is
    # a reset to 'blip' at 0.25 in the middle of your day.
    full = CONFIG.read_bytes()
    raw = read_config() | {"cue_volume": 0.3}
    whole = json.dumps(raw, indent=2).encode("utf-8")
    CONFIG.write_bytes(whole[:len(whole) // 2])
    pump(app, 3 * CONFIG_POLL_MS / 1000)
    check(shout.cues.voice.name == "follow me" and shout._cue_settings[1] != 0.25,
          "a half-written config.json leaves the running app's voice alone",
          f"{shout.cues.voice.name!r} at {shout._cue_settings[1]:.2f} after "
          f"3 polls of a truncated file")
    check(Config.load().cue_preset == "blip",
          "and that file is one Config.load() reads as defaults (control)",
          f"Config.load() on it -> {Config.load().cue_preset!r}")
    CONFIG.write_bytes(whole)
    took = pump(app, 3.0, lambda: shout._cue_settings[1] == 0.3)
    check(took is not None, "and the app picks the file up once it is whole",
          f"volume -> {shout._cue_settings[1]:.2f}")
    CONFIG.write_bytes(full)
    pump(app, 3.0, lambda: shout._cue_settings[1] != 0.3)
    shout._config_timer.stop()
    shout.cues.close()

    # -- the recent dictations page -------------------------------------------
    page = win.recent_page
    win.tabs["recent"].click()
    app.processEvents()
    check(win.stack.currentWidget() is page and win.tabs["recent"].isChecked()
          and not win.tabs["sounds"].isChecked(),
          "clicking a tab shows its page and marks only that tab",
          type(win.stack.currentWidget()).__name__)
    check(page.empty.isVisible() and not page.shown and not page.clear.isEnabled(),
          "an empty list says so, and has nothing to clear",
          f"{len(page.shown)} rows, Clear enabled={page.clear.isEnabled()}")

    # Added to the store the way Shout adds them, never pushed at the page: it
    # has to notice by itself, or a window left open goes stale.
    recent.add("first dictation")
    recent.add("second dictation")
    took = pump(app, 2.0, lambda: len(page.shown) == 2)
    texts = [row.entry.text for row in page.shown]
    check(took is not None and texts == ["second dictation", "first dictation"],
          "new dictations appear by themselves, newest first",
          f"{texts} after {took * 1000:.0f}ms" if took is not None else
          f"{texts} after 2s")
    check(not page.empty.isVisible() and page.clear.isEnabled(),
          "and the empty note gives way to them")

    take = " ".join(f"word{i}" for i in range(300))
    original = inj.get_clipboard_text()
    try:
        inj.set_clipboard_text("unmarked", private=False)
        check(not clipboard_is_private(),
              "an unmarked clipboard reads as not private (control)")
        page.shown[1].copy.click()
        check(inj.get_clipboard_text() == "first dictation",
              "Copy on an older row copies that row, not the newest",
              f"clipboard holds {inj.get_clipboard_text()!r}")
        check(clipboard_is_private(), "and marks the copy private, like a dictation",
              "kept out of Clipboard History and the cloud clipboard")

        recent.add(take)
        pump(app, 2.0, lambda: len(page.shown) == 3)
        shown = page.shown[0].text.text()
        check(shown.endswith("…") and len(shown) <= ui.EntryRow.SHOWN_CHARS + 1,
              "a long take is shown clipped, so it cannot bury the rest",
              f"{len(take)} chars shown as {len(shown)}")
        page.shown[0].copy.click()
        got = inj.get_clipboard_text() or ""
        check(got == take, "but Copy takes the whole of it",
              f"clipboard holds {len(got)} of {len(take)} chars")
    finally:
        if original is not None:
            # Private: the probe cannot know whether what it borrowed was a
            # password.
            inj.set_clipboard_text(original, private=True)

    for i in range(SIZE + 3):
        recent.add(f"dictation {i}")
    pump(app, 2.0, lambda: page.shown[0].entry.text == f"dictation {SIZE + 2}")
    texts = [row.entry.text for row in page.shown]
    check(len(texts) == SIZE and texts[-1] == "dictation 3",
          f"only the last {SIZE} are kept, oldest out first",
          f"{len(texts)} rows, oldest {texts[-1]!r}")

    page.clear.click()
    check(recent.last() is None and not page.shown and page.empty.isVisible(),
          "Clear empties the list and the store behind it",
          f"{len(page.shown)} rows, store last={recent.last()}")

    # Layout, measured rather than eyeballed. A screenshot cannot show this: at
    # 736px tall every label rendered 4-5px under its own minimum, which is
    # legible at this DPI and clips at another. Swept on BOTH pages, with the
    # list full of mixed lengths: the window takes its minimum from the taller
    # page, and a page that needs more is clipped only while it is showing.
    for i in range(SIZE):
        recent.add(take if i % 3 == 0 else " ".join(["dictation"] * (4 * i + 1)))
    pump(app, 2.0, lambda: len(page.shown) == SIZE)
    # Waited for, not assumed: for a tick after the rows arrive, the scroll area
    # has not yet grown the list, and they are squashed into the card.
    bar = page.scroll.verticalScrollBar()
    took = pump(app, 2.0, lambda: bar.maximum() > 0)
    check(took is not None,
          "precondition: a full list overflows the card and scrolls, not squashes",
          f"scroll range {bar.maximum()}px")
    for name in ui.Window.PAGES:
        win.show_page(name)
        app.processEvents()
        bad = squeezed(win)
        check(not bad, f"nothing is squeezed below its minimum on the {name} page",
              f"{win.width()}x{win.height()}, layout minimum "
              f"{win.minimumSizeHint().width()}x{win.minimumSizeHint().height()}"
              + (f", squeezed: {bad}" if bad else ""))
    tall = win.height()
    win.resize(win.width(), 736)
    app.processEvents()
    check(win.height() >= win.minimumSizeHint().height(),
          "and the window cannot be dragged below that minimum",
          f"asked for 736, Qt clamped to {win.height()}")
    # Control: with the floor explicitly lifted -- the only way to get under it --
    # the same sweep must report the clipping that 736px actually causes.
    # Without this the row above passes on a sweep that can no longer see
    # anything, which is how the first version of this gate was wrong.
    # setFixedHeight, not setMinimumSize(0,0): clearing the minimum does not
    # stick, because the layout re-imposes its own on the next activation --
    # which is exactly why the row above holds for the user, and why the first
    # attempt at this control silently measured a window still 840px tall.
    win.setFixedHeight(736)
    app.processEvents()
    check(len(squeezed(win)) > 0,
          "and the sweep detects clipping when it happens (control)",
          f"{len(squeezed(win))} widgets clipped at 736px tall")
    win.setMinimumSize(0, 0)
    win.setMaximumSize(16777215, 16777215)
    win.resize(win.width(), tall)
    app.processEvents()
    check(win.minimumSizeHint().height() <= 900,
          "the layout minimum still fits a 1080p screen",
          f"minimum height {win.minimumSizeHint().height()}px")

    # The window no longer ends a process when it closes, so nothing but
    # closeEvent releases the sounds page's output stream.
    check(lab.cues._stream is not None,
          "precondition: the sounds page holds an open output stream")
    win.close()
    check(lab.cues._stream is None, "closing the window releases that stream",
          "or every open/close of the window leaks one inside Shout")
    app.quit()

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'FAIL' if passed != len(rows) else 'PASS'} {passed}/{len(rows)} "
          f"Shout window assertions; lab -> config.json -> running app, and "
          f"recent dictations, verified")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
