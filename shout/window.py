"""The Shout window: home, your recent dictations, your words, the cue sounds,
the orb's look, and settings.

Six pages of one window, picked from an icon rail on the left and opened from
the tray icon's menu. It lives inside Shout's own process, not beside it the
way the cue lab used to, because that is where the recent dictations are. They
are kept in memory only (see `shout/recent.py`), and a second process could only
see them if they were written down somewhere.

## Home

What the window opens on. The orb, drawn by the overlay's own renderer, with
the model's state in its middle (Ready / Loading / Error, from a callable Shout
passes in), and four cards: today's dictations and words, the typical
release-to-text time with a sparkline, your words, and the last dictation with
Copy and Fix. Today and Speed are read from `shout.log`, which holds timings and
counts and never words: one `END-TO-END <ms>ms` line per dictation, paired with
the `transcribed ... -> N chars, W words` line before it. Lines written before
the word count existed have none, so the word total counts only the dictations
that carry one.

## Recent dictations

The last few transcripts, newest first, each with a Copy button: for the paste
that landed nowhere because you clicked out of the field. Copy marks the
clipboard private, the same as a dictation does. The store is filled on the
commit thread and polled here, one int per tick, so no signal crosses threads.

## Cue sounds

Audition the cue sounds and save the one you like, under a name of its own.
Pick a material, drag a slider, hear it the moment you let go. Nothing is written
until you press Save, and Save only touches the cue keys in config.json — every
other setting is left as it is.

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
slider re-synthesizes as it moves and plays in a few milliseconds once let go.

The knobs, roughly in the order they matter for "less bright":

    Root        the pitch everything is built from. This is the big one.
    Decay       0 is a held beep; higher is struck and ringing out, which is
                most of what separates "shrill" from "pleasant" at equal volume.
    Brightness  how much upper harmonic. 0 is a pure sine, the softest timbre
                there is; raise it for wood or glass character.
    Length      how long the notes ring. Decay makes long notes taper, not drone.
    Spread      how far apart the two notes are. Below 1 the interval narrows.
    Gap         silence between notes; what makes a blip read as two notes.

Save takes effect in the running app within about half a second — Shout watches
config.json — so there is nothing to restart. Then re-run
`harness/probe_cues.py`. That gate asserts the cue does not change your
transcript, and it is a property of the SOUND — every material here is harmonic
because the VAD rejects tones, and a noisy one could break it.

## Look (the orb)

The orb's look, by eye: how many waves go round, how rough the water is, how
fast it turns, how far your voice raises it, the size, the colour, the comet,
the resting capsule.
The preview is the overlay's own renderer and motion, drawn magnified and at
its real size, in whichever state you pick, with a stand-in voice whose level
you set. Save writes only the knobs that differ from the shipped look, as
`orb_tune`, and the running orb follows within half a second. Holding Ctrl+Win
then shows it with your real voice. Save never changes which look is on screen;
the page's own Pill | Orb switch does, writing the same `overlay_style` key as
the tray menu and following that menu while the page is open.

## Settings

Real controls over config.json, each writing exactly its own key through
`merge()`, which re-reads the file on every write. Cue volume is followed live;
language, microphone, the overlay and cues on/off say plainly that they need a
restart. Start with Windows makes or removes the same Startup shortcut
`scripts/install_shortcuts.ps1 -Autostart` makes, pointing at `shoutw.exe`.

---

## The look

Glass: a darkened slate-lavender to dusty-rose wash, translucent white cards
with a light 1px border, light-weight Segoe UI Variable type, solid white text,
and one white pill as the primary action. The orb is the only saturated colour.

*No backdrop blur, so the frost is the wash showing through.* Qt has no
`backdrop-filter`: a child widget cannot blur what its parent painted. What it
CAN do is translucency, so the wash is painted for real in `Window.paintEvent`
and every card is genuinely translucent over it. A smooth wash under a 12% white
card reads as frosted glass without the blur.

*The wash is darker than the mock-up's.* Measured over every text and surface
pair this window composites, at the wash's lightest corner: the mock-up's
#6e5068 corner put white text on a button inside a card at 4.21:1, and on a
hovered one at 3.81:1. The stops here clear 4.5:1 on all of them. Text fields
are a darker well than the card, so their placeholder can be dimmer than white
and still clear it. Warnings (a cue over the blip ceiling, a word with no room)
say so in words, not in a colour.
`harness/probe_lab.py` measures it, with faded white as the negative control.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import re
import statistics
import subprocess
import time
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date
from pathlib import Path

import numpy as np
import sounddevice as sd
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

from . import orb, words
from .config import Config, config_dir, merge
from .cues import MAX_GESTURE_S, PRESETS, Cues, Voice, build, duration_s, notes
from .devices import follow_default
from .inject import set_clipboard_text
from .overlay import EXPAND_RATE, _ease
from .recent import SIZE, Entry, Recent

# Every colour the window uses. Text is solid white throughout; `disabled` is the
# one faded white, and it is never readable text (see the probe's control).
C = {
    "wash_a": "#3a365c", "wash_b": "#48426a", "wash_c": "#5a4159",
    "text": "#ffffff",
    "ink": "#2f2a4a",                    # dark text on the white button
    "card": "rgba(255,255,255,0.12)", "border": "rgba(255,255,255,0.35)",
    "inset": "rgba(255,255,255,0.10)", "inset_hover": "rgba(255,255,255,0.15)",
    "inset_border": "rgba(255,255,255,0.40)", "hover_border": "rgba(255,255,255,0.65)",
    "rule": "rgba(255,255,255,0.22)", "track": "rgba(255,255,255,0.28)",
    "white_hover": "#f1eef8",
    # A hovered Home card's sheen, across its top only (HoverCard says why).
    "sheen": "rgba(255,255,255,0.06)",
    # Text fields sit a shade DARKER than the card, a well rather than a raised
    # tile: on a white-tinted field no placeholder dimmer than white cleared AA.
    "field": "rgba(24,20,48,0.16)",
    "placeholder": "#d4cde3",
    "popup": "#403a64",
    "disabled": "rgba(255,255,255,0.55)",
}

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

# The orb page's knobs, as three columns of sections: (field, label, decimals,
# suffix, what it does). Their ends are shout.orb.RANGES, which also clamps
# whatever config.json holds. A "%" suffix shows a fraction as a percent.
SPIN_TIP = "Turns per minute. Negative turns the other way."
TURB_TIP = ("How rough the water is. 0 is one even swell going round; higher "
            "makes the crests uneven and puts ripples on them. It never makes "
            "them pointy — your voice adds some of its own.")
# Hands-free is a different water from holding, not just a different count, so
# its Turbulence knob does a different thing with the same 0-1 range.
LATCH_TURB_TIP = ("How rough the water is. 0 is one even swell going round; "
                  "higher rides smaller, faster ripples on top of it, which is "
                  "what makes hands-free look unlike holding. Your voice adds "
                  "some of its own.")
ORB_KNOBS = [
    [("Holding", [
        ("hold_crests", "Waves", 0, "", "How many wave crests go round while you "
                                        "hold the keys."),
        ("hold_turb", "Turbulence", 2, "", TURB_TIP),
        ("hold_spin", "Spin", 1, "", SPIN_TIP),
    ]), ("Hands-free", [
        ("latch_crests", "Waves", 0, "", "How many wave crests go round while "
                                         "hands-free."),
        ("latch_turb", "Turbulence", 2, "", LATCH_TURB_TIP),
        ("latch_spin", "Spin", 1, "", SPIN_TIP),
    ])], [("Voice", [
        ("height", "Wave height", 1, "px", "How far a crest stands out while you "
                                           "are silent."),
        ("push", "Voice push", 1, "px", "How much further your voice raises the "
                                        "crests, at full volume. It roughens "
                                        "them as well."),
        ("swell", "Swell", 0, "%", "How much the whole orb grows at full volume."),
        ("glow", "Glow", 0, "%", "How much brighter it gets at full volume."),
    ]), ("Transcribing", [
        ("comet_rpm", "Comet", 0, "", "How fast the comet goes round, in turns per "
                                      "minute."),
        ("comet_tail", "Tail", 0, "°", "How far round the ring the comet's tail "
                                       "reaches."),
    ])],
    [("Look", [
        ("size", "Size", 0, "px", "The orb, glow edge to glow edge."),
        ("wobble", "Wobble", 2, "", "How far the edge wanders on its own. 0 keeps "
                                    "the ring round."),
        ("hue", "Hue", 0, "°", "Turns every colour round the colour wheel."),
        ("brightness", "Brightness", 2, "", "How bright the ring is."),
    ]), ("At rest", [
        ("rest_w", "Width", 0, "px", "The resting capsule, rim to rim."),
        ("rest_h", "Height", 0, "px", "The resting capsule, rim to rim."),
        ("rim_rpm", "Rim spin", 1, "", "How fast the rim's colour circles, in turns "
                                       "per minute."),
    ])],
]
PREVIEW_STATES = (("idle", "At rest"), ("recording", "Holding"),
                  ("latched", "Hands-free"), ("working", "Transcribing"))

QSS = f"""
#card, #navbar {{
    background: {C['card']};
    border: 1px solid {C['border']};
    border-radius: 24px;
}}
#navbar {{ border-radius: 30px; }}
#tile, #entry {{
    background: {C['inset']};
    border: 1px solid rgba(255,255,255,0.28);
    border-radius: 16px;
}}
#chip {{
    background: {C['inset']};
    border: 1px solid rgba(255,255,255,0.30);
    border-radius: 16px;
}}
#rule {{ background: {C['rule']}; border: none; }}
QLabel {{ color: {C['text']}; background: transparent; }}

QComboBox, QLineEdit {{
    background: {C['field']};
    border: 1px solid {C['border']};
    border-radius: 22px;
    padding: 0 16px;
    min-height: 42px;
    color: {C['text']};
    selection-background-color: #ffffff;
    selection-color: {C['ink']};
}}
QComboBox:hover, QLineEdit:hover {{ border: 1px solid {C['hover_border']}; }}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid #ffffff; }}
QComboBox {{ padding-right: 36px; }}
QComboBox::drop-down {{ border: none; width: 34px; }}
QComboBox::down-arrow {{ image: none; width: 0; height: 0; }}
QComboBox QAbstractItemView {{
    background: {C['popup']};
    border: 1px solid {C['border']};
    border-radius: 12px;
    padding: 4px;
    color: {C['text']};
    outline: none;
    selection-background-color: #ffffff;
    selection-color: {C['ink']};
}}
QComboBox QAbstractItemView::item {{ min-height: 32px; padding-left: 8px;
                                     border-radius: 8px; }}

QPushButton {{
    background: {C['inset']};
    border: 1px solid {C['inset_border']};
    border-radius: 22px;
    color: {C['text']};
    padding: 0 18px;
    min-height: 42px;
}}
QPushButton:hover {{ background: {C['inset_hover']}; border: 1px solid {C['hover_border']}; }}
QPushButton:pressed {{ background: {C['card']}; }}
QPushButton:focus {{ border: 1px solid #ffffff; }}
QPushButton:disabled {{ color: {C['disabled']}; border: 1px solid rgba(255,255,255,0.18);
                        background: transparent; }}
QPushButton#cta {{
    background: #ffffff; border: 1px solid #ffffff; color: {C['ink']};
    padding: 0 22px; font-weight: 600;
}}
QPushButton#cta:hover {{ background: {C['white_hover']}; }}
QPushButton#cta:focus {{ border: 2px solid {C['ink']}; }}
QPushButton#cta:disabled {{ background: rgba(255,255,255,0.18);
                            border: 1px solid rgba(255,255,255,0.18);
                            color: {C['disabled']}; }}
/* Sizes here, not setFixedSize: a sheet's min-height is applied at polish
   time and overrides the widget's own minimum, which squashed the rail's
   buttons to 21px. Contents size; the 1px border goes round it. */
QPushButton#icon36, QPushButton#icon28 {{
    background: transparent; border: 1px solid transparent; padding: 0;
}}
QPushButton#icon36 {{ border-radius: 18px; min-height: 34px; max-height: 34px;
                      min-width: 34px; max-width: 34px; }}
