"""One Shout at a time, and a way past one that has frozen.

A named mutex says a Shout is running; it cannot say that Shout is alive. On
21 Oct 2026 an NVIDIA driver installed under a running Shout, the orb's next
makeCurrent never returned, and the GUI thread sat in the driver HOLDING THE
GIL, so every thread stopped, the hotkey with them, while the mutex stayed
held and every click on the icon was refused. TerminateProcess could not
finish that process either: a thread in a kernel wait keeps the process, and
its handles, until the driver lets go.

So the GUI thread also beats a counter in a named shared-memory block (RAM,
never disk). A launch that finds the mutex taken watches the counter; if it
does not move, the running copy is frozen, and the launch terminates it and
runs anyway. The beat is a QTimer on the GUI thread on purpose: a Python thread
would keep beating through a hang that releases the GIL, and what matters is
the event loop.

The block is in the Local namespace, though the mutex is Global: creating a
Global file mapping needs SeCreateGlobalPrivilege, which a normal token lacks.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore

log = logging.getLogger("shout.instance")

MUTEX_NAME = "Global\\ShoutDictationSingleInstance"
BEAT_MS = 500
# How long a launch watches the counter before calling the running copy
# frozen: ten beats. A healthy copy answers within one.
FROZEN_AFTER_S = 5.0

ERROR_ALREADY_EXISTS = 183
PAGE_READWRITE = 0x04
FILE_MAP_ALL_ACCESS = 0xF001F
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateMutexW.restype = wintypes.HANDLE
_k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_k32.CreateFileMappingW.restype = wintypes.HANDLE
_k32.CreateFileMappingW.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                    wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR)
_k32.OpenFileMappingW.restype = wintypes.HANDLE
_k32.OpenFileMappingW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
_k32.MapViewOfFile.restype = ctypes.c_void_p
_k32.MapViewOfFile.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                               wintypes.DWORD, ctypes.c_size_t)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
_k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)


class _Block(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint32), ("beats", ctypes.c_uint32)]


# Never closed: the mutex and the block are this process's claim for its life.
_handles: list[int] = []
_block: _Block | None = None


def _block_name(mutex_name: str) -> str:
    return "Local\\" + mutex_name.rsplit("\\", 1)[-1] + ".heartbeat"


def _view(handle: int | None) -> _Block | None:
    if not handle:
        return None
    _handles.append(handle)
    addr = _k32.MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, ctypes.sizeof(_Block))
    return _Block.from_address(addr) if addr else None


def open_block(mutex_name: str = MUTEX_NAME) -> _Block | None:
    """The running copy's block, or None if there is none."""
    return _view(_k32.OpenFileMappingW(FILE_MAP_ALL_ACCESS, False, _block_name(mutex_name)))


def _beating(mutex_name: str, wait_s: float) -> tuple[bool, _Block | None]:
    """Whether the running copy's counter moves within wait_s. The block may
    not exist yet when that copy is a moment into its own start."""
    deadline = time.monotonic() + wait_s
    block, first = None, None
    while time.monotonic() < deadline:
        block = block or open_block(mutex_name)
        if block is not None:
            if first is None:
                first = block.beats
            elif block.beats != first:
                return True, block
        time.sleep(0.1)
    return False, block


def _ours(process: int) -> bool:
    """Whether a process runs from this venv's interpreter. The pid in a block
    can outlive its owner (a copy stuck in a driver keeps the block open after
    the copy that took over from it has exited), and Windows reuses pids. The
    base interpreter counts too: the venv's python.exe is uv's trampoline,
    which runs the base one as its child."""
    buf = ctypes.create_unicode_buffer(32768)
    size = wintypes.DWORD(len(buf))
    if not _k32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size)):
        return False
    here = {Path(p).resolve().parent for p in (sys.executable, sys._base_executable)}
    return Path(buf.value).resolve().parent in here


def _terminate(pid: int) -> None:
    access = PROCESS_TERMINATE | PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE
    process = _k32.OpenProcess(access, False, pid)
    if not process:
        log.warning("the frozen copy (pid %d) could not be opened; running anyway", pid)
        return
    try:
        if not _ours(process):
            log.warning("pid %d is no longer Shout; leaving it alone", pid)
            return
        _k32.TerminateProcess(process, 1)
        if _k32.WaitForSingleObject(process, 3000) == WAIT_OBJECT_0:
            log.warning("ended the frozen copy (pid %d)", pid)
        else:
            # Its user-mode threads are gone, hook included; only a thread
            # inside a driver remains, and it holds nothing that stops us.
            log.warning("the frozen copy (pid %d) is still exiting, held by a "
                        "driver; it ends at the next reboot", pid)
    finally:
        _k32.CloseHandle(process)


def claim(mutex_name: str = MUTEX_NAME, wait_s: float = FROZEN_AFTER_S) -> bool:
    """True if this process should run: no other Shout holds the mutex, or the
    one that does has stopped beating."""
    global _block
    mutex = _k32.CreateMutexW(None, False, mutex_name)
    taken = ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    _handles.append(mutex)
    if taken:
        alive, block = _beating(mutex_name, wait_s)
        if alive:
            return False
        if block is None:
            log.warning("a Shout without a heartbeat holds the lock; running anyway")
        else:
            log.warning("the running Shout (pid %d) has not beat in %.0fs; taking over",
                        block.pid, wait_s)
            _terminate(block.pid)
    handle = _k32.CreateFileMappingW(wintypes.HANDLE(-1), None, PAGE_READWRITE,
                                     0, ctypes.sizeof(_Block), _block_name(mutex_name))
    _block = _view(handle)
    if _block is not None:
        _block.pid = os.getpid()
    return True


def beat() -> None:
    if _block is not None:
        _block.beats += 1


def start_beating() -> QtCore.QTimer:
    """Beat from the GUI thread for as long as its event loop runs. Keep the
    timer referenced."""
    timer = QtCore.QTimer()
    timer.timeout.connect(beat)
    timer.start(BEAT_MS)
    return timer
