"""Pure gesture state machine.

No I/O, no Windows calls, no threads, no clock — every method takes an explicit
timestamp. This is the part most likely to be subtly wrong and the part hardest to
exercise through a real keyboard, so it is kept pure and tested with synthetic
sequences that run in milliseconds.

Gestures:
    hold          press chord, speak, release              -> commit
    double-tap    tap, tap again inside the latch window   -> latched, hands-free
    tap to end    single press while latched               -> commit

Recording starts on chord-down every time (research: latency), so the latch window
is waited out on audio that is already being captured and costs nothing.
"""
from __future__ import annotations

from enum import Enum


class Event(Enum):
    CHORD_DOWN = "chord_down"
    CHORD_UP = "chord_up"
    OTHER_KEY = "other_key"   # a third key arrived while both modifiers were held
    TICK = "tick"


class Command(Enum):
    START_CAPTURE = "start_capture"
    COMMIT = "commit"     # stop capturing, transcribe, inject
    DISCARD = "discard"   # stop capturing, throw the audio away


class State(Enum):
    IDLE = "idle"
    HOLDING = "holding"              # chord down, capturing
    PENDING_LATCH = "pending_latch"  # chord released after a short tap, still capturing
    LATCHED = "latched"              # hands-free, capturing, chord not held
    DEAD_CHORD = "dead_chord"        # chord physically down but we are done with it


class Gestures:
    def __init__(
        self,
        tap_max_ms: int = 300,
        latch_window_ms: int = 350,
        ptt_ceiling_s: int = 120,
        latch_ceiling_s: int = 600,
    ) -> None:
        self.tap_max = tap_max_ms / 1000.0
        self.latch_window = latch_window_ms / 1000.0
        self.ptt_ceiling = float(ptt_ceiling_s)
        self.latch_ceiling = float(latch_ceiling_s)

        self.state = State.IDLE
        self._t_down = 0.0    # when the current chord press began
        self._t_tap_up = 0.0  # when a short tap was released
        self._t_latch = 0.0   # when latched mode began

    # -- derived state, read by the tray and the watchdog ------------------

    @property
    def capturing(self) -> bool:
        return self.state in (State.HOLDING, State.PENDING_LATCH, State.LATCHED)

    @property
    def chord_held(self) -> bool:
        """True when we believe the physical chord is down. The watchdog compares
        this against GetAsyncKeyState to catch a key-up lost to an elevated window."""
        return self.state in (State.HOLDING, State.DEAD_CHORD)

    # -- the machine -------------------------------------------------------

    def handle(self, event: Event, t: float) -> list[Command]:
        return getattr(self, "_" + self.state.value)(event, t)

    def _idle(self, event: Event, t: float) -> list[Command]:
        if event is Event.CHORD_DOWN:
            self._t_down = t
            self.state = State.HOLDING
            return [Command.START_CAPTURE]
        return []

    def _holding(self, event: Event, t: float) -> list[Command]:
        if event is Event.CHORD_UP:
            if t - self._t_down > self.tap_max:
                # A deliberate hold cannot be the first half of a double-tap, so
                # commit immediately rather than waiting out the latch window.
                self.state = State.IDLE
                return [Command.COMMIT]
            self._t_tap_up = t
            self.state = State.PENDING_LATCH
            return []
        if event is Event.OTHER_KEY:
            # Ctrl+Win+D and friends. Cheap insurance; the OS shortcut still fires
            # because the hook only observes.
            self.state = State.DEAD_CHORD
            return [Command.DISCARD]
        if event is Event.TICK and t - self._t_down > self.ptt_ceiling:
            self.state = State.DEAD_CHORD
            return [Command.COMMIT]
        return []

    def _pending_latch(self, event: Event, t: float) -> list[Command]:
        if event is Event.CHORD_DOWN:
            if t - self._t_tap_up <= self.latch_window:
                self._t_latch = t
                self.state = State.LATCHED
                return []
            # Too slow to be a double-tap: close the previous clip, open a new one.
            self._t_down = t
            self.state = State.HOLDING
            return [Command.COMMIT, Command.START_CAPTURE]
        if event is Event.TICK and t - self._t_tap_up > self.latch_window:
            self.state = State.IDLE
            return [Command.COMMIT]
        return []

    def _latched(self, event: Event, t: float) -> list[Command]:
        if event is Event.CHORD_DOWN:
            # Tap again to end. Ends on key-down for latency; the matching key-up
            # is absorbed by DEAD_CHORD.
            self.state = State.DEAD_CHORD
            return [Command.COMMIT]
        if event is Event.TICK and t - self._t_latch > self.latch_ceiling:
            self.state = State.IDLE
            return [Command.COMMIT]
        return []

    def _dead_chord(self, event: Event, t: float) -> list[Command]:
        if event is Event.CHORD_UP:
            self.state = State.IDLE
        return []