QPushButton#icon28 {{ border-radius: 14px; min-height: 26px; max-height: 26px;
                      min-width: 26px; max-width: 26px; }}
QPushButton#icon36:hover, QPushButton#icon28:hover {{ background: {C['inset_hover']}; }}
QPushButton#icon36:focus, QPushButton#icon28:focus {{ border: 1px solid #ffffff; }}
QPushButton#nav {{
    background: transparent; border: 1px solid transparent; border-radius: 22px;
    padding: 0; min-height: 42px; max-height: 42px; min-width: 42px; max-width: 42px;
}}
QPushButton#nav:hover {{ background: {C['inset_hover']}; }}
QPushButton#nav:checked {{ background: #ffffff; }}
QPushButton#nav:focus {{ border: 1px solid #ffffff; }}
QPushButton#nav:checked:focus {{ border: 2px solid {C['ink']}; }}
QLabel#navlabel {{
    background: {C['popup']}; border: 1px solid {C['border']}; border-radius: 10px;
    padding: 6px 12px; color: {C['text']};
}}

/* A two- or four-way switch: one control, so it reads as a choice of view
   rather than as buttons that do something. */
#switch {{
    background: {C['inset']};
    border: 1px solid {C['border']};
    border-radius: 22px;
}}
QPushButton#seg {{
    background: transparent; border: 1px solid transparent; border-radius: 18px;
    padding: 0 16px; min-height: 34px;
}}
QPushButton#seg:hover {{ background: {C['inset_hover']}; }}
QPushButton#seg:checked {{ background: #ffffff; color: {C['ink']}; }}
QPushButton#seg:focus {{ border: 1px solid #ffffff; }}
QPushButton#seg:checked:focus {{ border: 2px solid {C['ink']}; }}

QSlider {{ min-height: 22px; max-height: 22px; }}
QSlider::groove:horizontal {{
    height: 4px; border-radius: 2px; background: transparent;
}}
QSlider::add-page:horizontal {{
    height: 4px; border-radius: 2px; background: {C['track']};
}}
QSlider::sub-page:horizontal {{
    height: 4px; border-radius: 2px; background: #ffffff;
}}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
    background: #ffffff; border: 1px solid rgba(47,42,74,0.35);
}}
QSlider::handle:horizontal:hover {{ background: {C['white_hover']}; }}
/* `QSlider:focus::handle:horizontal` is the form the Qt docs imply and it is
   MISPARSED: the declarations land on the QSlider widget itself and paint a
   border around every slider regardless of focus, which stacks into what looks
   like a grid drawn over the knobs. Bisected 8 Sep by stripping one rule at a
   time. State goes after the subcontrol. */
QSlider::handle:horizontal:focus {{ border: 2px solid {C['ink']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 4px;
                               min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: rgba(255,255,255,0.40); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
"""

# -- type --------------------------------------------------------------------
# Segoe UI Variable is Windows 11's; the named instances are separate families
# to Qt, and setFamilies falls through to plain Segoe UI where they are missing.
_TEXT = ["Segoe UI Variable Text", "Segoe UI"]
_TEXT_LIGHT = ["Segoe UI Variable Text Light", "Segoe UI Semilight", "Segoe UI"]
_DISPLAY = ["Segoe UI Variable Display Light", "Segoe UI Light", "Segoe UI"]


def font(px: int, display: bool = False, light: bool = False,
         bold: bool = False) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setFamilies(_DISPLAY if display else _TEXT_LIGHT if light else _TEXT)
    f.setPixelSize(px)
    if bold:
        f.setWeight(QtGui.QFont.Weight.DemiBold)
    return f


def label(text: str = "", px: int = 13, *, display: bool = False,
          light: bool = False, bold: bool = False, wrap: bool = False,
          name: str = "") -> QtWidgets.QLabel:
    w = QtWidgets.QLabel(text)
    w.setFont(font(px, display, light, bold))
    w.setWordWrap(wrap)
    if name:
        w.setObjectName(name)
    return w


def section(text: str) -> QtWidgets.QLabel:
    """A card's caption."""
    return label(text, 14, name="section")


def title(text: str) -> QtWidgets.QLabel:
    """A card's heading, where a card has one."""
    return label(text, 19, display=True, name="title")


def rule() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setObjectName("rule")
    line.setFixedHeight(1)
    return line


def status_label() -> QtWidgets.QLabel:
    w = label("", 12, wrap=True, name="status")
    w.setMinimumHeight(30)
    return w


def animations_wanted() -> bool:
    """Windows' equivalent of `prefers-reduced-motion`: Settings -> Accessibility
    -> Visual effects -> Animation effects. SPI_GETCLIENTAREAANIMATION."""
    try:
        enabled = ctypes.c_int(1)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
        return bool(enabled.value)
    except Exception:
        return True


# -- icons -------------------------------------------------------------------
# Stroke icons as inline SVG on a 24-unit grid, the mock-up's own paths.
ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z"/>',
    "recent": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "words": '<path d="M4 7V5h16v2"/><path d="M12 5v14"/><path d="M9 19h6"/>',
    "sounds": ('<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7"/>'
               '<path d="M18.5 5.5a9 9 0 0 1 0 13"/>'),
    "orb": '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="4"/>',
    "settings": ('<path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12"/>'
                 '<circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/>'
                 '<circle cx="18" cy="18" r="2"/>'),
    "lock": ('<rect x="5" y="11" width="14" height="10" rx="2"/>'
             '<path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
    "copy": ('<rect x="9" y="9" width="12" height="12" rx="2"/>'
             '<path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>'),
    "fix": '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "close": '<path d="M6 6l12 12M18 6 6 18"/>',
}


def svg_pixmap(name: str, colour: str, px: int, stroke: float = 1.6,
               dpr: float = 2.0) -> QtGui.QPixmap:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           f'stroke="{colour}" stroke-width="{stroke}" stroke-linecap="round" '
           f'stroke-linejoin="round">{ICONS[name]}</svg>')
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
    pm = QtGui.QPixmap(round(px * dpr), round(px * dpr))
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    renderer.render(p)
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def svg_icon(name: str, px: int, checked_colour: str | None = None,
             stroke: float = 1.6) -> QtGui.QIcon:
    icon = QtGui.QIcon()
    icon.addPixmap(svg_pixmap(name, C["text"], px, stroke),
                   QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
    if checked_colour:
        icon.addPixmap(svg_pixmap(name, checked_colour, px, stroke),
                       QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.On)
    return icon


def icon_label(name: str, px: int = 14) -> QtWidgets.QLabel:
    w = QtWidgets.QLabel()
    w.setPixmap(svg_pixmap(name, C["text"], px))
    w.setFixedSize(px, px)
    return w


def button(text: str, slot=None, *, primary: bool = False, icon: str = "",
           name: str = "") -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setObjectName("cta")
    elif name:
        b.setObjectName(name)
    if icon:
        b.setIcon(svg_icon(icon, 14))
        b.setIconSize(QtCore.QSize(14, 14))
    if slot is not None:
        b.clicked.connect(lambda _=False: slot())
    return b


def remove_button(what: str, size: int = 36) -> QtWidgets.QPushButton:
    """An icon-only X. Tooltip and accessible name both say what it removes."""
    b = QtWidgets.QPushButton()
    b.setObjectName(f"icon{size}")
    b.setIcon(svg_icon("close", 13, stroke=1.8))
    b.setIconSize(QtCore.QSize(13, 13))
    b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    b.setToolTip(f"Remove {what}")
    b.setAccessibleName(f"Remove {what}")
    return b


def page_head(heading: str, sub: str = "", right: QtWidgets.QWidget | None = None
              ) -> QtWidgets.QLayout:
    """A page's title, its one-line explanation, and anything that belongs to
    the whole page on the right."""
    row = QtWidgets.QHBoxLayout()
    row.setSpacing(16)
    col = QtWidgets.QVBoxLayout()
    col.setSpacing(4)
    col.addWidget(label(heading, 26, display=True, name="heading"))
    if sub:
        col.addWidget(label(sub, 13, wrap=True, name="sub"))
    row.addLayout(col, 1)
    if right is not None:
        row.addWidget(right, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
    return row


class Pill(QtWidgets.QAbstractButton):
    """A switch: a track and a dot, with its label beside it. Kept a real
    QAbstractButton so Space still toggles it and it still takes a focus ring,
    which a hand-painted QWidget would quietly lose."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setText(text)
        self.setAccessibleName(text)
        self.setFont(font(13))
        self._pos = 0.0
        self._anim = QtCore.QPropertyAnimation(self, b"dot", self)
        self._anim.setDuration(200 if animations_wanted() else 0)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        metrics = QtGui.QFontMetrics(self.font())
        self.setMinimumSize(metrics.horizontalAdvance(text) + 70, 44)

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def get_dot(self) -> float:
        return self._pos

    def set_dot(self, value: float) -> None:
        self._pos = value
        self.update()

    dot = QtCore.Property(float, get_dot, set_dot)

    def setChecked(self, on: bool) -> None:      # noqa: N802 - Qt casing
        super().setChecked(on)
        self._pos = 1.0 if on else 0.0           # no animation on the initial state
        self.update()

    def sizeHint(self) -> QtCore.QSize:          # noqa: N802 - Qt casing
        return self.minimumSize()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        on = self.isChecked()
        cy = self.height() / 2.0
        track = QtCore.QRectF(4, cy - 11, 40, 22)
        if self.hasFocus():
            p.setPen(QtGui.QPen(QtGui.QColor(C["text"]), 1.0))
            p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-3, -3, 3, 3), 14, 14)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 255 if on else 110), 1.0))
        p.setBrush(QtGui.QColor("#ffffff") if on else QtGui.QColor(255, 255, 255, 40))
        p.drawRoundedRect(track, 11, 11)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        x = track.left() + 3 + self._pos * 18
        p.setBrush(QtGui.QColor(C["ink"]) if on else QtGui.QColor("#ffffff"))
        p.drawEllipse(QtCore.QRectF(x, track.top() + 3, 16, 16))
        p.setPen(QtGui.QColor(C["text"]))
        p.setFont(self.font())
        p.drawText(QtCore.QRectF(56, 0, self.width() - 56, self.height()),
                   int(QtCore.Qt.AlignmentFlag.AlignVCenter
                       | QtCore.Qt.AlignmentFlag.AlignLeft), self.text())
        p.end()


class Combo(QtWidgets.QComboBox):
    """Qt draws no arrow once `down-arrow` is blanked, and the CSS
    border-triangle trick that would replace it renders as a filled square in
    QSS. Two strokes, painted over the styled box."""

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(C["text"]), 1.6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx, cy = self.width() - 22.0, self.height() / 2.0
        p.drawPolyline([QtCore.QPointF(cx - 4, cy - 2),
                        QtCore.QPointF(cx, cy + 2.5),
                        QtCore.QPointF(cx + 4, cy - 2)])
        p.end()


class JumpToClick(QtWidgets.QProxyStyle):
    """Qt jumps a slider to the click only for the buttons this hint names,
    which is the middle button out of the box. The left button pages toward the
    click instead, one pageStep per press: 1% of the range here."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QtWidgets.QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return QtCore.Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


class Slider(QtWidgets.QSlider):
    """Two things a stock QSlider gets wrong for auditioning.

    A click on the track steps toward the click instead of going there. With
    the proxy style it jumps, and the same press carries on as a drag, which is
    Qt's own path for the middle button.

    And a drag emits valueChanged for every pixel. Each one restarted the cue,
    which `Cues.play()` does by design, so a drag stacked dozens of restarts
    into a screech. `held` says a mouse gesture is in progress, and `settled`
    fires once when it ends, if the value moved."""

    settled = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__(QtCore.Qt.Orientation.Horizontal)
        # Per slider rather than app-wide, so the behaviour belongs to the widget
        # whoever built the QApplication (probe_lab builds its own). The proxy
        # wraps a fresh copy of the app's style, so the look does not change;
        # setStyle does not take ownership, hence the attribute.
        self._style = JumpToClick(QtWidgets.QApplication.style().name())
        self.setStyle(self._style)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.held = False
        self._from = 0

    def mousePressEvent(self, e: QtGui.QMouseEvent) -> None:
        # Set BEFORE Qt handles the press: the jump emits valueChanged from
        # inside it, ahead of sliderPressed, so isSliderDown() is still False.
        self.held = e.button() == QtCore.Qt.MouseButton.LeftButton
        self._from = self.value()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e: QtGui.QMouseEvent) -> None:
        super().mouseReleaseEvent(e)
        if self.held:
            self.held = False
            if self.value() != self._from:
                self.settled.emit()


def value_label(width: int) -> QtWidgets.QLabel:
    v = label("", 13, name="value")
    v.setMinimumWidth(width)
    v.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight
                   | QtCore.Qt.AlignmentFlag.AlignVCenter)
    return v


class StatTile(QtWidgets.QFrame):
    """A small inset readout: a caption over a value."""

    def __init__(self, caption: str) -> None:
        super().__init__()
        self.setObjectName("tile")
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(2)
        self.caption = label(caption, 12)
        self.value = label("--", 17, display=True, name="value")
        lay.addWidget(self.caption)
        lay.addWidget(self.value)

    def set_value(self, text: str) -> None:
        self.value.setText(text)


class Wave(QtWidgets.QWidget):
    """The gesture drawn end to end. Decay in particular is far easier to
    understand as a shape than as a number."""

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(82)
        self._audio = np.zeros(1, dtype=np.float32)
        self.setAccessibleName("The three cues drawn as a waveform")

    def set_audio(self, audio: np.ndarray) -> None:
        self._audio = audio
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1.0))
        p.setBrush(QtGui.QColor(255, 255, 255, 18))
        p.drawRoundedRect(r, 16, 16)
        cy = r.center().y()
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 50), 1.0))
        p.drawLine(QtCore.QPointF(r.left() + 14, cy), QtCore.QPointF(r.right() - 14, cy))
        a = self._audio
        if len(a) < 2:
            p.end()
            return
        peak = max(1e-6, float(np.max(np.abs(a))))
        left, width = int(r.left()) + 14, max(1, int(r.width()) - 28)
        edges = np.linspace(0, len(a), width + 1).astype(int)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 225), 1.0))
        half = r.height() / 2.0 - 12.0
        for i in range(width):
            seg = a[edges[i]:max(edges[i] + 1, edges[i + 1])]
            h = float(np.max(np.abs(seg))) / peak * half
            x = left + i
            p.drawLine(QtCore.QPointF(x, cy - h), QtCore.QPointF(x, cy + h))
        p.end()


