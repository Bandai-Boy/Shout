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

The orb page carries the same round trip for the look: move knobs, press Save,
resolve `orb_tune` the way the app does, compare field for field, and watch a
running app's overlay take it. It writes only the knobs you moved, reads back
only the knob that moved (the shipped values are not on slider steps, so reading
them all back would quietly change the look), and its preview animates only
while it is on screen.

The recent page is the way back to a paste that landed nowhere, so its rows are
about getting the RIGHT words back: a dictation appears without anything
refreshing the page, Copy on an older row copies that row and not the newest,
a clipped long take still copies whole, the copy is marked private, and the
list stops at its size. The clipboard it borrows is put back, marked private.

The home page reads today's counts from `shout.log`, so its rows parse a
synthetic log written into the throwaway APPDATA, with a log dated yesterday as
the control that must count zero today. Its two ways onward (Add a word, Fix on
the last dictation) must land on their pages. The settings page must write
exactly the key each control names and nothing else, and Start with Windows
must make a shortcut in the throwaway Startup folder that points at shoutw.exe.

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
from dataclasses import fields, replace
from datetime import date
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
from shout import orb  # noqa: E402
from shout import window as ui  # noqa: E402
from shout.__main__ import CONFIG_POLL_MS, Shout  # noqa: E402
from shout.config import Config, merge  # noqa: E402
from shout.cues import PRESETS, Voice  # noqa: E402
from shout.recent import SIZE, Entry, Recent  # noqa: E402
from shout.window import RESTART as RESTART_TEXT  # noqa: E402

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


def parse_colour(token: str) -> tuple[tuple[float, float, float], float]:
    """A token as (rgb, alpha): '#rrggbb' or 'rgba(r,g,b,a)'."""
    if token.startswith("#"):
        c = token.lstrip("#")
        return tuple(float(int(c[i:i + 2], 16)) for i in (0, 2, 4)), 1.0
    *rgb, alpha = (float(v) for v in re.findall(r"[\d.]+", token))
    return tuple(rgb), alpha


def over(token: str, bg: tuple[float, float, float]) -> tuple[float, float, float]:
    rgb, alpha = parse_colour(token)
    return tuple(alpha * f + (1 - alpha) * b for f, b in zip(rgb, bg))


def surfaces(wash: list[str]) -> dict[str, tuple[float, float, float]]:
    """Every surface text sits on, composited at the wash's LIGHTEST stop,
    which is where white text is worst off."""
    C = ui.C
    base = max((parse_colour(t)[0] for t in wash), key=lambda c: contrast_rgb(
        (0, 0, 0), c))
    card = over(C["card"], base)
    return {"wash": base, "chip on the wash": over(C["inset"], base),
            "card": card, "inset in a card": over(C["inset"], card),
            "text field": over(C["field"], card),
            "hovered inset": over(C["inset_hover"], card),
            "pressed button": over(C["card"], card),
            "popup": parse_colour(C["popup"])[0]}


