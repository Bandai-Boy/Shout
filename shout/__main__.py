"""Shout — hold Ctrl+Win, talk, text appears at the cursor.

    python -m shout

Threading model:
    main        pystray message loop
    hook        pynput WH_KEYBOARD_LL callback — enqueue and return, nothing else
    worker      drains the queue, runs the gesture machine, starts/stops capture
    watchdog    20ms poll: ticks, missed key-ups, dead-hook detection
    commit      single worker so transcripts are injected in the order spoken
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from logging.handlers import RotatingFileHandler

from .audio import Recorder
from .config import Config, config_dir
from .gestures import Command, Gestures
from .hotkey import HotkeyListener
from .tray import Tray
from .watchdog import Watchdog

log = logging.getLogger("shout")

SAMPLE_RATE = 16000
MIN_AUDIO_S = 0.25
MUTEX_NAME = "Global\\ShoutDictationSingleInstance"


def _setup_logging(level: str) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-16s %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fh = RotatingFileHandler(config_dir() / "shout.log", maxBytes=1_000_000,
                             backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.handlers = [fh, sh]


def _claim_single_instance() -> bool:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


class Shout:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.recorder = Recorder(device=cfg.input_device, preroll_ms=cfg.preroll_ms)
        self.gestures = Gestures(cfg.tap_max_ms, cfg.latch_window_ms,
                                 cfg.ptt_ceiling_s, cfg.latch_ceiling_s)
        self.hotkey = HotkeyListener(self._on_gesture_event)
        self.watchdog = Watchdog(self.gestures, self.hotkey, self._dispatch)
        self.tray = Tray(self._quit)
        self.commits = ThreadPoolExecutor(max_workers=1, thread_name_prefix="commit")
        self.ready = threading.Event()
        self.transcriber = None
        self._capture_started = 0.0
        self._stopping = False

    # -- model -------------------------------------------------------------

    def _load_model(self) -> None:
        t0 = time.perf_counter()
        try:
            from .transcribe import Transcriber  # imported late: pulls in CUDA
            self.transcriber = Transcriber(self.cfg)
            self.transcriber.load()
            self.transcriber.warmup()
            self.ready.set()
            self.tray.set_state("idle")
            log.info("READY in %.2fs from launch", time.perf_counter() - t0)
        except Exception:
            log.exception("model failed to load")
            self.tray.set_state("error")
            self.tray.notify("Model failed to load — see shout.log")

    # -- gesture plumbing ---------------------------------------------------

    def _on_gesture_event(self, event, t: float) -> None:
        self._dispatch(self.gestures.handle(event, t))

    def _dispatch(self, commands) -> None:
        for command in commands:
            try:
                self._run(command)
            except Exception:
                log.exception("command %s", command)
        if not self._stopping:
            self.tray.set_state(self._tray_state())

    def _tray_state(self) -> str:
        if not self.ready.is_set():
            return "loading"
        name = self.gestures.state.value
        if name == "latched":
            return "latched"
        if self.gestures.capturing:
            return "recording"
        return "idle"

    def _run(self, command: Command) -> None:
        if command is Command.START_CAPTURE:
            self._capture_started = time.perf_counter()
            self.recorder.start()
            log.debug("capture started")
        elif command is Command.DISCARD:
            audio = self.recorder.stop()
            log.info("discarded %.2fs of audio", len(audio) / SAMPLE_RATE)
        elif command is Command.COMMIT:
            audio = self.recorder.stop()
            held = time.perf_counter() - self._capture_started
            seconds = len(audio) / SAMPLE_RATE
            if seconds < MIN_AUDIO_S:
                log.info("ignoring %.2fs clip (held %.2fs)", seconds, held)
                return
            self.commits.submit(self._finish, audio, time.perf_counter())

    # -- transcribe + inject ------------------------------------------------

    def _finish(self, audio, t_commit: float) -> None:
        from .inject import inject

        self.tray.set_state("working")
        if not self.ready.wait(timeout=60):
            self.tray.notify("Model is still loading — clip dropped")
            return
        try:
            text = self.transcriber.transcribe(audio)
            if not text:
                log.info("empty transcript, nothing to inject")
                return
            outcome = inject(text, self.cfg)
            latency = time.perf_counter() - t_commit
            log.info("END-TO-END %.0fms for %.1fs of audio -> %s",
                     latency * 1000, len(audio) / SAMPLE_RATE, outcome)
            if outcome == "elevated_target":
                self.tray.notify("That window is elevated — text is on your clipboard")
            elif outcome not in ("pasted", "empty"):
                self.tray.notify(f"Could not paste ({outcome}) — text is on your clipboard")
        except Exception:
            log.exception("commit failed")
            self.tray.notify("Transcription failed — see shout.log")
        finally:
            self.tray.set_state(self._tray_state())

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> None:
        threading.Thread(target=self._load_model, name="model", daemon=True).start()
        self.recorder.arm()
        self.hotkey.start()
        self.watchdog.start()
        log.info("hold Ctrl+Win to dictate; double-tap to latch hands-free")
        self.tray.run()

    def _quit(self) -> None:
        self._stopping = True
        log.info("shutting down")
        self.watchdog.stop()
        self.hotkey.stop()
        self.recorder.close()
        self.commits.shutdown(wait=False)
        self.tray.stop()


def main() -> int:
    cfg = Config.load()
    _setup_logging(cfg.log_level)
    if not _claim_single_instance():
        log.error("Shout is already running")
        return 1
    Shout(cfg).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