class Meter(QtWidgets.QWidget):
    """A 2px bar: how much of a budget is used."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(2)
        self.fraction = 0.0

    def set_fraction(self, f: float) -> None:
        self.fraction = max(0.0, min(1.0, f))
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), QtGui.QColor(255, 255, 255, 72))
        p.fillRect(QtCore.QRect(0, 0, round(self.width() * self.fraction), 2),
                   QtGui.QColor("#ffffff"))
        p.end()


class Sparkline(QtWidgets.QWidget):
    """Release-to-text times, oldest to newest, with the newest marked."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(44)
        self.setMinimumWidth(60)
        self.values: list[float] = []

    def set_values(self, values: list[float]) -> None:
        self.values = list(values)
        if values:
            self.setAccessibleName(
                f"Release-to-text time over your last {len(values)} dictations "
                f"today, from {min(values) / 1000:.1f} to {max(values) / 1000:.1f} "
                f"seconds")
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        if not self.values:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        lo, hi = min(self.values), max(self.values)
        span = (hi - lo) or 1.0
        w, h, pad = self.width() - 6.0, self.height(), 6.0
        n = len(self.values)
        pts = [QtCore.QPointF(2 + (w - 2) * (i / (n - 1) if n > 1 else 1.0),
                              h - pad - (v - lo) / span * (h - 2 * pad))
               for i, v in enumerate(self.values)]
        pen = QtGui.QPen(QtGui.QColor("#ffffff"), 1.4)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        if n > 1:
            p.drawPolyline(pts)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor("#ffffff"))
        p.drawEllipse(pts[-1], 3.0, 3.0)
        p.end()


class FlowLayout(QtWidgets.QLayout):
    """Left to right, wrapping: Qt has no such layout built in."""

    def __init__(self, spacing: int = 8) -> None:
        super().__init__()
        self._items: list[QtWidgets.QLayoutItem] = []
        self._gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:            # noqa: N802 - Qt casing
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i: int):                   # noqa: N802 - Qt casing
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i: int):                   # noqa: N802 - Qt casing
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):              # noqa: N802 - Qt casing
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:        # noqa: N802 - Qt casing
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt casing
        return self._place(QtCore.QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QtCore.QRect) -> None:  # noqa: N802 - Qt casing
        super().setGeometry(rect)
        self._place(rect, False)

    def sizeHint(self) -> QtCore.QSize:         # noqa: N802 - Qt casing
        return self.minimumSize()

    def minimumSize(self) -> QtCore.QSize:      # noqa: N802 - Qt casing
        size = QtCore.QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        return size

    def _place(self, rect: QtCore.QRect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        for item in self._items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self._gap, 0
            if not test:
                item.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), hint))
            x += hint.width() + self._gap
            line = max(line, hint.height())
        return y + line - rect.y()


def scroller(body: QtWidgets.QWidget, min_height: int = 0) -> QtWidgets.QScrollArea:
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    if min_height:
        scroll.setMinimumHeight(min_height)
    scroll.setWidget(body)
    # Both, or the wash stops at the list: the viewport and the widget inside it
    # each fill their own background by default.
    scroll.viewport().setAutoFillBackground(False)
    body.setAutoFillBackground(False)
    return scroll


class Card(QtWidgets.QFrame):
    def __init__(self, margins: tuple[int, int, int, int] = (22, 20, 22, 20),
                 spacing: int = 14) -> None:
        super().__init__()
        self.setObjectName("card")
        self.box = QtWidgets.QVBoxLayout(self)
        self.box.setContentsMargins(*margins)
        self.box.setSpacing(spacing)


def qcolour(token: str) -> QtGui.QColor:
    """A '#rrggbb' or 'rgba(r,g,b,a)' token from C as a QColor."""
    if token.startswith("#"):
        return QtGui.QColor(token)
    r, g, b, a = (float(v) for v in token[5:-1].split(","))
    return QtGui.QColor(int(r), int(g), int(b), round(a * 255))


def _tween(owner: QtCore.QObject, ms: int, step) -> QtCore.QVariantAnimation:
    anim = QtCore.QVariantAnimation(owner)
    anim.setDuration(ms)
    anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
    anim.valueChanged.connect(lambda v: step(float(v)))
    return anim


def _run(anim: QtCore.QVariantAnimation, start: float, end: float, step) -> None:
    """Animate from start to end, or jump there when Windows says no animations."""
    anim.stop()
    if animations_wanted():
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.start()
    else:
        step(end)


class HoverCard(Card):
    """A Home card that answers the pointer: its border brightens and a sheen
    fades in across its top. The sheen fades out by SHEEN_REACH of the height,
    above the card's buttons, because white on a hovered button in a card is
    already the tightest pair in the window (4.51:1) and any fill under it
    fails AA. probe_lab asserts both halves."""

    SHEEN_REACH = 0.45
    MS = 160

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.glow = 0.0
        self._anim = _tween(self, self.MS, self._set)

    def _set(self, value: float) -> None:
        self.glow = value
        self.update()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        _run(self._anim, self.glow, 1.0, self._set)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        _run(self._anim, self.glow, 0.0, self._set)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)           # the QSS card
        if self.glow <= 0.0:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        sheen = qcolour(C["sheen"])
        sheen.setAlphaF(sheen.alphaF() * self.glow)
        grad = QtGui.QLinearGradient(0, 0, 0, self.height() * self.SHEEN_REACH)
        grad.setColorAt(0.0, sheen)
        grad.setColorAt(1.0, QtGui.QColor(255, 255, 255, 0))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(rect, 24, 24)
        # The border, from its resting white to the hover one.
        rest, hover = qcolour(C["border"]), qcolour(C["hover_border"])
        edge = QtGui.QColor(255, 255, 255, round(rest.alpha() + (hover.alpha()
                                                                 - rest.alpha()) * self.glow))
        p.setPen(QtGui.QPen(edge, 1.0))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(rect, 24, 24)