def contrast_rgb(a, b) -> float:
    def chan(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    def lum(c) -> float:
        r, g, b_ = (chan(x) for x in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b_

    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


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
                     "QPushButton#cta:focus", "QPushButton#nav:focus",
                     "QPushButton#seg:focus", "QSlider::handle:horizontal:focus"):
        check(selector in QSS, f"{selector} keeps a visible focus ring")

    # Contrast: every text colour on every surface it is drawn on, read from
    # the tokens rather than retyped here, so a lighter wash or a stronger fill
    # fails a row. Placeholder text sits only in text fields, whose hover
    # changes the border and not the fill.
    wash = [C["wash_a"], C["wash_b"], C["wash_c"]]
    tiers = surfaces(wash)
    used = {"text": list(tiers), "placeholder": ["text field"]}
    for role, where in used.items():
        fg = parse_colour(C[role])[0]
        worst = min(where, key=lambda t: contrast_rgb(fg, tiers[t]))
        ratio = contrast_rgb(fg, tiers[worst])
        check(ratio >= 4.5, f"{role} ({C[role]}) clears AA on every surface it is "
              f"drawn on", f"worst is {worst} at {ratio:.2f}:1")
    ink = parse_colour(C["ink"])[0]
    for fill in ("#ffffff", C["white_hover"]):
        ratio = contrast_rgb(ink, parse_colour(fill)[0])
        check(ratio >= 4.5, f"dark text on the white button and selected tab "
              f"({fill}) clears AA", f"{ratio:.2f}:1")
    # Controls: the same measurement must be able to fail. Faded white is the
    # disabled colour, and the mock-up's own lighter wash is what these stops
    # were darkened from.
    faded = over(C["disabled"], tiers["card"])
    ratio = contrast_rgb(faded, tiers["card"])
    check(ratio < 4.5, "and would reject faded white as text (control)",
          f"{C['disabled']} on a card: {ratio:.2f}:1")
    mock = surfaces(["#46426c", "#575079", "#6e5068"])
    ratio = min(contrast_rgb((255, 255, 255), mock[t]) for t in
                ("inset in a card", "hovered inset"))
    check(ratio < 4.5, "and the mock-up's lighter wash, which is why the stops "
          "are darker (control)", f"white on its inset: {ratio:.2f}:1")
    # Faded white therefore survives only where a contrast floor does not apply.
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", live)
    holders = [sel.strip() for sel, body in rules if C["disabled"] in body]
    check(holders and all(":disabled" in sel for sel in holders),
          "faded white is used only for disabled controls",
          f"rules using it: {holders}")


LOG_LINE = "{day} 10:{m:02d}:00,000 INFO    {name:<16} {msg}"


def log_lines(day: str) -> list[str]:
    """A day of shout.log: a warmup (transcribed, no END-TO-END), three
    dictations of which the first predates the word count, and an empty
    transcript. Expected: 3 dictations, 7 + 12 = 19 words over 2 of them,
    median 210ms."""
    rows = [("shout.transcribe", "transcribed 11.0s of audio in 0.40s -> 61 chars, 11 words"),
            ("shout.transcribe", "warmup 0.40s -> 'And so my fellow Americans'"),
            ("shout.transcribe", "transcribed 2.0s of audio in 0.16s -> 20 chars"),
            ("shout", "END-TO-END 160ms for 2.0s of audio -> pasted"),
            ("shout.transcribe", "transcribed 3.1s of audio in 0.20s -> 38 chars, 7 words"),
            ("shout", "END-TO-END 210ms for 3.1s of audio -> pasted (joined)"),
            ("shout.transcribe", "transcribed 0.6s of audio in 0.10s -> 0 chars, 0 words"),
            ("shout", "empty transcript, nothing to inject"),
            ("shout.transcribe", "transcribed 6.0s of audio in 0.30s -> 70 chars, 12 words"),
            ("shout", "END-TO-END 640ms for 6.0s of audio -> pasted")]
    return [LOG_LINE.format(day=day, m=i, name=n, msg=msg) for i, (n, msg) in
            enumerate(rows)]


def home_rows(app, win, recent) -> None:
    from datetime import timedelta
    home = win.home
    logs = TMP / "Shout"
    today = date.today()
    yday = today - timedelta(days=1)

    def write_log(name: str, lines: list[str]) -> None:
        (logs / name).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def shown(w) -> bool:
        return not w.isHidden()

    def fresh() -> None:
        """Refresh as if every file and the store had just changed: two writes
        in the same tick can leave a file's stamp where it was."""
        home._stamp = home._config_stamp = None
        home._version = -1
        home.refresh()

    win.show_page("home")
    took = pump(app, 2.0, lambda: home.hero.drawn >= 10)
    check(took is not None and home.hero.renderer is not None,
          "shown, the home orb animates", f"{home.hero.drawn} frames")

    # Nothing logged yet: no file at all.
    for name in ("shout.log", "shout.log.1"):
        (logs / name).unlink(missing_ok=True)
    home._stamp = None
    fresh()
    check(ui.today_stats(logs).dictations == 0 and shown(home.today_empty)
          and shown(home.speed_empty) and not shown(home.today_count),
          "with no shout.log, Today and Speed say no dictations yet",
          home.today_empty.text())

    write_log("shout.log", log_lines(today.isoformat()))
    st = ui.today_stats(logs)
    check((st.dictations, st.words, st.counted, st.latencies)
          == (3, 19, 2, [160, 210, 640]),
          "today's log: three dictations, words only from the lines that count them",
          f"{st.dictations} dictations, {st.words} words over {st.counted}, "
          f"latencies {st.latencies}")
    fresh()
    check(home.today_count.text() == "3" and home.today_words.text()
          == "19 words in the last 2" and home.speed.text().startswith("0.2<")
          and len(home.spark.values) == 3 and not shown(home.today_empty),
          "and the Today and Speed cards show them",
          f"{home.today_count.text()!r}, {home.today_words.text()!r}, "
          f"speed {home.speed.text()[:4]!r}, {len(home.spark.values)} points")

    # Control: the same lines dated yesterday are not today's, and the parser
    # does see them when asked about yesterday, so zero is not blindness.
    write_log("shout.log", log_lines(yday.isoformat()))
    check(ui.today_stats(logs).dictations == 0
          and ui.today_stats(logs, yday).dictations == 3,
          "a log from yesterday counts zero today, and three for yesterday (control)",
          f"today {ui.today_stats(logs).dictations}, yesterday "
          f"{ui.today_stats(logs, yday).dictations}")
    fresh()
    check(shown(home.today_empty) and not shown(home.today_count),
          "and the cards go back to no dictations yet today")

    # Rotation: the current file starts today, so the previous one may hold
    # today's earlier lines; when it does not start today, it cannot.
    write_log("shout.log.1", log_lines(yday.isoformat()) + log_lines(today.isoformat()))
    write_log("shout.log", log_lines(today.isoformat()))
    rotated = ui.today_stats(logs).dictations
    write_log("shout.log", log_lines(yday.isoformat())[-2:] + log_lines(today.isoformat()))
    unrotated = ui.today_stats(logs).dictations
    check(rotated == 6 and unrotated == 3,
          "after a rotation today, the previous file's lines from today count too",
          f"{rotated} with a rotation today, {unrotated} without")
    for name in ("shout.log", "shout.log.1"):
        (logs / name).unlink(missing_ok=True)

    # Your words, from config.json as it is now: two written, then none.
    kept = read_config()
    write_config(kept | {"words": ["Ogerpon", "TCGplayer"],
                         "replacements": {"cache": "cash"}})
    fresh()
    two = (home.names.text(), home.names_detail.text())
    write_config(kept | {"words": [], "replacements": {}})
    fresh()
    check(two[0] == "2 names" and two[1].startswith("1 fix · ")
          and home.names.text() == "0 names",
          "Your words counts the words and fixes in config.json, as it changes",
          f"{two} then {home.names.text()!r}")
    write_config(kept)

    # The last dictation, and Fix and Add a word.
    recent.clear()
    fresh()
    check(not home.copy.isEnabled() and not home.fix.isEnabled(),
          "with nothing dictated, Copy and Fix are off", home.last_text.text())
    recent.add("we count the cache tonight")
    fresh()
    check(home.last_text.text() == "we count the cache tonight" and home.fix.isEnabled(),
          "the last dictation shows on home", home.last_text.text())
    home.fix.click()
    app.processEvents()
    bar = win.recent_page.fix_bar
    check(win.stack.currentWidget() is win.recent_page and win.tabs["recent"].isChecked()
          and bar.isVisible() and win.recent_page.shown
          and win.recent_page.shown[0].entry.text == "we count the cache tonight",
          "Fix on the last dictation opens Recent with its fix bar, that dictation on top",
          f"{type(win.stack.currentWidget()).__name__}, bar visible={bar.isVisible()}")
    bar.close_bar("")
    recent.clear()
    win.show_page("home")
    home.add_word.click()
    app.processEvents()
    check(win.stack.currentWidget() is win.words_page and win.tabs["words"].isChecked(),
          "Add a word lands on the Words page",
          type(win.stack.currentWidget()).__name__)
    win.recent_page.status.setText("")
    win.show_page("orb")


def settings_rows(app, win) -> None:
    page = win.settings_page
    win.show_page("settings")
    app.processEvents()

    def changed_by(action) -> dict:
        before = read_config()
        action()
        app.processEvents()
        after = read_config()
        return {k: after.get(k) for k in set(before) | set(after)
                if before.get(k, "<absent>") != after.get(k, "<absent>")}

    # Control: the diff sees a second key when one is written.
    extra = changed_by(lambda: merge({"language": "fr", "beam_size": 4}))
    check(set(extra) == {"language", "beam_size"},
          "the settings diff sees every key a write touches (control)",
          f"{sorted(extra)}")
    write_config({k: v for k, v in read_config().items()
                  if k not in ("language", "beam_size")})

    auto = page.language.findData(None)
    got = changed_by(lambda: page.language.setCurrentIndex(auto))
    check(got == {"language": None},
          "Language writes only language", f"{got}")
    got = changed_by(lambda: page.language.setCurrentIndex(page.language.findData("de")))
    check(got == {"language": "de"}, "and a picked code", f"{got}")
    if page.mic.count() > 1:
        got = changed_by(lambda: page.mic.setCurrentIndex(1))
        check(got == {"input_device": page.mic.itemData(1)},
              "Microphone writes only input_device", f"{got}")
        got = changed_by(lambda: page.mic.setCurrentIndex(0))
        check(got == {"input_device": None},
              "and following the default writes null", f"{got}")
    else:
        check(page.mic.itemData(0) is None,
              "Microphone offers following the default (no other input listed here)")
    got = changed_by(page.overlay.click)
    check(got == {"overlay": not ui.Config().overlay},
          "the overlay switch writes only overlay", f"{got}")
    got = changed_by(page.cues.click)
    check(got == {"cues": not ui.Config().cues}, "the cues switch writes only cues",
          f"{got}")
    page.volume.setFocus()
    got = changed_by(lambda: QtWidgets.QApplication.sendEvent(page.volume, QtGui.QKeyEvent(
        E.KeyPress, QtCore.Qt.Key.Key_Right, QtCore.Qt.KeyboardModifier.NoModifier)))
    check(set(got) == {"cue_volume"} and abs(got["cue_volume"]
                                             - page.volume.value() / 100) < 1e-9,
          "Cue volume writes only cue_volume", f"{got}")
    check(RESTART_TEXT in page.status.text() or "next cue" in page.status.text(),
          "and the page says whether a restart is needed", page.status.text())
    write_config({k: v for k, v in read_config().items()
                  if k not in ("language", "input_device", "overlay", "cues")})

    # Start with Windows, in the THROWAWAY Startup folder only.
    link = ui.startup_link()
    check(str(link).startswith(str(TMP)) and not link.exists(),
          "precondition: the Startup shortcut path is inside the throwaway APPDATA",
          str(link))
    if not str(link).startswith(str(TMP)):
        return
    before = read_config()
    page.autostart.click()
    target = ""
    if link.exists():
        import subprocess
        target = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(New-Object -ComObject WScript.Shell).CreateShortcut($env:L).TargetPath"],
            env=os.environ | {"L": str(link)}, capture_output=True, text=True,
            timeout=30).stdout.strip()
    check(link.exists() and page.autostart.isChecked()
          and Path(target).resolve() == (ROOT / ".venv" / "Scripts" / "shoutw.exe").resolve(),
          "Start with Windows makes a Startup shortcut to shoutw.exe",
          f"target {target!r}; {page.status.text()}")
    check(read_config() == before, "and writes nothing to config.json")
    page.autostart.click()
    check(not link.exists() and not page.autostart.isChecked(),
          "and turning it off removes the shortcut", str(link))
    real = ui.LAUNCHER
    ui.LAUNCHER = TMP / "missing" / "shoutw.exe"
    try:
        page.autostart.click()
    finally:
        ui.LAUNCHER = real
    check(not link.exists() and not page.autostart.isChecked()
          and "install_shortcuts" in page.status.text(),
          "with no launcher installed, it refuses, stays off and says why (control)",
          page.status.text())
    win.show_page("orb")


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

    hero = win.home.hero
    check(hero.drawn == 0 and not hero._timer.isActive(),
          "the home orb draws nothing while its page is not showing",
          f"{hero.drawn} frames, timer active={hero._timer.isActive()}")
    check(hero.state == "ready",
          "with no model state passed in, home says Ready", hero.state)
    states = iter(["loading", "error", "ready"])
    seen = []
    win.home.model_state = lambda: current[0]
    for want in states:
        current = [want]
        win.home.refresh()
        seen.append((want, hero.state, hero.STATES[hero.state]))
    check(all(a == b for a, b, _ in seen) and [t for *_, t in seen]
          == ["Loading", "Error", "Ready"],
          "home shows the state Shout's callable reports, as it changes",
          f"{seen}")
    win.home.model_state = lambda: "ready"

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

    # The overlay's look follows the file the same way. Never built here, so this
    # is the follow alone; probe_orb swaps looks on a real window.
    was_style = shout.overlay.style
    CONFIG.write_text(json.dumps(read_config() | {"overlay_style": "orb"}, indent=2),
                      encoding="utf-8")
    took = pump(app, 3.0, lambda: shout.overlay.style == "orb")
    check(was_style == "pill" and took is not None and shout.tray._style == "orb",
          "a RUNNING app switches the overlay's look when config.json names one",
          f"{was_style!r} -> {shout.overlay.style!r}, tray ticks {shout.tray._style!r}")
    CONFIG.write_bytes(full)
    took = pump(app, 3.0, lambda: shout.overlay.style == "pill")
    check(took is not None, "and back when the file goes back",
          f"style {shout.overlay.style!r}")

    # -- the orb page -----------------------------------------------------------
    orb_page = win.orb_page
    preview = orb_page.preview
    shipped = orb.Tune()
    check(orb_page.tune == shipped and not orb_page.changes(),
          "precondition: with no orb_tune in the file, the orb page opens on the "
          "shipped look")
    check(preview.drawn == 0 and not preview._timer.isActive(),
          "the orb preview draws nothing while its page is not showing",
          f"{preview.drawn} frames, timer active={preview._timer.isActive()}")
    win.tabs["orb"].click()
    took = pump(app, 2.0, lambda: preview.drawn >= 20)
    lit = 0
    if preview.frames is not None:
        img = preview.frames[1].convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
        lit = int((np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes())[3::4]
                   > 40).sum())
    check(preview.renderer is not None and took is not None and lit > 0,
          "shown, it animates the orb", f"{preview.drawn} frames, {lit} lit pixels "
          f"at actual size")

    s = orb_page.sliders
    lo, hi = orb.RANGES["hold_spin"]
    on_step = lo + (hi - lo) * s["hold_spin"].value() / ui.STEPS
    check(abs(on_step - shipped.hold_spin) > 1e-6,
          "precondition: the shipped spin is not on a slider step",
          f"shipped {shipped.hold_spin:.5f}, nearest step {on_step:.5f}; so reading "
          f"every slider back would change it")
    bumps = s["hold_crests"]
    mouse(bumps, E.MouseButtonPress, bumps.width() * 0.8, LEFT, LEFT)
    release(bumps, bumps.width() * 0.8)
    check(bumps.value() != shipped.hold_crests
          and orb_page.tune == replace(shipped, hold_crests=bumps.value())
          and preview.motion.tune is orb_page.tune,
          "a click on Waves changes that one knob, and the preview draws it",
          f"waves {shipped.hold_crests} -> {orb_page.tune.hold_crests}; every other "
          f"field {'untouched' if orb_page.tune == replace(shipped, hold_crests=bumps.value()) else 'MOVED'}")

    s["hold_turb"].setValue(700)
    s["push"].setValue(900)
    s["hue"].setValue(250)
    s["rest_w"].setValue(100)
    before_save = read_config()
    orb_page.save.click()
    saved = read_config().get("orb_tune", {})
    got = orb.Tune.resolve(Config.load().orb_tune)

    def off_by_more_than_a_step(a: orb.Tune, b: orb.Tune) -> list[str]:
        out = []
        for f in fields(orb.Tune):
            lo, hi = orb.RANGES[f.name]
            step = 0 if isinstance(getattr(a, f.name), int) else (hi - lo) / ui.STEPS
            if abs(getattr(a, f.name) - getattr(b, f.name)) > step:
                out.append(f.name)
        return out

    moved = {"hold_crests", "hold_turb", "push", "hue", "rest_w"}
    check(set(saved) == moved,
          "Save writes only the knobs you moved", f"orb_tune keys {sorted(saved)}")
    off = off_by_more_than_a_step(got, orb_page.tune)
    check(not off, "and the app resolves exactly the look the page had",
          f"fields that differ: {off}" if off else "every field within a slider step")
    nudged = replace(orb_page.tune, hue=orb_page.tune.hue + 1.0)
    check(off_by_more_than_a_step(got, nudged) == ["hue"],
          "and that comparison sees one knob a step or two off (control)",
          f"hue nudged by 1 degree -> {off_by_more_than_a_step(got, nudged)}")
    after = {k: v for k, v in read_config().items() if k != "orb_tune"}
    kept = {k: v for k, v in before_save.items() if k != "orb_tune"}
    check(after == kept and "log_level" in after,
          "Save leaves every other key in config.json alone",
          f"{len(after)} other keys, log_level={after.get('log_level')!r}")
    check("On screen" in orb_page.status.text(),
          "saving while the pill is on screen says how to see the orb",
          orb_page.status.text())
    took = pump(app, 3.0, lambda: shout.overlay.orb_tune == got)
    check(took is not None,
          "a RUNNING app's overlay takes the saved orb tune, with no restart",
          f"after {took * 1000:.0f}ms" if took is not None else
          f"overlay still has {shout.overlay.orb_tune}")

    button = {b.text(): b for b in orb_page.findChildren(QtWidgets.QPushButton)}
    button["Defaults"].click()
    check(orb_page.tune == shipped and not orb_page.changes()
          and s["hold_crests"].value() == shipped.hold_crests,
          "Defaults puts every knob back on the shipped look",
          f"changes left: {orb_page.changes()}")
    button["Revert"].click()
    check(not off_by_more_than_a_step(orb_page.tune, got),
          "and Revert brings back what is saved",
          f"differs in {off_by_more_than_a_step(orb_page.tune, got)}")
    button["Defaults"].click()
    orb_page.save.click()
    took = pump(app, 3.0, lambda: shout.overlay.orb_tune == shipped)
    check(read_config().get("orb_tune") == {} and took is not None,
          "saving the defaults empties orb_tune, and the app goes back to the shipped look",
          f"orb_tune={read_config().get('orb_tune')}")

    # The look switch on the page, which writes the key the tray menu writes.
    looks = orb_page.style_buttons
    check(looks["pill"].isChecked() and not looks["orb"].isChecked()
          and shout.overlay.style == "pill",
          "precondition: the page's switch opens on the look the file names",
          f"pill checked={looks['pill'].isChecked()}, app on {shout.overlay.style!r}")
    before_switch = {k: v for k, v in read_config().items() if k != "overlay_style"}
    looks["orb"].click()
    took = pump(app, 3.0, lambda: shout.overlay.style == "orb")
    check(read_config().get("overlay_style") == "orb" and took is not None
          and shout.tray._style == "orb",
          "switching to Orb on the page puts the orb on a RUNNING app's screen",
          f"file {read_config().get('overlay_style')!r}, app {shout.overlay.style!r}, "
          f"tray ticks {shout.tray._style!r}")
    check({k: v for k, v in read_config().items() if k != "overlay_style"}
          == before_switch,
          "and touches no other key, the tune included")
    looks["pill"].click()
    took = pump(app, 3.0, lambda: shout.overlay.style == "pill")
    check(took is not None and shout.tray._style == "pill",
          "and Pill switches it back", f"app {shout.overlay.style!r}")
    # The tray's Appearance menu, while the page is open: through the file, the
    # way Shout.set_overlay_style writes it.
    CONFIG.write_text(json.dumps(read_config() | {"overlay_style": "orb"}, indent=2),
                      encoding="utf-8")
    took = pump(app, 3.0, lambda: looks["orb"].isChecked())
    check(took is not None and not looks["pill"].isChecked(),
          "a look picked from the tray while the page is open moves the page's switch",
          f"after {took * 1000:.0f}ms" if took is not None else "switch still on pill")
    looks["pill"].click()
    pump(app, 3.0, lambda: shout.overlay.style == "pill")
    # -- your words: Fix on a recent dictation, through to the running app -----
    # The same keys the Words page writes, so the page is driven here too, and
    # the running app must follow both without a restart.
    others = {k: v for k, v in read_config().items() if k not in ("words", "replacements")}
    picked: list[str] = []
    row = ui.EntryRow(Entry(time.time(), "we count the cache tonight"),
                      lambda _e: None, picked.append)
    holder = QtWidgets.QWidget()
    box = QtWidgets.QVBoxLayout(holder)
    box.addWidget(row)
    holder.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    holder.show()
    app.processEvents()

    def click(button) -> None:
        mouse(button, E.MouseButtonPress, button.width() / 2, LEFT, LEFT)
        mouse(button, E.MouseButtonRelease, button.width() / 2, LEFT, NOBUTTON)
        app.processEvents()

    def select_cache() -> None:
        row.text.setFocus()
        row.text.setSelection(13, 5)
        app.processEvents()

    select_cache()
    check(row.text.selectedText() == "cache", "precondition: the word is selected",
          repr(row.text.selectedText()))
    click(row.fix)
    check(picked == ["cache"],
          "Fix hands over the word selected in the dictation, after a real click",
          f"got {picked}")
    holder.close()

    bar = win.recent_page.fix_bar
    bar.open("cache")
    bar.write.setText("cash")
    bar.save.click()
    took = pump(app, 3.0, lambda: shout.vocab.apply("count the cache")[0] == "count the cash")
    check(read_config().get("replacements") == {"cache": "cash"}
          and read_config().get("words", []) == [] and took is not None,
          "Save fix reaches a RUNNING app, and a lowercase fix is not listened for",
          f"file {read_config().get('replacements')} words {read_config().get('words')}")
    bar.open("tseg player")
    bar.write.setText("TCGplayer")
    bar.save.click()
    took = pump(app, 3.0, lambda: "TCGplayer" in shout.vocab.heard())
    check(took is not None and read_config().get("words") == ["TCGplayer"],
          "a fix to a name is listened for too, from the next dictation",
          f"words {read_config().get('words')}")
    check(not bar.isVisible() and "TCGplayer" in win.recent_page.status.text(),
          "the bar closes and says what was saved", win.recent_page.status.text())

    page = win.words_page
    win.show_page("words")
    app.processEvents()
    check(len(page.word_rows_shown) == 1 and len(page.fix_rows_shown) == 2
          and page.budget.text().startswith("10 of "),
          "the Words page lists what Fix saved, without being reopened",
          f"{len(page.word_rows_shown)} words, {len(page.fix_rows_shown)} fixes, "
          f"{page.budget.text()!r}")
    page.word.setText("Ogerpon")
    page.add_word.click()
    page.fix_rows_shown[0].remove.click()          # newest first: tseg player
    took = pump(app, 3.0, lambda: len(shout.vocab.fixes) == 1
                and shout.vocab.words == ("TCGplayer", "Ogerpon"))
    check(took is not None and read_config().get("replacements") == {"cache": "cash"},
          "adding a word and removing a fix on the page reach the running app",
          f"app words {shout.vocab.words}, fixes {shout.vocab.fixes}")
    check({k: v for k, v in read_config().items() if k not in ("words", "replacements")}
          == others, "and no other key in the file moved")
    good = CONFIG.read_text(encoding="utf-8")
    CONFIG.write_text("{", encoding="utf-8")           # caught mid-write
    page.word.setText("Enel")
    page.add_word.click()
    check(CONFIG.read_text(encoding="utf-8") == "{"
          and "Could not read" in page.status.text(),
          "a half-written file is never saved over with empty lists",
          page.status.text())
    CONFIG.write_text(good, encoding="utf-8")
    write_config(read_config() | {"words": [], "replacements": {}})
    pump(app, 3.0, lambda: shout.vocab.words == ())
    win.show_page("orb")
    app.processEvents()

    shout._config_timer.stop()
    shout.cues.close()

    home_rows(app, win, recent)
    settings_rows(app, win)

    # -- the recent dictations page -------------------------------------------
    page = win.recent_page
    win.tabs["recent"].click()
    app.processEvents()
    check(win.stack.currentWidget() is page and win.tabs["recent"].isChecked()
          and not any(win.tabs[k].isChecked() for k in ui.Window.PAGES
                      if k != "recent"),
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
    # Every other page full as well: a dozen words wrapping as chips, fixes, and
    # a day of dictations on home.
    write_config(read_config() | {
        "words": [f"Name{i} Longername{i}" if i % 4 == 0 else f"Word{i}"
                  for i in range(14)],
        "replacements": {f"heard {i}": f"Written {i}" for i in range(8)}})
    win.words_page.refresh()
    (TMP / "Shout" / "shout.log").write_text(
        "\n".join(log_lines(date.today().isoformat()) * 8) + "\n", encoding="utf-8")
    win.home._stamp = win.home._config_stamp = None
    win.home.refresh()
    for name in ui.Window.PAGES:
        win.show_page(name)
        app.processEvents()
        bad = squeezed(win)
        check(not bad, f"nothing is squeezed below its minimum on the {name} page",
              f"{win.width()}x{win.height()}, layout minimum "
              f"{win.minimumSizeHint().width()}x{win.minimumSizeHint().height()}"
              + (f", squeezed: {bad}" if bad else ""))
    tall = win.height()
    short = win.minimumSizeHint().height() - 120
    win.resize(win.width(), short)
    app.processEvents()
    check(win.height() >= win.minimumSizeHint().height(),
          "and the window cannot be dragged below that minimum",
          f"asked for {short}, Qt clamped to {win.height()}")
    # Control: with the floor explicitly lifted -- the only way to get under it --
    # the same sweep must report the clipping that 736px actually causes.
    # Without this the row above passes on a sweep that can no longer see
    # anything, which is how the first version of this gate was wrong.
    # setFixedHeight, not setMinimumSize(0,0): clearing the minimum does not
    # stick, because the layout re-imposes its own on the next activation --
    # which is exactly why the row above holds for the user, and why the first
    # attempt at this control silently measured a window still 840px tall.
    win.setFixedHeight(short)
    app.processEvents()
    check(len(squeezed(win)) > 0,
          "and the sweep detects clipping when it happens (control)",
          f"{len(squeezed(win))} widgets clipped at {short}px tall")
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
    check(preview.renderer is not None,
          "precondition: the orb page holds an OpenGL renderer")
    win.close()
    check(lab.cues._stream is None, "closing the window releases that stream",
          "or every open/close of the window leaks one inside Shout")
    check(preview.renderer is None and not preview._timer.isActive(),
          "and the orb preview's renderer and timer")
    check(hero.renderer is None and not hero._timer.isActive(),
          "and the home orb's")
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
