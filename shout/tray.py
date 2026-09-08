"""Tray icon, on Qt.

Was pystray, which needed its own thread and its own message loop beside Tk's.
Qt owns the main thread and QSystemTrayIcon lives on it, so that whole thread —
and the "which of the two toolkits gets the main thread" problem it existed to
solve — is gone.

Everything crossing a thread boundary does so as a plain attribute or a deque
append, drained by a timer on the GUI thread. Qt widgets, QSystemTrayIcon
included, may only be touched from the thread that created them, and
`set_state()`/`notify()` are both called from worker threads.
"""
from __future__ import annotations

import collections
import logging

from PySide6 import QtCore, QtGui, QtWidgets

log = logging.getLogger(__name__)

POLL_MS = 120

# state -> (fill, ring or None)
_COLORS = {
    "loading": ((110, 110, 110), None),
    "idle": ((70, 130, 200), None),
    "recording": ((220, 60, 60), None),
    "latched": ((220, 60, 60), (255, 210, 60)),
    "working": ((235, 170, 50), None),
    "error": ((120, 30, 30), None),
}

_LABELS = {
    "loading": "Shout — loading model",
    "idle": "Shout — ready",
    "recording": "Shout — recording",
    "latched": "Shout — recording (hands-free)",
    "working": "Shout — transcribing",
    "error": "Shout — error",
}


def _icon(state: str) -> QtGui.QIcon:
    fill, ring = _COLORS.get(state, _COLORS["idle"])
    pm = QtGui.QPixmap(64, 64)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    if ring:
        p.setPen(QtGui.QPen(QtGui.QColor(*ring), 6))
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        p.drawEllipse(QtCore.QRectF(5, 5, 54, 54))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(*fill))
        p.drawEllipse(QtCore.QRectF(16, 16, 32, 32))
    else:
        p.setBrush(QtGui.QColor(*fill))
        p.drawEllipse(QtCore.QRectF(10, 10, 44, 44))
    p.end()
    return QtGui.QIcon(pm)


class Tray:
    def __init__(self, on_quit) -> None:
        self._on_quit = on_quit
        self._state = "loading"        # written by any thread
        self._applied = None           # what the icon currently shows
        self._stopped = False
        self._pending: collections.deque[tuple[str, str]] = collections.deque()
        self.icon: QtWidgets.QSystemTrayIcon | None = None
        self._menu: QtWidgets.QMenu | None = None
        self._timer: QtCore.QTimer | None = None

    # -- called from other threads (attribute rebind / deque append) --------

    def set_state(self, state: str) -> None:
        self._state = state

    def notify(self, message: str, title: str = "Shout") -> None:
        self._pending.append((title, message))

    # -- GUI thread ---------------------------------------------------------

    def build(self) -> None:
        self.icon = QtWidgets.QSystemTrayIcon(_icon("loading"))
        self.icon.setToolTip(_LABELS["loading"])
        menu = QtWidgets.QMenu()
        quit_action = menu.addAction("Quit Shout")
        quit_action.triggered.connect(lambda _checked=False: self._on_quit())
        # The menu must outlive this call; a QMenu with no Python reference is
        # collected and the tray item then has no menu at all.
        self._menu = menu
        self.icon.setContextMenu(menu)
        self.icon.show()
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.start(POLL_MS)

    def _tick(self) -> None:
        if self._stopped:
            return
        try:
            if self._state != self._applied:
                self._applied = self._state
                self.icon.setIcon(_icon(self._state))
                self.icon.setToolTip(_LABELS.get(self._state, "Shout"))
            while self._pending:
                title, message = self._pending.popleft()
                self.icon.showMessage(title, message, _icon(self._state), 5000)
        except Exception:
            log.exception("tray tick")

    def stop(self) -> None:
        """Callable from any thread; see Overlay.stop()."""
        self._stopped = True

    def teardown(self) -> None:
        """GUI thread only, after app.exec() has returned."""
        if self._timer is not None:
            self._timer.stop()
        if self.icon is not None:
            self.icon.hide()