class NavLabel(QtWidgets.QLabel):
    """The rail's hover label, the way Vendor Vault's sidebar does it: the
    page's name in a pill beside the icon, at once, where a tooltip waits
    ~700ms and draws in the system's style. It also shows for keyboard focus.
    A child of the window, raised over the page, and transparent to the mouse
    so it never takes the hover from what is under it."""

    GAP = 10                                # from the rail's right edge
    SLIDE = 6                               # px it slides in from
    MS = 140

    def __init__(self, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("navlabel")
        self.setFont(font(13))
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._fade = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade)
        self._anim = _tween(self, self.MS, self._step)
        self._at = QtCore.QPoint()
        self.for_tab: QtWidgets.QWidget | None = None
        self.hide()

    def show_for(self, tab: QtWidgets.QWidget, rail: QtWidgets.QWidget) -> None:
        self.for_tab = tab
        self.setText(tab.accessibleName())
        self.adjustSize()
        mid = tab.mapTo(self.parentWidget(), QtCore.QPoint(0, tab.height() // 2))
        right = rail.mapTo(self.parentWidget(), QtCore.QPoint(rail.width(), 0)).x()
        self._at = QtCore.QPoint(right + self.GAP, mid.y() - self.height() // 2)
        self.raise_()
        self.show()
        _run(self._anim, 0.0, 1.0, self._step)

    def _step(self, t: float) -> None:
        self._fade.setOpacity(t)
        self.move(self._at.x() - round(self.SLIDE * (1.0 - t)), self._at.y())

    def hide_for(self, tab: QtWidgets.QWidget) -> None:
        if tab is self.for_tab:
            self._anim.stop()
            self.for_tab = None
            self.hide()


def switch(options, on_pick, group_parent, columns: int = 0
           ) -> tuple[QtWidgets.QFrame, dict[str, QtWidgets.QPushButton]]:
    """A segmented control: (key, text) options, one checked at a time. All
    in one row, or wrapped to `columns`."""
    frame = QtWidgets.QFrame()
    frame.setObjectName("switch")
    lay = QtWidgets.QGridLayout(frame)
    lay.setContentsMargins(4, 4, 4, 4)
    lay.setSpacing(2)
    group = QtWidgets.QButtonGroup(group_parent)
    buttons: dict[str, QtWidgets.QPushButton] = {}
    for key, text in options:
        b = QtWidgets.QPushButton(text)
        b.setObjectName("seg")
        b.setCheckable(True)
        # Keyboard focus only: a clicked segment that keeps a focus ring reads
        # as a second highlight laid over the checked one.
        b.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
        b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, k=key: on_pick(k))
        group.addButton(b)
        i = len(buttons)
        lay.addWidget(b, *(divmod(i, columns) if columns else (0, i)))
        buttons[key] = b
    return frame, buttons


class Lab(QtWidgets.QWidget):
    """The cue sounds page. Transparent: `Window` paints what shows through."""

    def __init__(self) -> None:
        super().__init__()
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
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        outer.addLayout(page_head("Cue sounds", "Three gestures, one voice. Nothing "
                                  "is saved until you press Save."))

        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(16)

        # -- material, waveform, readout --
        top_card = Card()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.preset = Combo()
        self.preset.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.preset.setAccessibleName("Material")
        self.preset.setToolTip("Material")
        self.preset.currentTextChanged.connect(self._on_preset)
        self.auto = Pill("Play on change")
        self.auto.setChecked(True)
        top_card.box.addWidget(section("Material"))
        row.addWidget(self.preset, 1)
        row.addWidget(self.auto)
        top_card.box.addLayout(row)

        self.wave = Wave()
        top_card.box.addWidget(self.wave, 1)

        self.tiles = {
            "notes": StatTile("Notes"),
            "longest": StatTile("Longest gesture"),
            "peak": StatTile("Peak"),
        }
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(8)
        for tile in self.tiles.values():
            tiles.addWidget(tile, 1)
        top_card.box.addLayout(tiles)
        cards.addWidget(top_card, 1)

        # -- knobs --
        voice_card = Card(spacing=10)
        voice_card.box.addWidget(section("Voice"))
        grid = QtWidgets.QGridLayout()
        grid.setVerticalSpacing(6)
        grid.setHorizontalSpacing(12)
        self.sliders: dict[str, Slider] = {}
        self.values: dict[str, QtWidgets.QLabel] = {}
        rows = KNOBS + [("_volume", "Volume", 0.0, 1.0, 2)]
        for row_i, (fieldname, text, lo, hi, dp) in enumerate(rows):
            grid.addWidget(label(text, 13, name="knob"), row_i, 0)
            s = Slider()
            s.setRange(0, STEPS)
            s.setAccessibleName(text)
            s.valueChanged.connect(self._on_slider)
            s.settled.connect(self._on_settled)
            grid.addWidget(s, row_i, 1)
            v = value_label(52)
            grid.addWidget(v, row_i, 2)
            self.sliders[fieldname] = s
            self.values[fieldname] = v
        grid.setColumnStretch(1, 1)
        voice_card.box.addLayout(grid)
        voice_card.box.addStretch(1)
        cards.addWidget(voice_card, 1)
        outer.addLayout(cards, 1)

        # -- audition + save --
        save_card = Card(spacing=12)
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(section("Audition"))
        head.addStretch(1)
        for name, text in (("start", "Start"), ("stop", "Stop"),
                           ("latch", "Latch")):
            head.addWidget(button(text, lambda n=name: self._play(n)))
        head.addWidget(button("All three", self._play_all))
        save_card.box.addLayout(head)
        save_card.box.addWidget(rule())

        named = QtWidgets.QHBoxLayout()
        named.setSpacing(8)
        named.addWidget(section("Save as"))
        self.name = QtWidgets.QLineEdit()
        self.name.setMaxLength(40)
        self.name.setPlaceholderText("A name for this voice")
        self.name.setAccessibleName("Voice name")
        self.name.returnPressed.connect(self._save)
        named.addWidget(self.name, 1)
        self.delete = button("Delete", self._delete, name="danger")
        named.addWidget(self.delete)
        named.addWidget(button("Revert", self._revert))
        save = button("Save to config", self._save, primary=True)
        save.setDefault(True)
        named.addWidget(save)
        save_card.box.addLayout(named)

        self.status = status_label()
        save_card.box.addWidget(self.status)
        outer.addWidget(save_card)

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
        # Mid-drag the wave, the readouts and the synth all follow the knob; only
        # the audition waits for the release. Keys still play on every step.
        dragging = any(s.held for s in self.sliders.values())
        self._apply(play="start" if self.auto.isChecked() and not dragging
                    else None)

    def _on_settled(self) -> None:
        if self.auto.isChecked():
            self._play("start")

    def _apply(self, play: str | None) -> None:
        self.cues.set_volume(self.volume)
        self.cues.set_voice(self.voice)
        self.wave.set_audio(build("latch", self.cues.rate, self.volume, self.voice))
        longest = max(duration_s(n, self.voice) for n in ("start", "stop", "latch"))
        peak = float(np.max(np.abs(build("start", self.cues.rate, self.volume,
                                         self.voice))))
        over = longest > MAX_GESTURE_S
        self.tiles["notes"].set_value(
            "  ".join(f"{f:.0f}" for f, _ in notes("latch", self.voice)) + " Hz")
        self.tiles["longest"].set_value(
            f"{longest * 1000:.0f}ms" + ("  OVER" if over else ""))
        self.tiles["longest"].caption.setText(
            "Over the blip ceiling" if over else "Longest gesture")
        self.tiles["peak"].set_value(f"{peak:.3f}")
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
        return merge(keys)

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
        self._write(keys)
        self.voice = replace(self.voice, name=name)
        self._rebuild_list()
        self._sync_widgets()
        # No path in here: a long one wraps this to a third line, which the
        # window does not grow for, and the slider card pays the 15px.
        self.status.setText(
            f"Saved {what}. Shout plays it from your next dictation, no restart "
            f"needed. Then re-run harness/probe_cues.py.")

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


def speech_level(t: float) -> float:
    """A stand-in for talking, as a 0-1 envelope: syllables about four a
    second, louder and quieter phrases, and a breath every few seconds. The
    rough shape a level meter shows for speech, not a model of it."""
    syllable = max(0.0, math.sin(t * math.tau * 4.1)) ** 0.7
    phrase = 0.65 + 0.35 * math.sin(t * math.tau * 0.43)
    return 0.0 if t % 3.2 > 2.6 else syllable * phrase


class OrbPreview(QtWidgets.QWidget):
    """The orb as the overlay draws it, twice: magnified, to see what a knob
    does, and at the size it really is on screen. Both are the overlay's own
    Renderer and Choreography, rendered the overlay's way, so what you tune here
    is what the app draws and not a lookalike of it. Animates only while
    visible."""

    ZOOM = 2.0
    GAP = 14.0

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(340, 212)
        self.state = "recording"
        self.level = 0.3
        self.talking = True
        self.motion = orb.Choreography()
        self.expand = 0.0
        self.frames: tuple[QtGui.QImage | None, QtGui.QImage] | None = None
        self.drawn = 0
        try:
            self.renderer: orb.Renderer | None = orb.Renderer()
        except Exception:
            self.renderer = None
        self._t0 = self._last = time.perf_counter()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)

    def zoom(self) -> float:
        """The magnified view's scale: up to ZOOM, as far as the tile allows
        beside the actual-size view. 0 when that is too little to be worth
        showing, which a very big tune can make it."""
        win = self.motion.tune.win
        room = min(self.height() - 4.0, self.width() - 3 * self.GAP - win)
        z = min(self.ZOOM, room / win)
        return z if z >= 1.25 else 0.0

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last, 0.1)
        self._last = now
        # Per frame, like the overlay's own inflation.
        target = 0.0 if self.state == "idle" else 1.0
        self.expand += (target - self.expand) * EXPAND_RATE
        if abs(self.expand - target) <= 0.001:
            self.expand = target
        talk = speech_level(now - self._t0) if self.talking else 1.0
        self.motion.step(dt, self.state, self.level * talk)
        self.render_frames()
        self.update()

    def render_frames(self) -> None:
        if self.renderer is None:
            return
        dpr = self.devicePixelRatioF()
        win = self.motion.tune.win
        morph = _ease(self.expand)
        views = []
        try:
            for zoom in (self.zoom(), 1.0):
                if not zoom:
                    views.append(None)
                    continue
                image = self.renderer.render(self.motion.uniforms(morph, dpr * zoom),
                                             round(win * dpr * zoom * orb.SUPERSAMPLE))
                image.setDevicePixelRatio(dpr * orb.SUPERSAMPLE)
                views.append(image)
        except Exception:
            self.close_renderer()
            return
        self.frames = (views[0], views[1])
        self.drawn += 1

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        r = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 70), 1.0))
        p.setBrush(QtGui.QColor(20, 16, 40, 70))
        p.drawRoundedRect(r, 16, 16)
        p.setFont(font(11))
        p.setPen(QtGui.QColor(C["text"]))
        if self.renderer is None:
            p.drawText(r.adjusted(16, 0, -16, 0),
                       int(QtCore.Qt.AlignmentFlag.AlignCenter
                           | QtCore.Qt.TextFlag.TextWordWrap),
                       "The orb needs OpenGL, which is not available here.")
            p.end()
            return
        if self.frames is None:
            p.end()
            return
        big, actual = self.frames
        win = self.motion.tune.win
        x = self.GAP
        if big is not None:
            side = win * self.zoom()
            p.drawImage(QtCore.QPointF(x, (self.height() - side) / 2.0), big)
            p.drawText(QtCore.QPointF(x + 4, 20), f"{self.zoom():g}x")
            x += side + self.GAP
        ax = x + (self.width() - x - win) / 2.0
        p.drawImage(QtCore.QPointF(ax, (self.height() - win) / 2.0), actual)
        p.drawText(QtCore.QRectF(x, 8, self.width() - x - self.GAP, 16),
                   int(QtCore.Qt.AlignmentFlag.AlignHCenter), "Actual size")
        p.end()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._last = time.perf_counter()
        self._timer.start(16)
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def close_renderer(self) -> None:
        self._timer.stop()
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
        self.update()


