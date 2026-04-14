"""
Output truncation for kernel stdout / stderr.

We cap each stream at 64 KB per execution so that:
  - the chat_store messages row stays bounded,
  - the SSE stream doesn't have to ferry megabytes to the browser,
  - the model's next-turn context isn't blown out by `print(big_array)`.

When the cap fires, the truncated stream ends with a clear marker the
model can read and react to ("oh, I should print less / save to a file
instead"). The byte count of dropped output is included so it isn't a
silent loss.
"""

from __future__ import annotations

DEFAULT_CAP_BYTES = 64 * 1024


class CappedBuffer:
    """
    Append-only string buffer with a hard byte cap. After the cap is hit,
    further writes are counted but discarded; the final value carries a
    truncation marker.
    """

    __slots__ = ("_chunks", "_size", "_cap", "_dropped")

    def __init__(self, cap_bytes: int = DEFAULT_CAP_BYTES) -> None:
        self._chunks: list[str] = []
        self._size: int = 0
        self._cap: int = max(1024, int(cap_bytes))
        self._dropped: int = 0

    def write(self, text: str) -> None:
        if not text:
            return
        # Approximate: len(str) ~= bytes for ASCII; for unicode the exact
        # byte count would need encode('utf-8'). We err on the side of
        # over-counting so we never overflow the cap.
        size = len(text)
        if self._size >= self._cap:
            self._dropped += size
            return
        room = self._cap - self._size
        if size <= room:
            self._chunks.append(text)
            self._size += size
            return
        # Partial fit: keep whatever fits, drop the rest.
        self._chunks.append(text[:room])
        self._size += room
        self._dropped += size - room

    @property
    def dropped(self) -> int:
        return self._dropped

    @property
    def truncated(self) -> bool:
        return self._dropped > 0

    def value(self) -> str:
        body = "".join(self._chunks)
        if self._dropped:
            body += f"\n[truncated, {self._dropped} more bytes]"
        return body
