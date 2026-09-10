"""The last few dictations, for when a paste lands nowhere.

Click out of the text field before the paste arrives and Ctrl+V goes into
nothing. SendInput cannot tell: `inject()` reports "pasted", and 300ms later the
previous clipboard is put back. Without this, the transcript is simply gone.

Memory only, and bounded. The README promises your words are never written to
disk, and a missed paste is noticed in seconds, not days, so a file would buy
nothing worth breaking that promise for. Quitting Shout forgets them.
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass

SIZE = 10


@dataclass(frozen=True)
class Entry:
    at: float           # time.time() when the transcript came back
    text: str


class Recent:
    """Written by the commit thread, read on the GUI thread."""

    def __init__(self) -> None:
        self._items: collections.deque[Entry] = collections.deque(maxlen=SIZE)
        self._lock = threading.Lock()
        # Bumped on every change, so a view can tell it is stale by comparing
        # one int rather than the entries.
        self.version = 0

    def add(self, text: str) -> None:
        with self._lock:
            self._items.append(Entry(time.time(), text))
            self.version += 1

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.version += 1

    def newest_first(self) -> list[Entry]:
        with self._lock:
            return list(reversed(self._items))

    def last(self) -> Entry | None:
        with self._lock:
            return self._items[-1] if self._items else None