class OrbPage(QtWidgets.QWidget):
    """The orb page, labelled Look. Transparent: `Window` paints what shows
    through."""

    def __init__(self) -> None:
        super().__init__()
        self.tune = orb.Tune.resolve(Config.load().orb_tune)
        self.sliders: dict[str, Slider] = {}
        self.values: dict[str, QtWidgets.QLabel] = {}
        self.suffixes: dict[str, tuple[int, str]] = {}
        self._loading = False
        self._build_ui()
        self._sync_widgets()
        self.show_state(self.preview.state)
        # The tray's Appearance menu writes the same key, possibly while this
        # page is open, so the switch follows the file rather than a snapshot.
        self._style_stamp: int | None = None
        self._style_timer = QtCore.QTimer(self)
        self._style_timer.timeout.connect(self._follow_style)
        self._follow_style()

    def _build_ui(self) -> None:
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        right = QtWidgets.QWidget()
        on_screen = QtWidgets.QHBoxLayout(right)
        on_screen.setContentsMargins(0, 0, 0, 0)
        on_screen.setSpacing(10)
        on_screen.addWidget(label("On screen", 13))
        looks, self.style_buttons = switch((("pill", "Pill"), ("orb", "Orb")),
                                           self._set_style, self)
        for key, b in self.style_buttons.items():
            b.setAccessibleName(f"Show the {key}")
        on_screen.addWidget(looks)
        outer.addLayout(page_head("Look", "Drag a knob and the preview follows. "
                                  "Save puts it on your screen.", right))

        # -- preview --
        top = Card()
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(16)
        self.preview = OrbPreview()
        row.addWidget(self.preview, 1)
        side = QtWidgets.QWidget()
        side.setFixedWidth(236)
        column = QtWidgets.QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(6)
        column.addWidget(label("Preview as", 13))
        states, self.state_buttons = switch(PREVIEW_STATES, self.show_state, self,
                                            columns=2)
        column.addWidget(states)
        column.addSpacing(4)
        column.addWidget(label("Voice level", 13))
        level_row = QtWidgets.QHBoxLayout()
        level_row.setSpacing(8)
        self.level = Slider()
        self.level.setRange(0, STEPS)
        self.level.setAccessibleName("Preview voice level")
        self.level.setToolTip("How loud the preview's voice is. Ordinary speech "
                              "at arm's length is somewhere near 15.")
        self.level.valueChanged.connect(self._on_level)
        self.level_value = value_label(28)
        level_row.addWidget(self.level, 1)
        level_row.addWidget(self.level_value)
        column.addLayout(level_row)
        self.talking = Pill("Talking")
        self.talking.setChecked(True)
        self.talking.setToolTip("On, the level comes and goes like speech. Off, it "
                                "holds steady.")
        self.talking.toggled.connect(lambda on: setattr(self.preview, "talking", on))
        column.addWidget(self.talking)
        column.addStretch(1)
        row.addWidget(side)
        top.box.addLayout(row)
        outer.addWidget(top)
        self.level.setValue(round(self.preview.level * STEPS))
        self._on_level()

        # -- knobs --
        knobs = Card(spacing=12)
        columns = QtWidgets.QHBoxLayout()
        columns.setSpacing(24)
        for sections in ORB_KNOBS:
            grid = QtWidgets.QGridLayout()
            grid.setVerticalSpacing(4)
            grid.setHorizontalSpacing(10)
            r = 0
            for heading, knob_rows in sections:
                if r:
                    grid.setRowMinimumHeight(r, 6)
                    r += 1
                grid.addWidget(section(heading), r, 0, 1, 3)
                r += 1
                for name, text, dp, suffix, tip in knob_rows:
                    caption = label(text, 13, name="knob")
                    caption.setToolTip(tip)
                    grid.addWidget(caption, r, 0)
                    s = Slider()
                    lo, hi = orb.RANGES[name]
                    s.setRange(*((int(lo), int(hi)) if self._whole(name) else (0, STEPS)))
                    s.setAccessibleName(f"{heading} {text}")
                    s.setToolTip(tip)
                    s.valueChanged.connect(lambda _v, n=name: self._on_knob(n))
                    grid.addWidget(s, r, 1)
                    v = value_label(50)
                    grid.addWidget(v, r, 2)
                    self.sliders[name] = s
                    self.values[name] = v
                    self.suffixes[name] = (dp, suffix)
                    r += 1
            grid.setColumnStretch(1, 1)
            grid.setRowStretch(r, 1)
            columns.addLayout(grid, 1)
        knobs.box.addLayout(columns)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        self.status = status_label()
        buttons.addWidget(self.status, 1)
        for text, slot in (("Defaults", self._defaults), ("Revert", self._revert)):
            buttons.addWidget(button(text, slot))
        self.save = button("Save to config", self._save, primary=True)
        buttons.addWidget(self.save)
        knobs.box.addLayout(buttons)
        outer.addWidget(knobs)
        outer.addStretch(1)

    @staticmethod
    def _whole(name: str) -> bool:
        return isinstance(getattr(orb.Tune(), name), int)

    # -- slider <-> tune -----------------------------------------------------

    def _sync_widgets(self) -> None:
        """Push the tune into the sliders without reading them back, and into
        the preview."""
        self._loading = True
        try:
            for name, s in self.sliders.items():
                value = getattr(self.tune, name)
                lo, hi = orb.RANGES[name]
                s.setValue(round(value) if self._whole(name)
                           else round((value - lo) / (hi - lo) * STEPS))
        finally:
            self._loading = False
        for name in self.sliders:
            self._label(name)
        self.preview.motion.tune = self.tune

    def _label(self, name: str) -> None:
        dp, suffix = self.suffixes[name]
        value = getattr(self.tune, name) * (100 if suffix == "%" else 1)
        self.values[name].setText(f"{value + 0.0:.{dp}f}{suffix}")

    def _on_knob(self, name: str) -> None:
        """Only the knob that moved is read back. Reading them all would snap
        every untouched value to its slider step, and the shipped look is not
        on those steps."""
        if self._loading:
            return
        s = self.sliders[name]
        lo, hi = orb.RANGES[name]
        value = s.value() if self._whole(name) else lo + (hi - lo) * s.value() / STEPS
        self.tune = replace(self.tune, **{name: value})
        self._label(name)
        self.preview.motion.tune = self.tune

    def _on_level(self) -> None:
        self.preview.level = self.level.value() / STEPS
        self.level_value.setText(f"{self.preview.level * 100:.0f}")

    def show_state(self, key: str) -> None:
        self.preview.state = key
        self.state_buttons[key].setChecked(True)

    # -- persistence ---------------------------------------------------------

    def changes(self) -> dict:
        """The knobs that differ from the shipped look by more than one slider
        step, which is all Save writes. A knob you never moved stays out of the
        file, and so keeps following the shipped look if that ever changes."""
        shipped = orb.Tune()
        out = {}
        for f in fields(orb.Tune):
            have, want = getattr(self.tune, f.name), getattr(shipped, f.name)
            lo, hi = orb.RANGES[f.name]
            if self._whole(f.name):
                if have != want:
                    out[f.name] = have
            elif abs(have - want) > (hi - lo) / STEPS:
                out[f.name] = round(have, 4)
        return out

    def _save(self) -> None:
        # Merged, like the cue keys: every other setting in the file stays.
        merge({"orb_tune": self.changes()})
        if Config.load().overlay_style == "orb":
            self.status.setText("Saved. The orb on your screen follows within half a "
                                "second; hold Ctrl+Win to see it with your own voice.")
        else:
            self.status.setText("Saved. Shout is showing the pill; switch On screen to "
                                "Orb to see it.")

    def _set_style(self, style: str) -> None:
        """Which look the overlay draws. Written to the file, which the running
        app follows within half a second, the same key the tray menu writes.
        Leaves the tune alone either way."""
        merge({"overlay_style": style})
        self.status.setText("The overlay is the orb now." if style == "orb" else
                            "The overlay is the pill now. The orb keeps your tune.")

    def _follow_style(self) -> None:
        """A stat per tick, and a read only when the file changed."""
        try:
            stamp = (config_dir() / "config.json").stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp == self._style_stamp:
            return
        try:
            style = Config.read().overlay_style
        except (OSError, ValueError):
            return                      # mid-write: read it again next tick
        self._style_stamp = stamp
        self.style_buttons.get(style, self.style_buttons["pill"]).setChecked(True)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._follow_style()
        self._style_timer.start(500)
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._style_timer.stop()
        super().hideEvent(event)

    def _defaults(self) -> None:
        self.tune = orb.Tune()
        self._sync_widgets()
        self.status.setText("Back to the shipped look. Save to keep it.")

    def _revert(self) -> None:
        self.tune = orb.Tune.resolve(Config.load().orb_tune)
        self._sync_widgets()
        self.status.setText("Back to what is saved.")


def clock(at: float) -> str:
    return time.strftime("%I:%M %p", time.localtime(at)).lstrip("0")


class EntryRow(QtWidgets.QFrame):
    """One dictation: when, what, and Copy. Long text is shown clipped, so one
    long hands-free take cannot push the rest of the list off the card; Copy
    always takes the whole of it."""

    SHOWN_CHARS = 280

    def __init__(self, entry: Entry, on_copy, on_fix=None) -> None:
        super().__init__()
        self.setObjectName("entry")
        self.entry = entry
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(16, 12, 12, 12)
        row.setSpacing(12)
        col = QtWidgets.QVBoxLayout()
        col.setSpacing(4)
        when = label(clock(entry.at), 12, name="when")
        shown = entry.text
        if len(shown) > self.SHOWN_CHARS:
            shown = shown[:self.SHOWN_CHARS].rstrip() + "…"
        self.text = label(shown, 14, light=True, wrap=True)
        self.text.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(when)
        col.addWidget(self.text)
        row.addLayout(col, 1)
        self.copy = button("Copy", icon="copy")
        self.copy.setAccessibleName("Copy this dictation")
        self.copy.clicked.connect(lambda _=False: on_copy(entry))
        self.fix = button("Fix", icon="fix")
        self.fix.setAccessibleName("Fix a word Shout got wrong")
        self.fix.setToolTip("Select the wrong word first, then Fix")
        # The selection says which word is wrong, so a click leaves focus on
        # the text. Belt and braces: probe_lab's offscreen window could not show
        # a focus-taking button losing it.
        self.fix.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        if on_fix is not None:
            self.fix.clicked.connect(
                lambda _=False: on_fix(self.text.selectedText().strip()))
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addWidget(self.copy)
        buttons.addWidget(self.fix)
        row.addLayout(buttons)
        row.setAlignment(buttons, QtCore.Qt.AlignmentFlag.AlignTop)


class FixBar(QtWidgets.QFrame):
    """Shout wrote X; you meant Y. Saved as a fix, and Y is listened for too
    when it looks like a name. See shout/words.py."""

    def __init__(self, on_done) -> None:
        super().__init__()
        self.setObjectName("entry")
        self.on_done = on_done
        box = QtWidgets.QVBoxLayout(self)
        box.setContentsMargins(16, 12, 12, 12)
        box.setSpacing(10)
        box.addWidget(label("Fix a word. Shout makes this fix from your next "
                            "dictation on.", 13, wrap=True, name="sub"))
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.heard = QtWidgets.QLineEdit()
        self.heard.setPlaceholderText("Shout wrote")
        self.heard.setAccessibleName("What Shout wrote")
        self.write = QtWidgets.QLineEdit()
        self.write.setPlaceholderText("You meant")
        self.write.setAccessibleName("What you meant")
        row.addWidget(self.heard, 1)
        row.addWidget(icon_label("arrow"))
        row.addWidget(self.write, 1)
        self.cancel = button("Cancel", lambda: self.close_bar(""))
        self.save = button("Save fix", self._save, primary=True)
        self.write.returnPressed.connect(self._save)
        self.heard.textChanged.connect(self._sync)
        self.write.textChanged.connect(self._sync)
        row.addWidget(self.cancel)
        row.addWidget(self.save)
        box.addLayout(row)
        self._sync()
        self.hide()

    def open(self, heard: str) -> None:
        self.heard.setText(heard)
        self.write.setText("")
        self.show()
        (self.write if heard else self.heard).setFocus()

    def _sync(self) -> None:
        heard, write = words._clean(self.heard.text()), words._clean(self.write.text())
        self.save.setEnabled(bool(heard and write and heard != write))

    def _save(self) -> None:
        if not self.save.isEnabled():
            return
        heard, write = words._clean(self.heard.text()), words._clean(self.write.text())
        try:
            listened = words.add_fix(heard, write)
        except (OSError, ValueError):
            # The bar stays open with what you typed, so trying again is one click.
            self.on_done("Could not read config.json just now. Try again.")
            return
        self.close_bar(f"Saved. Shout now writes {write!r} where it heard "
                       f"{heard!r}" + (", and listens for it." if listened else "."))

    def close_bar(self, message: str) -> None:
        self.hide()
        self.on_done(message)


class RecentPage(QtWidgets.QWidget):
    """The recent dictations page, newest first."""

    POLL_MS = 250

    def __init__(self, recent: Recent) -> None:
        super().__init__()
        self.recent = recent
        self._version = -1                  # the store version on screen
        self.shown: list[EntryRow] = []
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        self.clear = button("Clear", self._clear)
        self.clear.setToolTip("Forget these dictations now")
        outer.addLayout(page_head(
            "Recent dictations", f"Your last {SIZE} dictations, newest first. Kept "
            "in memory only, and forgotten when Shout quits.", self.clear))

        card = Card(spacing=12)
        body = QtWidgets.QWidget()
        self.rows = QtWidgets.QVBoxLayout(body)
        self.rows.setContentsMargins(0, 0, 4, 0)
        self.rows.setSpacing(8)
        self.empty = label("Nothing yet. Every dictation lands here as well as at "
                           "your cursor.", 13, wrap=True, name="sub")
        self.rows.addWidget(self.empty)
        self.scroll = scroller(body)
        card.box.addWidget(self.scroll, 1)

        self.fix_bar = FixBar(self._fixed)
        card.box.addWidget(self.fix_bar)
        self.status = status_label()
        card.box.addWidget(self.status)
        outer.addWidget(card, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(self.POLL_MS)
        self.refresh()

    def _fix(self, selected: str) -> None:
        self.status.setText("" if selected else
                            "Type what Shout wrote, or select it in the "
                            "dictation before pressing Fix.")
        self.fix_bar.open(selected)

    def _fixed(self, message: str) -> None:
        self.status.setText(message)

    def refresh(self) -> None:
        version = self.recent.version
        if version == self._version:
            return
        # The version is read BEFORE the entries, so a dictation landing in
        # between is on screen a tick early, never a tick late.
        self._version = version
        entries = self.recent.newest_first()
        while self.rows.count() > 1:            # everything but the empty label
            widget = self.rows.takeAt(1).widget()
            if widget is not None:
                # Hidden first: a row out of the layout but not yet deleted
                # still paints where it was.
                widget.hide()
                widget.deleteLater()
        self.shown = [EntryRow(entry, self._copy, self._fix) for entry in entries]
        for row in self.shown:
            self.rows.addWidget(row)
        self.rows.addStretch(1)
        self.empty.setVisible(not entries)
        self.clear.setEnabled(bool(entries))

    def _copy(self, entry: Entry) -> None:
        if set_clipboard_text(entry.text):
            self.status.setText("Copied. Click where it goes and press Ctrl+V.")
        else:
            self.status.setText("Another app is holding the clipboard. Try again.")

    def _clear(self) -> None:
        self.recent.clear()
        self.refresh()
        self.status.setText("Cleared.")


class ListRow(QtWidgets.QFrame):
    """One word you listen for, as a chip with Remove."""

    def __init__(self, text: str, on_remove, what: str) -> None:
        super().__init__()
        self.setObjectName("chip")
        self.setFixedHeight(36)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(14, 0, 4, 0)
        row.setSpacing(2)
        self.label = label(text, 14)
        self.label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.label)
        self.remove = remove_button(what, 28)
        self.remove.clicked.connect(lambda _=False: on_remove())
        row.addWidget(self.remove)


