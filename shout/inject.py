"""Put transcribed text at the cursor.

Clipboard + synthetic Ctrl+V. UI Automation TextPattern is read-only, and typing
character by character is slow and drops keys under load.

Three things this has to get right, none of them obvious:

1. The chord contains Win. Firing Ctrl+V while the user still physically holds Win
   produces Ctrl+Win+V, which is the Clipboard History shortcut — a flyout opens,
   focus leaves the field, nothing pastes. Transcription returns in ~0.36s, which
   is easily faster than a slow finger lift, so this is the normal timing on a
   short utterance rather than an edge case. Wait for the modifiers to clear; never
   synthesize a Win key-up, which strands the modifier down.

2. Text on the clipboard is retained by Clipboard History and can sync to the
   cloud clipboard, which would defeat the entire point of a local-only stack.
   The documented opt-out formats are set alongside the text.

3. A non-elevated process cannot inject into an elevated window (UIPI) and
   SendInput does not reliably report that failure. Detect it beforehand and leave
   the text on the clipboard instead of losing it.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

from .winapi import (CHORD_VKS, foreground_is_elevated, kernel32, key_down,
                     send_ctrl_v, user32)

log = logging.getLogger(__name__)

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

# Presence of this format excludes the content from clipboard history and from
# roaming to the cloud clipboard.
_EXCLUDE = "ExcludeClipboardContentFromMonitorProcessing"
_NO_HISTORY = "CanIncludeInClipboardHistory"
_NO_CLOUD = "CanUploadToCloudClipboard"


def _open_clipboard(retries: int = 12, delay: float = 0.02) -> bool:
    """Another process can hold the clipboard; retry rather than fail the paste."""
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def get_clipboard_text() -> str | None:
    if not _open_clipboard():
        return None
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return None
        try:
            return ctypes.c_wchar_p(ptr).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def _set_dword_format(name: str, value: int) -> None:
    fmt = user32.RegisterClipboardFormatW(name)
    if not fmt:
        return
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, ctypes.sizeof(wintypes.DWORD))
    if not handle:
        return
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, ctypes.byref(wintypes.DWORD(value)),
                   ctypes.sizeof(wintypes.DWORD))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)


def set_clipboard_text(text: str, private: bool = True) -> bool:
    """private=True keeps the content out of clipboard history and the cloud."""
    buf = ctypes.create_unicode_buffer(text)
    size = ctypes.sizeof(buf)
    if not _open_clipboard():
        log.warning("could not open the clipboard")
        return False
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        ctypes.memmove(ptr, buf, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        if private:
            fmt = user32.RegisterClipboardFormatW(_EXCLUDE)
            if fmt:
                marker = kernel32.GlobalAlloc(GMEM_MOVEABLE, 1)
                if marker and not user32.SetClipboardData(fmt, marker):
                    kernel32.GlobalFree(marker)
            _set_dword_format(_NO_HISTORY, 0)
            _set_dword_format(_NO_CLOUD, 0)
        return True
    finally:
        user32.CloseClipboard()


def wait_for_modifiers_clear(timeout_s: float) -> bool:
    """Ctrl+V while Win is held is Ctrl+Win+V, which opens Clipboard History."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not any(key_down(vk) for vk in CHORD_VKS):
            return True
        time.sleep(0.01)
    return False


def _restore_later(ours: str, previous: str, delay_s: float) -> None:
    def restore() -> None:
        # Only put the old content back if ours is still there — if the user
        # copied something in the meantime, leave their copy alone.
        if get_clipboard_text() == ours:
            set_clipboard_text(previous, private=False)
    threading.Timer(delay_s, restore).start()


def inject(text: str, cfg) -> str:
    """Returns an outcome string. Anything other than 'pasted' means the text is
    sitting on the clipboard for the user to paste manually."""
    if not text.strip():
        return "empty"

    previous = get_clipboard_text() if cfg.restore_clipboard else None
    if not set_clipboard_text(text):
        return "clipboard_failed"

    if foreground_is_elevated():
        log.warning("focused window is elevated; text left on the clipboard")
        return "elevated_target"

    if not wait_for_modifiers_clear(cfg.modifier_wait_ms / 1000):
        log.warning("modifiers still held after %dms; text left on the clipboard",
                    cfg.modifier_wait_ms)
        return "modifiers_held"

    sent = send_ctrl_v()
    if sent != 4:
        log.warning("SendInput inserted %d/4 events", sent)
        return "sendinput_failed"

    if previous is not None:
        _restore_later(text, previous, cfg.clipboard_restore_ms / 1000)
    return "pasted"
