"""Shared Win32 bindings. Raw ctypes — pywin32 is a heavy dependency for this much.

Used by inject.py (synthetic paste, elevation check) and watchdog.py (key polling).
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LWIN, VK_RWIN = 0x5B, 0x5C
VK_CONTROL, VK_V = 0x11, 0x56

CHORD_VKS = (VK_LCONTROL, VK_RCONTROL, VK_LWIN, VK_RWIN)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL


def key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def any_chord_key_down() -> bool:
    return any(key_down(vk) for vk in CHORD_VKS)


def _kb_input(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0, dwExtraInfo=None)
    return inp


def send_ctrl_v() -> int:
    """Synthesize Ctrl+V. Returns the number of events actually inserted.

    Note: SendInput is subject to UIPI and can fail against a higher-integrity
    foreground window without the return value indicating it — which is why
    foreground_is_elevated() is checked first rather than relying on this.
    """
    events = (INPUT * 4)(
        _kb_input(VK_CONTROL, False),
        _kb_input(VK_V, False),
        _kb_input(VK_V, True),
        _kb_input(VK_CONTROL, True),
    )
    return user32.SendInput(4, events, ctypes.sizeof(INPUT))


def foreground_is_elevated() -> bool:
    """True when the focused window belongs to a process we cannot inject into.

    A non-elevated caller can usually OpenProcess with QUERY_LIMITED_INFORMATION
    even on an elevated target, but OpenProcessToken is denied — that denial is
    the signal. Protected processes (some AV) also land here, which is the safe
    direction to be wrong in: we skip the paste and leave the text on the
    clipboard rather than firing keystrokes into nothing.
    """
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return True
    try:
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return True
        kernel32.CloseHandle(token)
        return False
    finally:
        kernel32.CloseHandle(handle)