class FixRow(QtWidgets.QWidget):
    """One fix: what Shout heard, what it writes, and Remove."""

    def __init__(self, heard: str, write: str, on_remove, last: bool) -> None:
        super().__init__()
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(4, 6, 0, 6)
        row.setSpacing(10)
        self.label = label(heard, 14, light=True, wrap=True)
        row.addWidget(self.label, 1)
        row.addWidget(icon_label("arrow"))
        self.write = label(write, 14, bold=True, wrap=True)
        row.addWidget(self.write, 1)
        self.remove = remove_button(f"the fix for {heard}")
        self.remove.clicked.connect(lambda _=False: on_remove())
        row.addWidget(self.remove)
        col.addLayout(row)
        if not last:
            col.addWidget(rule())


class WordsPage(QtWidgets.QWidget):
    """Your words: what Shout listens for, and what it always fixes. Reads
    config.json on every refresh, never from a snapshot, because Fix on the
    Recent page writes the same keys."""

    def __init__(self) -> None:
        super().__init__()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        outer.addLayout(page_head("Words", "Names and terms Shout should know. Used "
                                  "from your next dictation, kept in config.json on "
                                  "this PC."))
        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(16)

        listen = Card()
        head = QtWidgets.QHBoxLayout()
        head.addWidget(title("Listen for"))
        head.addStretch(1)
        self.budget = label("", 12, name="sub")
        head.addWidget(self.budget)
        listen.box.addLayout(head)
        self.meter = Meter()
        listen.box.addWidget(self.meter)
        add = QtWidgets.QHBoxLayout()
        add.setSpacing(10)
        self.word = QtWidgets.QLineEdit()
        self.word.setPlaceholderText("A name, a set, a product")
        self.word.setAccessibleName("Word to listen for")
        self.word.returnPressed.connect(self._add_word)
        self.add_word = button("Add", self._add_word, primary=True)
        self.add_word.setAccessibleName("Add this word")
        add.addWidget(self.word, 1)
        add.addWidget(self.add_word)
        listen.box.addLayout(add)
        chips = QtWidgets.QWidget()
        self.word_rows = FlowLayout(8)
        chips.setLayout(self.word_rows)
        self.words_empty = label("None yet. Add the names Shout gets wrong: cards, "
                                 "sets, people, products.", 13, wrap=True, name="sub")
        listen.box.addWidget(self.words_empty)
        listen.box.addWidget(scroller(chips, 90), 1)
        listen.box.addWidget(label("Newest first. A fix with a capital letter, saved "
                                   "from Recent, is added here too.", 12, wrap=True,
                                   name="sub"))
        cards.addWidget(listen, 1)

        fixes = Card()
        head = QtWidgets.QHBoxLayout()
        head.addWidget(title("Always replace"))
        head.addStretch(1)
        self.fix_count = label("", 12, name="sub")
        head.addWidget(self.fix_count)
        fixes.box.addLayout(head)
        fixes.box.addSpacing(2)
        add = QtWidgets.QHBoxLayout()
        add.setSpacing(8)
        self.heard = QtWidgets.QLineEdit()
        self.heard.setPlaceholderText("Shout writes")
        self.heard.setAccessibleName("What Shout writes")
        self.write = QtWidgets.QLineEdit()
        self.write.setPlaceholderText("You meant")
        self.write.setAccessibleName("What you meant")
        self.write.returnPressed.connect(self._add_fix)
        self.add_fix = button("Add", self._add_fix, primary=True)
        self.add_fix.setAccessibleName("Add this fix")
        add.addWidget(self.heard, 1)
        add.addWidget(icon_label("arrow"))
        add.addWidget(self.write, 1)
        add.addWidget(self.add_fix)
        fixes.box.addLayout(add)
        body = QtWidgets.QWidget()
        self.fix_rows = QtWidgets.QVBoxLayout(body)
        self.fix_rows.setContentsMargins(0, 0, 4, 0)
        self.fix_rows.setSpacing(0)
        self.fixes_empty = label("None yet. Use Fix on a recent dictation, or add one "
                                 "here.", 13, wrap=True, name="sub")
        fixes.box.addWidget(self.fixes_empty)
        fixes.box.addWidget(scroller(body, 90), 1)
        fixes.box.addWidget(label("Whole words only. Every Fix you save on Recent "
                                  "lands here.", 12, wrap=True, name="sub"))
        cards.addWidget(fixes, 1)
        outer.addLayout(cards, 1)

        self.status = status_label()
        outer.addWidget(self.status)
        self.word_rows_shown: list[ListRow] = []
        self.fix_rows_shown: list[FixRow] = []
        self.refresh()

    @staticmethod
    def _empty(rows: QtWidgets.QLayout) -> None:
        while rows.count():
            item = rows.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()

    def refresh(self) -> None:
        try:
            listed, reps = words.stored()
        except (OSError, ValueError):
            self.status.setText("Could not read config.json just now. Reopen the "
                                "window to try again.")
            return
        vocab = words.Vocab.resolve(listed, reps)
        heard = set(vocab.heard())
        hint = vocab.hint() or ""
        self.budget.setText(f"{len(hint)} of {words.HINT_BUDGET} characters")
        self.meter.set_fraction(len(hint) / words.HINT_BUDGET)
        self._empty(self.word_rows)
        self.word_rows_shown = [
            ListRow(w if w in heard else f"{w}  (no room: not listened for)",
                    lambda w=w: self._remove_word(w), f"the word {w}")
            for w in reversed(vocab.words)]
        for row in self.word_rows_shown:
            self.word_rows.addWidget(row)
        self.words_empty.setVisible(not self.word_rows_shown)
        self._empty(self.fix_rows)
        pairs = list(reversed(vocab.fixes))
        self.fix_rows_shown = [
            FixRow(h, w, lambda h=h: self._remove_fix(h), i == len(pairs) - 1)
            for i, (h, w) in enumerate(pairs)]
        for row in self.fix_rows_shown:
            self.fix_rows.addWidget(row)
        self.fix_rows.addStretch(1)
        self.fixes_empty.setVisible(not pairs)
        self.fix_count.setText(f"{len(pairs)} fix{'' if len(pairs) == 1 else 'es'}")

    def _act(self, change, done: str) -> bool:
        try:
            change()
        except (OSError, ValueError):
            self.status.setText("Could not read config.json just now. Try again.")
            return False
        self.status.setText(done)
        self.refresh()
        return True

    def _add_word(self) -> None:
        word = words._clean(self.word.text())
        if not word:
            return
        if self._act(lambda: words.add_word(word), f"Listening for {word!r}."):
            self.word.clear()

    def _remove_word(self, word: str) -> None:
        self._act(lambda: words.remove_word(word), f"Removed {word!r}.")

    def _add_fix(self) -> None:
        heard, write = words._clean(self.heard.text()), words._clean(self.write.text())
        if not heard or not write or heard == write:
            self.status.setText("Fill in both sides: what Shout writes, and what "
                                "you meant.")
            return
        if self._act(lambda: words.add_fix(heard, write),
                     f"Shout now writes {write!r} where it hears {heard!r}."):
            self.heard.clear()
            self.write.clear()

    def _remove_fix(self, heard: str) -> None:
        self._act(lambda: words.remove_fix(heard), f"Removed the fix for {heard!r}.")


# -- today, from shout.log ---------------------------------------------------
# shout.log holds timings and counts, never words. One END-TO-END line per
# dictation that reached a paste; the `transcribed` line before it carries the
# word count (lines written before that count existed carry none). A warmup or
# an empty transcript logs `transcribed` with no END-TO-END after it, so a
# count only lands when its END-TO-END does.
END_TO_END = re.compile(r"END-TO-END (\d+)ms")
TRANSCRIBED = re.compile(r"transcribed .* -> \d+ chars(?:, (\d+) words)?")


@dataclass
class DayStats:
    dictations: int = 0
    latencies: list = field(default_factory=list)   # ms, oldest first
    words: int = 0
    counted: int = 0            # dictations whose log line carried a word count


def _from_day(path: Path, prefix: str) -> tuple[str, bool]:
    """The part of one log file written on the day `prefix` names, and whether
    the whole file is from that day. Lines are in time order, so it is
    everything from the first line of that day on."""
    try:
        data = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False
    if data.startswith(prefix):
        return data, True
    i = data.find("\n" + prefix)
    return (data[i + 1:] if i >= 0 else ""), False


def today_stats(folder: Path | None = None, day: date | None = None) -> DayStats:
    folder = folder or config_dir()
    prefix = (day or date.today()).isoformat()
    text, whole = _from_day(folder / "shout.log", prefix)
    # The log rotates at 1MB. Only when the current file STARTS today can the
    # previous one hold some of today too.
    if whole:
        head, _ = _from_day(folder / "shout.log.1", prefix)
        if head:
            text = head + ("" if head.endswith("\n") else "\n") + text
    stats = DayStats()
    pending: int | None = None
    for line in text.splitlines():
        m = TRANSCRIBED.search(line)
        if m:
            pending = int(m.group(1)) if m.group(1) is not None else None
            continue
        m = END_TO_END.search(line)
        if m:
            stats.dictations += 1
            stats.latencies.append(int(m.group(1)))
            if pending is not None:
                stats.words += pending
                stats.counted += 1
            pending = None
    return stats


class OrbHero(QtWidgets.QWidget):
    """The home page's orb: the overlay's renderer, magnified so its ring is
    RING px across, with the model's state in the middle. The resting ring is
    the holding water with no voice; loading shows the transcribing comet.
    Animates only while visible, at about 30 frames a second."""

    RING = 226
    STATES = {"ready": "Ready", "loading": "Loading", "error": "Error"}

    def __init__(self, model_line: str) -> None:
        super().__init__()
        self.setMinimumSize(290, 290)
        self.model_line = model_line
        self.state = "ready"
        self.motion = orb.Choreography(orb.Tune.resolve(Config.load().orb_tune))
        self.frame: QtGui.QImage | None = None
        self.drawn = 0
        try:
            self.renderer: orb.Renderer | None = orb.Renderer()
        except Exception:
            self.renderer = None
        self._last = time.perf_counter()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAccessibleName("Shout's state")

    def set_state(self, state: str) -> None:
        state = state if state in self.STATES else "ready"
        if state != self.state:
            self.state = state
            self.setAccessibleDescription(self.STATES[state])
            self.update()

    def zoom(self) -> float:
        return self.RING / (self.motion.tune.size * orb.RING)

    def _tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last, 0.1)
        self._last = now
        self.motion.step(dt, "working" if self.state == "loading" else "recording", 0.0)
        if self.renderer is not None:
            dpr, tune, zoom = self.devicePixelRatioF(), self.motion.tune, self.zoom()
            try:
                # No supersampling: at this magnification the edge is already
                # smooth, and it keeps the read-back a quarter of the size.
                image = self.renderer.render(
                    self.motion.uniforms(1.0, dpr * zoom / orb.SUPERSAMPLE),
                    round(tune.win * dpr * zoom))
                image.setDevicePixelRatio(dpr)
                self.frame = image
                self.drawn += 1
            except Exception:
                self.close_renderer()
        self.update()

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        c = QtCore.QPointF(self.width() / 2.0, self.height() / 2.0)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 72), 1.0))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, 144.5, 144.5)
        if self.frame is not None:
            tune, zoom = self.motion.tune, self.zoom()
            # The shader puts the orb's centre where the overlay's window puts
            # it, bottom-anchored; move that point to the middle of this widget.
            full = tune.size / 2.0 * orb.RING
            cy = tune.win - tune.bottom_pad - full
            p.drawImage(QtCore.QPointF(c.x() - tune.win / 2.0 * zoom, c.y() - cy * zoom),
                        self.frame)
        else:
            # No OpenGL: the mock-up's ring, painted.
            grad = QtGui.QConicalGradient(c, 90)
            for at, colour in ((0.0, "#c2b0ff"), (0.25, "#a9dcff"), (0.5, "#a7e6f2"),
                               (0.75, "#b6b8ff"), (1.0, "#c2b0ff")):
                grad.setColorAt(at, QtGui.QColor(colour))
            p.setPen(QtGui.QPen(QtGui.QBrush(grad), 2.0))
            p.drawEllipse(c, self.RING / 2.0, self.RING / 2.0)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 77), 1.0))
        p.setBrush(QtGui.QColor(255, 255, 255, 26))
        p.drawEllipse(c, 89, 89)
        p.setPen(QtGui.QColor(C["text"]))
        p.setFont(font(40, display=True))
        p.drawText(QtCore.QRectF(c.x() - 88, c.y() - 34, 176, 48),
                   int(QtCore.Qt.AlignmentFlag.AlignCenter), self.STATES[self.state])
        p.setFont(font(13))
        p.drawText(QtCore.QRectF(c.x() - 88, c.y() + 14, 176, 22),
                   int(QtCore.Qt.AlignmentFlag.AlignCenter), self.model_line)
        p.end()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self._last = time.perf_counter()
        self._timer.start(33)
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def close_renderer(self) -> None:
        self._timer.stop()
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
        self.update()


