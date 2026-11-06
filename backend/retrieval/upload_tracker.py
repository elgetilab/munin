"""Which uploads a user has cancelled, so their documents do not stay stored.

The gateway does not pass a browser's disconnect on to retrieval, so aborting the
request in the browser leaves the upload running here. The browser therefore names
each upload and says so when the user cancels it. The cancel can land at any point:

- before or during the upload: the route sees it and removes the document;
- after the upload finished, when the reply was lost or still on its way: the
  tracker remembers the document, so the cancel removes it directly.

Held in memory, because retrieval runs as one process and an entry only matters
for the minutes around one upload. A restart forgets them, and the document stays.
All calls come from the event loop, so there is no lock.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

_Key = tuple[str, str]


class UploadTracker:
    def __init__(self, ttl_sec: float = 3600.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_sec
        self._clock = clock
        self._cancelled: dict[_Key, float] = {}
        self._finished: dict[_Key, tuple[str, float]] = {}

    def _prune(self) -> None:
        cutoff = self._clock() - self._ttl
        self._cancelled = {k: t for k, t in self._cancelled.items() if t >= cutoff}
        self._finished = {k: v for k, v in self._finished.items() if v[1] >= cutoff}

    def cancel(self, user_email: str, upload_id: str) -> Optional[str]:
        """Record a cancel. Returns the document to remove if the upload finished."""
        self._prune()
        key = (user_email, upload_id)
        finished = self._finished.pop(key, None)
        if finished:
            return finished[0]
        self._cancelled[key] = self._clock()
        return None

    def is_cancelled(self, user_email: str, upload_id: str) -> bool:
        return (user_email, upload_id) in self._cancelled

    def finish(self, user_email: str, upload_id: str, document_id: str) -> None:
        self._prune()
        self._finished[(user_email, upload_id)] = (document_id, self._clock())
