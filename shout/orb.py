"""The orb: the overlay's second look, drawn by a fragment shader.

The shader started as the voice-powered orb React component (itself the
ReactBits orb): simplex noise wanders the ring's radius, a highlight orbits it,
and the colours sweep round. Measured 17 Sep 2026 before any of this was
written: the original GLSL compiled verbatim in Qt's OpenGL on the RTX 5070, and
a frame at overlay size costs 0.05ms including the copy back to the CPU.

What changed from the component, and why:

    no microphone of its own    Shout already has the level; the component
                                opened a second capture stream to get it
    a resting capsule           the orb only reads as an orb at ~56px and up,
                                far too loud to leave on screen all day, so at
                                rest it squashes into a small glass capsule whose
                                rim colour circles slowly, and it inflates when
                                something happens
    two kinds of water          noise bumps made holding and hands-free look
                                alike. Holding is an open swell; hands-free is
                                the same swell with ripples riding on it, which
                                reads as a different sea rather than as more
                                spikes. Each turns its own way, and your voice
                                raises and roughens both
    a voice curve               the component multiplied two small level terms,
                                so ordinary speech away from the mic barely moved
                                it
    a comet while transcribing  a dim ring with one bright head travelling round
                                it, so it cannot be mistaken for a quiet recording
    the field turns, the        rotating the whole shape would tip the capsule
    shape does not              over mid-morph

Everything you would want to adjust by eye is a field of `Tune`, which the Orb
page of the Shout window edits and saves as `orb_tune` in config.json. The
window's size is derived from it, so no tune can push the glow off its edge.

It renders offscreen and hands back a QImage, which the overlay paints like any
other image. The overlay window stays a plain translucent widget, so none of its
measured Win32 behaviour (no activation, click-through, topmost) depends on how
Qt composites OpenGL into a layered window.
"""
from __future__ import annotations

import array
import math
from dataclasses import asdict, dataclass, fields, replace

from PySide6 import QtCore, QtGui, QtOpenGL

RING = 0.8              # where the ring sits inside the glow, in shader units
SUPERSAMPLE = 2         # rendered at 2x and scaled down: the capsule is 10px tall
# Turbulence at full voice, and how much of it the voice adds. Water gets
# rougher when energy goes into it, not spikier: most of what you hear shows up
# as finer waves rather than taller ones, which is what keeps a loud orb from
# blooming into a star.
TURB_MAX = 1.4
ROUGH = 0.45
# The tallest a crest can stand, in units of its nominal height. Both waves
# divide by the sum of their own terms, so each peaks at exactly 1 when they all
# line up, and a state's two weights never sum past 1 (they ease as a pair).
# That is what makes `height` mean the tip height in px rather than 1.23x it.
PEAK = 1.0
WARP = 0.02             # the voice ripple, in shader units at full voice
# Voice. The overlay's meter is 0-1 over -52..-12 dBFS; below the gate is room
# noise, and the curve lifts the quiet end, where speech from arm's length sits.
# It was a square root until 17 Sep. Flattening the crests took the loud end
# down by a quarter, which would have taken ordinary speech down with it -- the
# curve is what decides how much of the response lands at arm's length rather
# than at a shout, so 0.40 buys the flattening back where it is actually read.
# Measured on the render at meter 0.15: the wave grows 0.68 -> 1.04px, against
# 0.86 -> 1.31px before, so the same 1.5x on a ring a fifth shorter.
VOICE_GATE = 0.04
VOICE_CURVE = 0.40


def _rpm(rad_per_s: float) -> float:
    return rad_per_s * 60.0 / math.tau