class HomePage(QtWidgets.QWidget):
    """What the window opens on. Refreshes twice a second while it shows, and
    reads shout.log only when the file has changed."""

    POLL_MS = 500
    LAST_CHARS = 150

    def __init__(self, recent: Recent, model_state, go) -> None:
        super().__init__()
        self.recent = recent
        self.model_state = model_state
        self.go = go
        self._stamp = None
        self._config_stamp = None
        self._version = -1
        self.stats = DayStats()
        cfg = Config.load()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)

        private = QtWidgets.QFrame()
        private.setObjectName("chip")
        chip = QtWidgets.QHBoxLayout(private)
        chip.setContentsMargins(14, 7, 14, 7)
        chip.setSpacing(8)
        chip.addWidget(icon_label("lock"))
        chip.addWidget(label("On this PC only", 12))
        private.setToolTip("Nothing you say leaves this PC, and your words are "
                           "never written to disk.")
        outer.addLayout(page_head("Home", right=private))

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(20)
        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        device = "GPU" if cfg.device == "cuda" else cfg.device.upper()
        self.hero = OrbHero(f"{cfg.model} · {device}")
        left.addWidget(self.hero, 1)
        hint = label("Hold Ctrl+Win to talk · double-tap for hands-free", 13,
                     wrap=True)
        hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        left.addWidget(hint)
        body.addLayout(left, 0)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(14)
        grid.addWidget(self._today_card(), 0, 0)
        grid.addWidget(self._speed_card(), 0, 1)
        grid.addWidget(self._words_card(), 1, 0)
        grid.addWidget(self._last_card(), 1, 1)
        for i in (0, 1):
            grid.setColumnStretch(i, 1)
            grid.setRowStretch(i, 1)
        body.addLayout(grid, 1)
        outer.addLayout(body, 1)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    # -- cards ---------------------------------------------------------------

    @staticmethod
    def _card() -> HoverCard:
        return HoverCard((20, 18, 20, 18), 6)

    def _today_card(self) -> Card:
        card = self._card()
        card.box.addWidget(section("Today"))
        card.box.addSpacing(6)
        self.today_count = label("0", 46, display=True)
        self.today_unit = label("dictations", 13)
        self.today_empty = label("No dictations yet today", 15, light=True, wrap=True)
        card.box.addWidget(self.today_count)
        card.box.addWidget(self.today_unit)
        card.box.addWidget(self.today_empty)
        card.box.addStretch(1)
        card.box.addWidget(rule())
        self.today_words = label("", 13, wrap=True)
        card.box.addWidget(self.today_words)
        return card

    def _speed_card(self) -> Card:
        card = self._card()
        card.box.addWidget(section("Speed"))
        card.box.addSpacing(6)
        self.speed = label("", 46, display=True)
        self.speed.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.speed_unit = label("release to text, typical", 13, wrap=True)
        self.speed_empty = label("No dictations yet today", 15, light=True, wrap=True)
        card.box.addWidget(self.speed)
        card.box.addWidget(self.speed_unit)
        card.box.addWidget(self.speed_empty)
        card.box.addStretch(1)
        self.spark = Sparkline()
        card.box.addWidget(self.spark)
        return card

    def _words_card(self) -> Card:
        card = self._card()
        card.box.addWidget(section("Your words"))
        card.box.addSpacing(6)
        self.names = label("", 34, display=True)
        self.names_detail = label("", 13, wrap=True)
        card.box.addWidget(self.names)
        card.box.addWidget(self.names_detail)
        card.box.addStretch(1)
        self.add_word = button("Add a word", self._go_add_word, primary=True)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.add_word)
        row.addStretch(1)
        card.box.addLayout(row)
        return card

    def _last_card(self) -> Card:
        card = self._card()
        head = QtWidgets.QHBoxLayout()
        head.addWidget(section("Last dictation"))
        head.addStretch(1)
        self.last_when = label("", 12)
        head.addWidget(self.last_when)
        card.box.addLayout(head)
        self.last_text = label("", 14, light=True, wrap=True)
        self.last_text.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        card.box.addWidget(self.last_text)
        card.box.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(8)
        self.copy = button("Copy", self._copy, icon="copy")
        self.copy.setAccessibleName("Copy your last dictation")
        self.fix = button("Fix", self._go_fix, icon="fix")
        self.fix.setAccessibleName("Fix a word in your last dictation")
        row.addWidget(self.copy)
        row.addWidget(self.fix)
        row.addStretch(1)
        card.box.addLayout(row)
        return card

    # -- refresh -------------------------------------------------------------

    def refresh(self) -> None:
        try:
            self.hero.set_state(str(self.model_state()).lower())
        except Exception:
            self.hero.set_state("error")
        self._refresh_stats()
        self._refresh_words()
        self._refresh_last()

    def _refresh_stats(self) -> None:
        folder = config_dir()
        stamp = [date.today()]
        for name in ("shout.log", "shout.log.1"):
            try:
                st = (folder / name).stat()
                stamp.append((st.st_mtime_ns, st.st_size))
            except OSError:
                stamp.append(None)
        if stamp == self._stamp:
            return
        self._stamp = stamp
        self.stats = s = today_stats(folder)
        some = s.dictations > 0
        for w in (self.today_count, self.today_unit, self.speed, self.speed_unit,
                  self.spark):
            w.setVisible(some)
        self.today_empty.setVisible(not some)
        self.speed_empty.setVisible(not some)
        self.today_words.setVisible(some)
        if not some:
            return
        self.today_count.setText(f"{s.dictations:,}")
        self.today_unit.setText("dictation" if s.dictations == 1 else "dictations")
        if s.counted:
            self.today_words.setText(f"{s.words:,} word{'' if s.words == 1 else 's'}"
                                     + ("" if s.counted == s.dictations else
                                        f" in the last {s.counted}"))
        else:
            self.today_words.setText("Words are counted from your next dictation")
        median = statistics.median(s.latencies) / 1000.0
        self.speed.setText(f"{median:.1f}<span style='font-size:22px'> s</span>"
                           if median < 10 else
                           f"{median:.0f}<span style='font-size:22px'> s</span>")
        self.spark.set_values(s.latencies[-20:])

    def _refresh_words(self) -> None:
        try:
            stamp = (config_dir() / "config.json").stat().st_mtime_ns
        except OSError:
            stamp = 0
        if stamp == self._config_stamp:
            return
        try:
            listed, reps = words.stored()
        except (OSError, ValueError):
            return                      # mid-write: next tick
        self._config_stamp = stamp
        vocab = words.Vocab.resolve(listed, reps)
        n, f = len(vocab.words), len(vocab.fixes)
        self.names.setText(f"{n} name{'' if n == 1 else 's'}")
        self.names_detail.setText(
            f"{f} fix{'' if f == 1 else 'es'} · {len(vocab.hint() or '')} of "
            f"{words.HINT_BUDGET} characters")

    def _refresh_last(self) -> None:
        version = self.recent.version
        if version == self._version:
            return
        self._version = version
        entry = self.recent.last()
        if entry is None:
            self.last_when.setText("")
            self.last_text.setText("Nothing yet. Your next dictation shows here, "
                                   "and nowhere on disk.")
        else:
            shown = entry.text
            if len(shown) > self.LAST_CHARS:
                shown = shown[:self.LAST_CHARS].rstrip() + "…"
            self.last_when.setText(clock(entry.at))
            self.last_text.setText(shown)
        self.copy.setEnabled(entry is not None)
        self.fix.setEnabled(entry is not None)

    # -- actions -------------------------------------------------------------

    def _copy(self) -> None:
        entry = self.recent.last()
        if entry is None:
            return
        ok = set_clipboard_text(entry.text)
        self.copy.setText("Copied" if ok else "Try again")
        QtCore.QTimer.singleShot(1500, lambda: self.copy.setText("Copy"))

    def _go_add_word(self) -> None:
        self.go("add_word")

    def _go_fix(self) -> None:
        self.go("fix_last")

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        self.refresh()
        self._timer.start(self.POLL_MS)
        super().showEvent(event)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)


# -- settings ----------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / ".venv" / "Scripts" / "shoutw.exe"
LANGUAGES = [("en", "English"), (None, "Detect each time"), ("es", "Spanish"),
             ("fr", "French"), ("de", "German"), ("it", "Italian"),
             ("pt", "Portuguese"), ("nl", "Dutch"), ("ja", "Japanese"),
             ("zh", "Chinese")]
RESTART = "Restart Shout to apply."


def startup_link() -> Path:
    """Where scripts/install_shortcuts.ps1 -Autostart puts its shortcut."""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / \
        "Startup" / "Shout.lnk"


# The same shortcut install_shortcuts.ps1 makes, through the same COM object.
# Paths go in as environment variables, never spliced into the script: a
# Windows user name can contain an apostrophe.
_MAKE_LINK = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SHOUT_LNK); "
    "$s.TargetPath = $env:SHOUT_TARGET; $s.Arguments = '-m shout'; "
    "$s.WorkingDirectory = $env:SHOUT_ROOT; $s.IconLocation = $env:SHOUT_ICO; "
    "$s.Description = 'Shout - local dictation. Hold Ctrl+Win to talk.'; $s.Save(); "
    "(New-Object -ComObject WScript.Shell).CreateShortcut($env:SHOUT_LNK).TargetPath")


def set_autostart(on: bool) -> Path | None:
    """Make or remove the Startup shortcut, for this user, without admin.
    Returns the target the saved shortcut points at, read back from the file.
    Raises if the launcher is not installed or the shortcut cannot be made."""
    link = startup_link()
    if not on:
        link.unlink(missing_ok=True)
        return None
    if not LAUNCHER.exists():
        raise FileNotFoundError(f"{LAUNCHER} is missing")
    link.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ | {"SHOUT_LNK": str(link), "SHOUT_TARGET": str(LAUNCHER),
                        "SHOUT_ROOT": str(ROOT),
                        "SHOUT_ICO": str(ROOT / "assets" / "shout.ico")}
    done = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _MAKE_LINK],
        env=env, capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    target = done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""
    if done.returncode != 0 or not link.exists() or \
            Path(target).resolve() != LAUNCHER.resolve():
        link.unlink(missing_ok=True)
        raise OSError(f"the shortcut could not be made ({done.stderr.strip()[:120]})")
    return Path(target)


def input_choices() -> list[tuple[int | None, str]]:
    """Follow the Windows default, then each microphone PortAudio's MME list
    has. MME, because that is the list `input_device` indexes; the Sound Mapper
    is the first entry and is what following the default already opens."""
    out: list[tuple[int | None, str]] = [(None, "Follow the Windows default "
                                               "(recommended)")]
    try:
        mapper = follow_default("input")
        for api in sd.query_hostapis():
            if api["name"] != "MME":
                continue
            for index in api["devices"]:
                info = sd.query_devices(index)
                if info["max_input_channels"] > 0 and index != mapper:
                    out.append((index, info["name"]))
    except Exception:
        pass
    return out


