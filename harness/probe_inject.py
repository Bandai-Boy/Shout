"""End-to-end injection against a target whose contents can be read back.

The oracle is a Tkinter Text widget: stdlib, no dependencies, and fully under our
control, so "did the text actually arrive" is a real measurement rather than a
screenshot or a guess.

Two safety rules, both learned the hard way:

* Never inject without first confirming the Tk window owns the foreground. If
  focus lands somewhere else, SendInput would type a transcript into whatever the
  user happens to have open. The probe fails loudly instead.
* Every claim gets a control. The negative control disables the keystroke and
  requires the oracle to come back EMPTY — otherwise a probe that always reports
  "text present" would pass forever.

A Tk widget proves the mechanism, not the world. The manual pass in real apps
(browser, editor, terminal, chat) is still required.
"""
from __future__ import annotations

import ctypes
import sys
import time
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The console here is cp1252; harness output is not.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shout import inject as inj
from shout.config import Config
from shout.winapi import user32

GA_ROOT = 2
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), name, detail))


class Target:
    """A focusable window whose text can be read back."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Shout injection target")
        self.root.geometry("520x140+200+200")
        self.root.attributes("-topmost", True)
        self.text = tk.Text(self.root, height=6, width=60)
        self.text.pack(fill="both", expand=True)
        self.text.focus_set()
        self.pump(0.2)

    def pump(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)

    def hwnds(self) -> tuple[int, int]:
        hwnd = self.root.winfo_id()
        return hwnd, user32.GetAncestor(hwnd, GA_ROOT)

    def take_focus(self, timeout: float = 3.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.root.lift()
            self.root.focus_force()
            self.text.focus_set()
            self.pump(0.05)
            if user32.GetForegroundWindow() in self.hwnds():
                return True
        return False

    def contents(self) -> str:
        return self.text.get("1.0", "end-1c")

    def clear(self) -> None:
        self.text.delete("1.0", "end")
        self.pump(0.05)

    def wait_for_text(self, timeout: float = 3.0) -> str:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.pump(0.05)
            if self.contents().strip():
                break
        return self.contents()

    def close(self) -> None:
        try:
            self.root.destroy()
        except Exception:
            pass


def main() -> int:
    cfg = Config()
    original = inj.get_clipboard_text()
    target = Target()

    try:
        if not target.take_focus():
            check(False, "target window took foreground focus",
                  "ABORTED without injecting — refusing to type into an unknown window")
            raise SystemExit(_report())
        check(True, "target window took foreground focus")

        # --- subject: a normal injection ------------------------------
        sample = "The Charizard is a Base Set holo, near mint, PSA 8."
        target.clear()
        t0 = time.perf_counter()
        outcome = inj.inject(sample, cfg)
        got = target.wait_for_text()
        elapsed = (time.perf_counter() - t0) * 1000

        check(outcome == "pasted", "inject reported a successful paste", outcome)
        check(got == sample, "text arrived in the target exactly", repr(got[:60]))
        check(elapsed < 600, "injection latency", f"{elapsed:.0f}ms")

        # --- negative control: the probe must be able to see nothing ---
        real_send = inj.send_ctrl_v
        inj.send_ctrl_v = lambda: 4  # clipboard is set, no keystroke is sent
        try:
            target.clear()
            control_outcome = inj.inject("control text that must not appear", cfg)
            control_got = target.wait_for_text(timeout=1.2)
        finally:
            inj.send_ctrl_v = real_send
        check(control_outcome == "pasted", "control ran through the same code path",
              control_outcome)
        check(control_got == "", "control: with no keystroke the oracle reads EMPTY",
              repr(control_got[:40]) + " — proves the subject measured a real paste")

        # --- multi-line and unicode -----------------------------------
        target.clear()
        tricky = "Line one — em dash, ünïcode, 中文.\nLine two after a newline."
        inj.inject(tricky, cfg)
        got2 = target.wait_for_text()
        check(got2 == tricky, "multi-line unicode survives the clipboard round-trip",
              repr(got2[:50]))

        # --- clipboard restore ----------------------------------------
        sentinel = "user-clipboard-sentinel-" + str(int(time.time()))
        inj.set_clipboard_text(sentinel, private=False)
        target.clear()
        inj.inject("transient transcript", cfg)
        target.wait_for_text()
        deadline = time.monotonic() + 2.0
        restored = None
        while time.monotonic() < deadline:
            restored = inj.get_clipboard_text()
            if restored == sentinel:
                break
            target.pump(0.05)
        check(restored == sentinel, "the user's previous clipboard is put back",
              repr((restored or "")[:40]))

        # --- empty input ----------------------------------------------
        check(inj.inject("   ", cfg) == "empty", "whitespace-only text is not injected")

    finally:
        target.close()
        if original is not None:
            inj.set_clipboard_text(original, private=False)

    return _report()


def _report() -> int:
    failed = sum(not ok for ok, _, _ in RESULTS)
    print()
    for ok, name, detail in RESULTS:
        print(f"[{'ok  ' if ok else 'FAIL'}] {name}" + (f"  —  {detail}" if detail else ""))
    print(f"\nINJECT {len(RESULTS) - failed}/{len(RESULTS)} ok, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