@dataclass(frozen=True)
class Tune:
    """What the Orb page adjusts. The defaults are the shipped look.

    Sizes are logical px and turns are revolutions per minute, so each knob does
    one thing: a bigger orb keeps its bumps the same height, and more bumps do
    not turn any faster. A negative spin turns the other way."""

    size: float = 72.0                  # the expanded orb, glow edge to glow edge
    hold_crests: int = 9                # while holding
    hold_turb: float = 0.8              # 0 is one even swell; 1 is choppy water
    hold_spin: float = _rpm(2.2 / 7)
    latch_crests: int = 9               # while hands-free
    latch_turb: float = 0.8
    latch_spin: float = _rpm(-1.6 / 12)
    height: float = 1.4                 # a crest tip in silence, px
    push: float = 3.0                   # added to it at full voice, px
    swell: float = 0.10                 # the orb grows by this fraction at full voice
    glow: float = 0.35                  # and brightens by this much
    wobble: float = 1.0                 # scales how far the edge wanders on noise
    hue: float = 0.0                    # degrees, on top of each state's own
    brightness: float = 1.0
    comet_rpm: float = _rpm(3.2)        # transcribing
    comet_tail: float = math.degrees(1 / 0.7)   # degrees behind the head to 1/e
    rest_w: float = 40.0                # the resting capsule, rim to rim
    rest_h: float = 10.0
    rim_rpm: float = _rpm(1.3)          # its rim's colour circling

    @classmethod
    def resolve(cls, raw) -> "Tune":
        """config.json's `orb_tune`, clamped to what the page's sliders reach.
        A key it does not know, or a value that is not a number, keeps its
        default, so a hand-edited file cannot put the orb somewhere the page
        cannot bring it back from."""
        tune = cls()
        if not isinstance(raw, dict):
            return tune
        changes = {}
        for f in fields(cls):
            value = raw.get(f.name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                continue
            lo, hi = RANGES[f.name]
            value = min(hi, max(lo, float(value)))
            changes[f.name] = (round(value) if isinstance(getattr(tune, f.name), int)
                               else value)
        return replace(tune, **changes)

    @property
    def reach(self) -> float:
        """How far the loudest orb's glow reaches from its centre, in px. The
        glow ends one crest tip past the ring's outer edge; hands-free breathing,
        the voice swell and the ripple then push that out."""
        breathe = max(m.breathe for m in MOTION.values())
        return ((self.size / 2.0 * (1.0 + WARP) + (self.height + self.push) * PEAK)
                * (1.0 + breathe + self.swell))

    @property
    def win(self) -> int:
        """The overlay window's side, square and fixed for this tune: room for
        the loudest orb and the widest capsule, with a pixel to spare."""
        return 2 * math.ceil(max(self.reach, self.rest_w / 2.0 + 2.0)) + 2

    @property
    def bottom_pad(self) -> int:
        """How far the window hangs below the line the shape's bottom sits on, so
        the glow under a loud orb is not cut off. The overlay subtracts it when
        placing."""
        return math.ceil(self.reach - self.size / 2.0 * RING) + 1


# The page's slider ends, and the clamp on whatever config.json holds.
RANGES = {
    "size": (48.0, 96.0),
    "hold_crests": (3, 20),
    "hold_turb": (0.0, 1.0),
    "hold_spin": (-20.0, 20.0),
    "latch_crests": (3, 20),
    "latch_turb": (0.0, 1.0),
    "latch_spin": (-20.0, 20.0),
    "height": (0.0, 6.0),
    "push": (0.0, 16.0),
    "swell": (0.0, 0.25),
    "glow": (0.0, 1.0),
    "wobble": (0.0, 3.0),
    "hue": (-180.0, 180.0),
    "brightness": (0.4, 1.6),
    "comet_rpm": (5.0, 90.0),
    "comet_tail": (15.0, 300.0),
    "rest_w": (16.0, 72.0),
    "rest_h": (4.0, 20.0),
    "rim_rpm": (0.0, 40.0),
}


@dataclass(frozen=True)
class Motion:
    fluid: float        # how far the edge wanders on noise; 0 = rigid
    wander: float       # how fast the wander evolves, noise units per second
    lobes: float        # noise scale: higher means more, smaller bumps
    spin: float         # steady turn of the colour field, rad/s
    orbit: float        # the travelling highlight, rad/s
    sweep: float        # the colour sweep, rad/s; at rest, the rim's circling
    voice: float        # 0-1, how much the mic level drives the orb
    breathe: float      # size pulse, as a fraction of the radius; 0 = none
    gain: float         # brightness
    hue: float          # degrees, rotated in YIQ like the component's `hue` prop
    hold: float         # 0-1, weight of the holding crests
    latch: float        # 0-1, weight of the hands-free waves
    comet: float        # 0-1, how far the ring dims behind one bright head


# Per state. The comet's speed and the resting rim's come from the Tune instead.
MOTION = {
    "idle": Motion(fluid=0.0, wander=0.0, lobes=0.65, spin=0.0, orbit=0.0, sweep=1.3,
                   voice=0.0, breathe=0.0, gain=1.0, hue=0.0, hold=0.0, latch=0.0,
                   comet=0.0),
    "recording": Motion(fluid=0.5, wander=0.7, lobes=0.9, spin=0.4, orbit=-1.4,
                        sweep=2.2, voice=1.0, breathe=0.0, gain=1.0, hue=0.0,
                        hold=1.0, latch=0.0, comet=0.0),
    # Hands-free also breathes, because it is running on its own rather than
    # being held open.
    "latched": Motion(fluid=0.4, wander=0.3, lobes=0.4, spin=0.7, orbit=-0.8,
                      sweep=1.0, voice=1.0, breathe=0.035, gain=1.0, hue=0.0,
                      hold=0.0, latch=1.0, comet=0.0),
    # Transcribing: nearly rigid, one comet going steadily round. The mic is
    # closed, so a shape still reacting to sound would be a lie about it.
    "working": Motion(fluid=0.15, wander=0.1, lobes=0.5, spin=0.1, orbit=-3.2,
                      sweep=0.5, voice=0.0, breathe=0.0, gain=1.0, hue=0.0,
                      hold=0.0, latch=0.0, comet=1.0),
    "loading": Motion(fluid=0.15, wander=0.1, lobes=0.5, spin=0.1, orbit=-3.2,
                      sweep=0.5, voice=0.0, breathe=0.0, gain=0.55, hue=0.0,
                      hold=0.0, latch=0.0, comet=1.0),
    "error": Motion(fluid=0.2, wander=0.08, lobes=0.5, spin=0.0, orbit=-0.6,
                    sweep=0.3, voice=0.0, breathe=0.0, gain=1.0, hue=-80.0,
                    hold=0.0, latch=0.0, comet=0.0),
}
# Time constant for gliding between two states' motion. Short, because a short
# clip transcribes in 150-400ms and the comet has to read inside that.
EASE_S = 0.12
BREATH_RATE = 1.7       # rad/s


def voice_drive(level: float) -> float:
    """The overlay's 0-1 meter, reshaped for the orb: gated, then lifted."""
    return max(0.0, (level - VOICE_GATE) / (1.0 - VOICE_GATE)) ** VOICE_CURVE


class Choreography:
    """Where the orb is in its motion: the eased parameters and the phases they
    drive. Phases accumulate rather than being computed from a clock, so a
    change of speed between states never jumps the picture. `tune` may be
    swapped at any time; the motion eases to it the way it eases between
    states."""

    def __init__(self, tune: Tune | None = None) -> None:
        self.tune = tune or Tune()
        self.now = self.target("idle")
        self.drive = 0.0
        self.noise_t = 0.0
        self.rot = 0.0
        self.orbit = 0.0
        self.sweep = 0.0
        self.breath = 0.0
        self.ripple_t = 0.0
        self.hold_phase = 0.0
        self.latch_phase = 0.0

    def target(self, state: str) -> dict:
        """The motion a state settles to, with the tune's speeds in it."""
        key = state if state in MOTION else "idle"
        m = asdict(MOTION[key])
        if m["comet"]:
            m["orbit"] = -self.tune.comet_rpm * math.tau / 60.0
        if key == "idle":
            m["sweep"] = self.tune.rim_rpm * math.tau / 60.0
        return m

    def resting(self) -> bool:
        """Settled into the idle motion, where the only thing moving is the
        rim's slow circling, which the overlay draws at a lower frame rate."""
        return self.now == self.target("idle") and self.drive == 0.0

    def step(self, dt: float, state: str, level: float) -> bool:
        """Advance by dt seconds. True if the picture can have changed."""
        target = self.target(state)
        k = 1.0 - math.exp(-dt / EASE_S) if dt > 0 else 0.0
        moved = False
        for name, want in target.items():
            have = self.now[name]
            if have != want:
                have += (want - have) * k
                if abs(want - have) < 1e-3:
                    have = want
                self.now[name] = have
                moved = True
        m, t = self.now, self.tune
        self.drive = m["voice"] * voice_drive(level)
        heard = self.drive
        if dt > 0:
            self.noise_t += dt * (m["wander"] + heard * 0.8)
            self.rot += dt * (m["spin"] + heard * 1.6)
            self.orbit += dt * m["orbit"]
            self.sweep += dt * m["sweep"]
            self.breath += dt * (BREATH_RATE if m["breathe"] else 0.0)
            self.ripple_t += dt
            # A pattern of k crests turning at w rad/s moves its phase k times
            # as fast, which is what keeps the spin independent of the count.
            turn = dt * math.tau / 60.0 * (1.0 + heard)
            self.hold_phase += turn * t.hold_spin * t.hold_crests
            self.latch_phase += turn * t.latch_spin * t.latch_crests
            moved = True
        return moved

    def uniforms(self, morph: float, dpr: float) -> dict:
        """Shader inputs for a window of tune.win logical px square at this
        device pixel ratio, with `morph` running 0 (capsule) to 1 (orb), already
        eased."""
        m, t = self.now, self.tune
        full = t.size / 2.0 * RING
        half_h = t.rest_h / 2.0 + (full - t.rest_h / 2.0) * morph
        half_w = t.rest_w / 2.0 + (full - t.rest_w / 2.0) * morph
        unit = half_h / RING
        seg = max(0.0, half_w - half_h) / unit
        # The bottom of the body stays put as it grows, like the pill's.
        cy = t.win - t.bottom_pad - half_h
        unit *= (1.0 + m["breathe"] * morph * math.sin(self.breath)
                 + t.swell * self.drive * morph)
        px = dpr * SUPERSAMPLE
        # Tip heights are px on the full-size orb; the shader wants them in its
        # own units, where `wave` peaks at 1.
        crest = (t.height + t.push * self.drive) / (t.size / 2.0) * morph
        # A voice roughens the water as well as raising it.
        rough = ROUGH * self.drive
        return {
            "center": QtGui.QVector2D(t.win / 2.0 * px, (t.win - cy) * px),
            "scale": unit * px,
            "seg": seg,
            "fill": 1.0 - morph,
            "fluid": m["fluid"] * t.wobble * morph,
            "noiseT": self.noise_t,
            "lobes": m["lobes"],
            "rot": self.rot,
            "orbitA": self.orbit,
            "sweepA": self.sweep,
            "warp": WARP * self.drive * morph,
            "rippleT": self.ripple_t,
            "gain": m["gain"] * t.brightness * (1.0 + t.glow * self.drive),
            "hue": m["hue"] + t.hue,
            "holdK": float(t.hold_crests),
            "holdAmp": m["hold"] * crest,
            "holdPhase": self.hold_phase,
            "holdTurb": min(TURB_MAX, t.hold_turb + rough),
            "latchK": float(t.latch_crests),
            "latchAmp": m["latch"] * crest,
            "latchPhase": self.latch_phase,
            "latchTurb": min(TURB_MAX, t.latch_turb + rough),
            "comet": m["comet"] * morph,
            "tail": math.radians(t.comet_tail),
        }


# -- rendering ---------------------------------------------------------------

VERT = """
attribute vec2 position;
void main() {
  gl_Position = vec4(position, 0.0, 1.0);
}
"""

FRAG = """
uniform vec2 center;
uniform float scale;
uniform float seg;
uniform float fill;
uniform float fluid;
uniform float noiseT;
uniform float lobes;
uniform float rot;
uniform float orbitA;
uniform float sweepA;
uniform float warp;
uniform float rippleT;
uniform float gain;
uniform float hue;
uniform float holdK;
uniform float holdAmp;
uniform float holdPhase;
uniform float holdTurb;
uniform float latchK;
uniform float latchAmp;
uniform float latchPhase;
uniform float latchTurb;
uniform float comet;
uniform float tail;

const float TAU = 6.2831853;
// The most ripples the smallest wave may put round the ring, whatever the crest
// count. Twenty is a wavelength of ~9px on the shipped orb.
const float RIPPLE_K = 20.0;

vec3 rgb2yiq(vec3 c) {
  return vec3(dot(c, vec3(0.299, 0.587, 0.114)),
              dot(c, vec3(0.596, -0.274, -0.322)),
              dot(c, vec3(0.211, -0.523, 0.312)));
}

vec3 yiq2rgb(vec3 c) {
  return vec3(c.x + 0.956 * c.y + 0.621 * c.z,
              c.x - 0.272 * c.y - 0.647 * c.z,
              c.x - 1.106 * c.y + 1.703 * c.z);
}

vec3 adjustHue(vec3 color, float hueDeg) {
  float a = hueDeg * 3.14159265 / 180.0;
  vec3 yiq = rgb2yiq(color);
  float i = yiq.y * cos(a) - yiq.z * sin(a);
  float q = yiq.y * sin(a) + yiq.z * cos(a);
  return yiq2rgb(vec3(yiq.x, i, q));
}

vec3 hash33(vec3 p3) {
  p3 = fract(p3 * vec3(0.1031, 0.11369, 0.13787));
  p3 += dot(p3, p3.yxz + 19.19);
  return -1.0 + 2.0 * fract(vec3(p3.x + p3.y, p3.x + p3.z, p3.y + p3.z) * p3.zyx);
}

float snoise3(vec3 p) {
  const float K1 = 0.333333333;
  const float K2 = 0.166666667;
  vec3 i = floor(p + (p.x + p.y + p.z) * K1);
  vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);
  vec3 e = step(vec3(0.0), d0 - d0.yzx);
  vec3 i1 = e * (1.0 - e.zxy);
  vec3 i2 = 1.0 - e.zxy * (1.0 - e);
  vec3 d1 = d0 - (i1 - K2);
  vec3 d2 = d0 - (i2 - K1);
  vec3 d3 = d0 - 0.5;
  vec4 h = max(0.6 - vec4(dot(d0, d0), dot(d1, d1), dot(d2, d2), dot(d3, d3)), 0.0);
  vec4 n = h * h * h * h * vec4(dot(d0, hash33(i)), dot(d1, hash33(i + i1)),
                                dot(d2, hash33(i + i2)), dot(d3, hash33(i + 1.0)));
  return dot(vec4(31.316), n);
}

const vec3 baseColor1 = vec3(0.611765, 0.262745, 0.996078);
const vec3 baseColor2 = vec3(0.298039, 0.760784, 0.913725);
const vec3 baseColor3 = vec3(0.062745, 0.078431, 0.600000);
const float innerRadius = 0.6;

// Holding: open water. One swell of K crests travels round, leaning the way it
// goes as wind-driven water does; its neighbouring counts beat against it, so
// no two crests match and the pattern keeps rearranging itself; and smaller
// ripples ride on top. Turbulence only adds company to the swell — every term
// is a sine, so the outline stays round at any setting and can never form a
// corner, which is the whole point of it. Counts are integers, so the pattern
// meets itself where atan wraps.
//
// Divides by the sum of its own terms, so it peaks at exactly 1 (see PEAK).
// Dividing by only 0.6 of that sum, as it did until 17 Sep, let the terms stack
// to 1.23x nominal wherever they happened to line up -- a few crests standing a
// quarter taller than their neighbours, which is the shape of a star's point
// and is what Gabe was seeing when he raised his voice.
float wave(float K, float ph, float turb, float ga, float t) {
  float a1 = 0.55 * turb, a2 = 0.40 * turb, a3 = 0.30 * turb;
  float x = K * ga + ph;
  // Ripples have a size of their own rather than a fraction of the swell's, or
  // a high crest count puts so many of them round the ring that it reads as a
  // gear rather than as water.
  float rk = min(2.0 * K, RIPPLE_K);
  float s = sin(x + 0.25 * turb * sin(x))
          + a1 * sin((K + 1.0) * ga + 1.3 * ph + 0.7 * t)
          + a2 * sin((K - 1.0) * ga - 0.9 * ph + 1.9)
          + a3 * sin(rk * ga - 1.7 * ph + 0.6 * t);
  return s / (1.0 + a1 + a2 + a3);
}

// Hands-free: the same water with the wind on it. The swell carries two
// octaves of smaller, faster waves rather than sidebands, so the ring is
// visibly textured instead of visibly taller -- which is how you tell the two
// states apart now that they carry the same number of crests. Capped like the
// swell's ripples above, so the top of the Waves slider cannot turn the fine
// octave into a gear. Peaks at 1 the same way.
//
// The ripples run ONE MORE than twice and four times the crest count, never
// exactly twice and four times. At the exact multiples they are harmonics: they
// lock to the swell and every crest becomes the same peaked waveform, which
// measured 6.77 tip over trough where a sine measures 1.00 and looked like a
// star at the probe's magnification. Offset by one they beat against the swell
// instead, so no two crests match and the pattern keeps rearranging -- the same
// trick the swell plays with its own K+-1 sidebands. Measured 1.00.
float ripples(float K, float ph, float turb, float ga, float t) {
  float a2 = 0.50 * turb, a3 = 0.25 * turb * turb;
  float s = sin(K * ga + ph)
          + a2 * sin(min(2.0 * K + 1.0, RIPPLE_K + 1.0) * ga - 1.7 * ph + 0.6 * t)
          + a3 * sin(min(4.0 * K + 1.0, 2.0 * RIPPLE_K + 1.0) * ga + 2.3 * ph - 0.9 * t);
  return s / (1.0 + a2 + a3);
}

float light1(float intensity, float attenuation, float dist) {
  return intensity / (1.0 + dist * attenuation);
}

float light2(float intensity, float attenuation, float dist) {
  return intensity / (1.0 + dist * dist * attenuation);
}

void main() {
  vec3 color1 = adjustHue(baseColor1, hue);
  vec3 color2 = adjustHue(baseColor2, hue);
  vec3 color3 = adjustHue(baseColor3, hue);

  vec2 p = (gl_FragCoord.xy - center) / scale;
  p.x += warp * sin(p.y * 10.0 + rippleT);
  p.y += warp * sin(p.x * 10.0 + rippleT);
  // The colour field turns; the shape does not, or the capsule would tip over.
  vec2 r = vec2(cos(rot) * p.x - sin(rot) * p.y, sin(rot) * p.x + cos(rot) * p.y);

  // Distance from a capsule's spine; with seg = 0 this is the component's length(uv).
  float len = length(vec2(max(abs(p.x) - seg, 0.0), p.y));
  float ga = atan(p.y, p.x);     // around the shape itself
  float ang = atan(r.y, r.x);    // around the turning colour field

  float n0 = snoise3(vec3(r * lobes, noiseT)) * 0.5 + 0.5;
  float nf = clamp(0.5 + (n0 - 0.5) * fluid, 0.0, 1.0);
  float r0 = mix(mix(innerRadius, 1.0, 0.4), mix(innerRadius, 1.0, 0.6), nf);

  float crest = holdAmp * wave(holdK, holdPhase, holdTurb, ga, rippleT)
              + latchAmp * ripples(latchK, latchPhase, latchTurb, ga, rippleT);
  r0 += crest;

  // -- the orb ---------------------------------------------------------------
  float d0 = abs(len - r0);
  float v0 = light1(1.0, 10.0, d0) * smoothstep(r0 * 1.05, r0, len);
  float cl = cos(ang + sweepA) * 0.5 + 0.5;
  float a = orbitA - rot;
  vec2 pos = vec2(cos(a), sin(a)) * r0;
  pos.x += sign(pos.x) * seg;
  float v1 = light2(1.5, 5.0, distance(p, pos)) * light1(1.0, 50.0, d0);
  float v2 = smoothstep(1.0 + crest, mix(innerRadius, 1.0, nf * 0.5) + crest, len);
  float v3 = smoothstep(innerRadius + crest, mix(innerRadius, 1.0, 0.5) + crest, len);

  vec3 col = mix(color3, mix(color1, color2, cl), v0);
  col = (col + v1) * v2 * v3;
  // The comet: bright at its head, fading along the tail it leaves behind.
  float behind = mod(ga - orbitA, TAU);
  float fade = exp(-behind / tail);
  col *= mix(1.0, 0.45 + 1.5 * fade, comet);
  col = clamp(col * gain, 0.0, 1.0);
  vec4 orb = vec4(col, max(max(col.r, col.g), col.b));

  // -- the resting capsule: glass, with a rim whose colour circles ------------
  float px = 1.0 / scale;                             // one render pixel, in units
  float rim = smoothstep(2.6 * px, 1.0 * px, abs(len - r0));
  vec3 rimColor = mix(color1, color2, cos(ga + sweepA) * 0.5 + 0.5);
  float inside = smoothstep(r0 + px, r0 - px, len);
  float sheen = smoothstep(0.0, 0.8, p.y / r0) * 0.22;   // lighter toward the top
  vec3 glass = vec3(0.02, 0.02, 0.06) + rimColor * 0.10 + vec3(sheen);
  float glassA = 0.42 * inside;
  vec4 rest = vec4(rimColor * rim, rim) + vec4(glass * glassA, glassA) * (1.0 - rim);

  gl_FragColor = mix(orb, rest, fill);
}
"""

GL_FLOAT = 0x1406
GL_TRIANGLES = 0x0004
GL_BLEND = 0x0BE2
GL_COLOR_BUFFER_BIT = 0x4000


class Renderer:
    """An offscreen OpenGL context that turns shader inputs into a QImage.
    GUI thread only. Raises RuntimeError if OpenGL is not available, which the
    overlay answers by drawing the pill instead."""

    def __init__(self) -> None:
        self._ctx = QtGui.QOpenGLContext()
        if not self._ctx.create():
            raise RuntimeError("no OpenGL context")
        self._surface = QtGui.QOffscreenSurface()
        self._surface.setFormat(self._ctx.format())
        self._surface.create()
        self._make_current()
        self._gl = self._ctx.functions()
        self._program = QtOpenGL.QOpenGLShaderProgram()
        ok = (self._program.addShaderFromSourceCode(
                  QtOpenGL.QOpenGLShader.ShaderTypeBit.Vertex, VERT)
              and self._program.addShaderFromSourceCode(
                  QtOpenGL.QOpenGLShader.ShaderTypeBit.Fragment, FRAG)
              and self._program.link())
        if not ok:
            raise RuntimeError("orb shader: " + self._program.log().strip())
        # One triangle that covers the whole target, as ogl's Triangle does.
        data = array.array("f", (-1, -1, 3, -1, -1, 3)).tobytes()
        self._vbo = QtOpenGL.QOpenGLBuffer(QtOpenGL.QOpenGLBuffer.Type.VertexBuffer)
        self._vbo.create()
        self._vbo.bind()
        self._vbo.allocate(data, len(data))
        self._fbo: QtOpenGL.QOpenGLFramebufferObject | None = None

    def _make_current(self) -> None:
        if not self._ctx.makeCurrent(self._surface):
            raise RuntimeError("OpenGL context lost")

    def render(self, uniforms: dict, size_px: int) -> QtGui.QImage:
        self._make_current()
        if self._fbo is None or self._fbo.width() != size_px:
            self._fbo = QtOpenGL.QOpenGLFramebufferObject(QtCore.QSize(size_px, size_px))
        gl, prog = self._gl, self._program
        self._fbo.bind()
        gl.glViewport(0, 0, size_px, size_px)
        gl.glDisable(GL_BLEND)
        gl.glClearColor(0.0, 0.0, 0.0, 0.0)
        gl.glClear(GL_COLOR_BUFFER_BIT)
        prog.bind()
        self._vbo.bind()
        prog.enableAttributeArray("position")
        prog.setAttributeBuffer("position", GL_FLOAT, 0, 2, 8)
        for name, value in uniforms.items():
            if isinstance(value, QtGui.QVector2D):
                prog.setUniformValue(name, value)
            else:
                prog.setUniformValue1f(name, float(value))
        gl.glDrawArrays(GL_TRIANGLES, 0, 3)
        image = self._fbo.toImage()
        self._fbo.release()
        return image

    def close(self) -> None:
        if self._ctx.makeCurrent(self._surface):
            self._fbo = None
            self._vbo.destroy()
            self._program.removeAllShaders()
            self._ctx.doneCurrent()
        self._surface.destroy()
