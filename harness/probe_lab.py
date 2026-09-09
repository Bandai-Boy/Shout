"""Gate: the cue lab writes a config that resolves back to the voice it played.

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

The widget is constructed but never shown. It is an ordinary activating window,
so showing it would steal focus from whatever is in front, and nothing here needs
it mapped — Qt lays out and measures a hidden widget perfectly well.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Before any config read: every path below must land in the throwaway tree, not
# in the real %APPDATA%\Shout\config.json that the running app is using.
TMP = Path(tempfile.mkdtemp(prefix="shout_lab_"))
os.environ["APPDATA"] = str(TMP)
CONFIG = TMP / "Shout" / "config.json"

from PySide6 import QtWidgets  # noqa: E402

from shout.config import Config  # noqa: E402
from shout.cues import PRESETS, Voice  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
import cue_lab  # noqa: E402

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


def as_app_sees_it() -> Voice:
    """Resolve the file the way `shout/__main__.py` does, and only that way."""
    cfg = Config.load()
    return Voice.resolve(cfg.cue_preset, cfg.cue_voice, cfg.cue_presets)


def main() -> int:
    write_config(LEGACY)
    app = QtWidgets.QApplication(sys.argv[:1])
    lab = cue_lab.Lab()
    lab.cues.play = lambda *_a, **_k: None      # audition silently

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

    lab.cues.close()
    app.quit()

    for ok, name, detail in rows:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))
    passed = sum(ok for ok, _, _ in rows)
    print(f"{'FAIL' if passed != len(rows) else 'PASS'} {passed}/{len(rows)} "
          f"cue lab assertions; lab -> config.json -> Voice round trip verified")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