class SettingsPage(QtWidgets.QWidget):
    """Each control writes its own key and nothing else, re-reading the file
    for every write. Start with Windows is a shortcut, not a key."""

    def __init__(self) -> None:
        super().__init__()
        self._loading = True
        cfg = Config.load()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(14)
        outer.addLayout(page_head("Settings", "Kept in config.json on this PC. A "
                                  "setting that needs a restart says so."))
        cards = QtWidgets.QHBoxLayout()
        cards.setSpacing(16)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(16)
        dictation = Card()
        dictation.box.addWidget(title("Dictation"))
        self.language = Combo()
        self.language.setAccessibleName("Language")
        for code, name in LANGUAGES:
            self.language.addItem(name, code)
        i = self.language.findData(cfg.language)
        if i < 0:
            self.language.addItem(f"Other: {cfg.language}", cfg.language)
            i = self.language.count() - 1
        self.language.setCurrentIndex(i)
        self.language.currentIndexChanged.connect(
            lambda _i: self._write("language", self.language.currentData()))
        self._field(dictation, "Language", "The language you speak. Detecting it "
                    "each time is slower and can guess wrong on short dictations. "
                    + RESTART, self.language)
        self.mic = Combo()
        self.mic.setAccessibleName("Microphone")
        for index, name in input_choices():
            self.mic.addItem(name, index)
        i = self.mic.findData(cfg.input_device)
        if i < 0:
            self.mic.addItem(f"Device {cfg.input_device} (not found)", cfg.input_device)
            i = self.mic.count() - 1
        self.mic.setCurrentIndex(i)
        self.mic.currentIndexChanged.connect(
            lambda _i: self._write("input_device", self.mic.currentData()))
        self._field(dictation, "Microphone", "Following the default moves with you "
                    "when you switch mics in Windows. A named one is saved by its "
                    "number, which Windows can renumber. " + RESTART, self.mic)
        dictation.box.addStretch(1)
        left.addWidget(dictation, 1)
        cards.addLayout(left, 1)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(16)
        feedback = Card()
        feedback.box.addWidget(title("Feedback"))
        self.overlay = Pill("Show the pill or orb")
        self.overlay.setChecked(bool(cfg.overlay))
        self.overlay.toggled.connect(lambda on: self._write("overlay", on))
        feedback.box.addWidget(self.overlay)
        feedback.box.addWidget(label("Above the taskbar while Shout listens and "
                                     "transcribes. " + RESTART, 12, wrap=True,
                                     name="sub"))
        self.cues = Pill("Cue sounds")
        self.cues.setChecked(bool(cfg.cues))
        self.cues.toggled.connect(lambda on: self._write("cues", on))
        feedback.box.addWidget(self.cues)
        feedback.box.addWidget(label("Short tones when a dictation starts and "
                                     "stops. " + RESTART, 12, wrap=True, name="sub"))
        volume = QtWidgets.QHBoxLayout()
        volume.setSpacing(12)
        volume.addWidget(label("Cue volume", 13))
        self.volume = Slider()
        self.volume.setRange(0, 100)
        self.volume.setAccessibleName("Cue volume")
        self.volume.setValue(round(float(cfg.cue_volume) * 100))
        self.volume_value = value_label(40)
        self.volume_value.setText(f"{self.volume.value()}%")
        self.volume.valueChanged.connect(self._on_volume)
        self.volume.settled.connect(self._save_volume)
        volume.addWidget(self.volume, 1)
        volume.addWidget(self.volume_value)
        feedback.box.addLayout(volume)
        feedback.box.addWidget(label("Applies from the next cue, no restart.", 12,
                                     wrap=True, name="sub"))
        right.addWidget(feedback)

        startup = Card()
        startup.box.addWidget(title("Startup"))
        self.autostart = Pill("Start Shout with Windows")
        self.autostart.setChecked(startup_link().exists())
        self.autostart.toggled.connect(self._on_autostart)
        startup.box.addWidget(self.autostart)
        startup.box.addWidget(label("A shortcut in your Startup folder, for you "
                                    "only, no admin needed. From your next "
                                    "sign-in.", 12, wrap=True, name="sub"))
        right.addWidget(startup)
        right.addStretch(1)
        cards.addLayout(right, 1)
        outer.addLayout(cards, 1)

        self.status = status_label()
        outer.addWidget(self.status)
        self._loading = False

    @staticmethod
    def _field(card: Card, heading: str, detail: str,
               control: QtWidgets.QWidget) -> None:
        card.box.addWidget(label(heading, 14))
        card.box.addWidget(control)
        card.box.addWidget(label(detail, 12, wrap=True, name="sub"))
        card.box.addSpacing(4)

    def _write(self, key: str, value) -> None:
        if self._loading:
            return
        try:
            merge({key: value})
        except OSError:
            self.status.setText("Could not write config.json just now. Try again.")
            return
        live = key == "cue_volume"
        self.status.setText("Saved. " + ("Shout uses it from the next cue." if live
                                         else RESTART))

    def _on_volume(self, value: int) -> None:
        self.volume_value.setText(f"{value}%")
        if not self.volume.held:            # keys: every step; a drag: on release
            self._save_volume()

    def _save_volume(self) -> None:
        self._write("cue_volume", round(self.volume.value() / 100.0, 3))

    def _on_autostart(self, on: bool) -> None:
        if self._loading:
            return
        try:
            target = set_autostart(on)
        except (OSError, subprocess.SubprocessError) as e:
            self._loading = True
            self.autostart.setChecked(not on)
            self._loading = False
            self.status.setText(
                "Shout's launcher is not installed. Run scripts\\install_shortcuts.ps1 "
                "once, then try again." if isinstance(e, FileNotFoundError)
                else f"Could not change it: {e}")
            return
        self.status.setText(f"Shout starts with Windows, through {target.name}."
                            if on else "Shout no longer starts with Windows.")


# -- the window --------------------------------------------------------------

NAV = (("home", "Home"), ("recent", "Recent dictations"), ("words", "Words"),
       ("sounds", "Cue sounds"), ("orb", "Look"), ("settings", "Settings"))


class Window(QtWidgets.QWidget):
    """Six pages, picked from the rail on the left. Shout builds a fresh one
    each time it is opened after being closed, so every page reads config.json
    as it is now, not as it was the first time. `model_state` is a zero-argument
    callable returning "ready", "loading" or "error"; the gates pass none."""

    PAGES = tuple(key for key, _ in NAV)

    def __init__(self, recent: Recent, page: str = "home", model_state=None) -> None:
        super().__init__()
        self.setWindowTitle("Shout")
        self.setStyleSheet(QSS)
        self.setFont(font(13))
        # QSS has no placeholder colour; the palette carries it to every field.
        palette = self.palette()
        palette.setColor(QtGui.QPalette.ColorRole.PlaceholderText,
                         QtGui.QColor(C["placeholder"]))
        self.setPalette(palette)
        root =QtWidgets.QHBoxLayout(self)
        root.setContentsMargins(18, 18, 28, 22)
        root.setSpacing(22)

        rail = QtWidgets.QFrame()
        rail.setObjectName("navbar")
        nav = QtWidgets.QVBoxLayout(rail)
        nav.setContentsMargins(8, 8, 8, 8)
        nav.setSpacing(6)
        group = QtWidgets.QButtonGroup(self)
        self.tabs: dict[str, QtWidgets.QPushButton] = {}
        for key, text in NAV:
            tab = QtWidgets.QPushButton()
            tab.setObjectName("nav")
            tab.setCheckable(True)
            tab.setIcon(svg_icon(key, 19, checked_colour=C["ink"]))
            tab.setIconSize(QtCore.QSize(19, 19))
            # No tooltip: the NavLabel names it, at once and in this window's style.
            tab.setAccessibleName(text)
            tab.installEventFilter(self)
            # Keyboard focus only: a clicked tab that keeps a focus ring reads
            # as a second highlight laid over the checked one.
            tab.setFocusPolicy(QtCore.Qt.FocusPolicy.TabFocus)
            tab.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            tab.clicked.connect(lambda _=False, k=key: self.show_page(k))
            group.addButton(tab)
            nav.addWidget(tab)
            self.tabs[key] = tab
        self.rail = rail
        self.nav_label = NavLabel(self)
        column = QtWidgets.QVBoxLayout()
        column.addStretch(1)
        column.addWidget(rail)
        column.addStretch(1)
        root.addLayout(column)

        self.recent_page = RecentPage(recent)
        self.words_page = WordsPage()
        self.lab = Lab()
        self.orb_page = OrbPage()
        self.settings_page = SettingsPage()
        self.home = HomePage(recent, model_state or (lambda: "ready"), self._go)
        self.pages = {"home": self.home, "recent": self.recent_page,
                      "words": self.words_page, "sounds": self.lab,
                      "orb": self.orb_page, "settings": self.settings_page}
        self.stack = QtWidgets.QStackedWidget()
        for key in self.PAGES:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)
        self.show_page(page if page in self.pages else "home")

        # Sized FROM the layout's own minimum, not from a number that looked
        # right: at 736 every label was rendering 4-5px under its minimumSizeHint
        # -- legible at this DPI, clipped at another. Qt enforces the layout
        # minimum on its own once nothing overrides it. The stack takes its
        # tallest page, so switching pages never resizes it.
        hint = self.sizeHint()
        self.resize(max(940, hint.width()), hint.height())

    def eventFilter(self, obj: QtCore.QObject, event: QtCore.QEvent) -> bool:
        """The rail's tabs: name the one under the pointer or with focus."""
        kind = event.type()
        if kind in (QtCore.QEvent.Type.Enter, QtCore.QEvent.Type.FocusIn):
            self.nav_label.show_for(obj, self.rail)
        elif kind in (QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.FocusOut):
            self.nav_label.hide_for(obj)
        return False

    def show_page(self, key: str) -> None:
        if key == "words":
            self.words_page.refresh()     # Fix on the Recent page writes it too
        self.stack.setCurrentWidget(self.pages[key])
        self.tabs[key].setChecked(True)

    def _go(self, where: str) -> None:
        """The home page's ways onward: Add a word, and Fix on the last
        dictation, which opens the Recent page's fix bar with that dictation
        at the top of the list."""
        if where == "add_word":
            self.show_page("words")
            self.words_page.word.setFocus()
        elif where == "fix_last":
            self.show_page("recent")
            self.recent_page.refresh()
            self.recent_page.scroll.verticalScrollBar().setValue(0)
            self.recent_page._fix("")
        else:
            self.show_page(where)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        # The title bar in the wash's colour, dark mode for its buttons. Windows
        # 11 only; anything older keeps its own title bar.
        try:
            hwnd = int(self.winId())
            dwm = ctypes.windll.dwmapi
            dark = ctypes.c_int(1)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), 4)
            r, g, b = (int(C["wash_a"][i:i + 2], 16) for i in (1, 3, 5))
            colour = ctypes.c_uint(r | g << 8 | b << 16)
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colour), 4)
        except Exception:
            pass

    def paintEvent(self, _e: QtGui.QPaintEvent) -> None:
        """The wash: slate-lavender to dusty rose, corner to corner. Painted
        rather than a flat colour because the cards above it are translucent,
        and its variation under them is the only frost Qt can give them."""
        p = QtGui.QPainter(self)
        grad = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0.0, QtGui.QColor(C["wash_a"]))
        grad.setColorAt(0.48, QtGui.QColor(C["wash_b"]))
        grad.setColorAt(1.0, QtGui.QColor(C["wash_c"]))
        p.fillRect(self.rect(), QtGui.QBrush(grad))
        p.end()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # The sounds page holds an output stream for as long as it exists, and
        # closing the window no longer ends a process that would release it.
        self.lab.cues.close()
        # And the orb page and the home orb an OpenGL context each.
        self.orb_page.preview.close_renderer()
        self.home.hero.close_renderer()
        super().closeEvent(event)


def application(argv: list[str]) -> QtWidgets.QApplication:
    """The QApplication as Shout runs it, and as the gates build it too. The
    style decides how a slider takes a click: Windows 11's jumps to it, Fusion's
    steps toward it. So a gate left on the default style passed a jump the lab
    never made. App-wide, now that this window shares Shout's process, which
    means the tray menu is drawn by Fusion as well."""
    app = QtWidgets.QApplication(argv)
    app.setStyle("Fusion")
    return app
