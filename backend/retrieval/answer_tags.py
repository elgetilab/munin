"""Strip stray ``<answer>`` tags and collapse looped paragraphs in replies.

Sage-A1.5 sometimes wraps its reply in literal ``<answer>`` / ``</answer>``
tags (no prompt or chat template asks for them) and, when it loops, repeats
the same paragraph or block several times in a row (lab smoke s3_bars turns
5 and 7, docs/agent-track/LAB-BUGFIX-PLAN.md F5). Nothing downstream renders
the tags, and the next turn copies whatever the history shows, so the habit
compounds.

Two entry points:

- ``clean_reply(text)`` for a finished reply (persistence, history): drops
  the tags outside fenced code blocks and collapses consecutive repeats of a
  paragraph or a block of paragraphs to one copy. Returns the counts so the
  caller can log them. ``strip_answer_tags(text)`` is the text-only wrapper.
- ``AnswerTagStreamFilter`` for the live token stream: drops the tags as they
  stream, holding back a trailing ``<...`` partial until it is known not to
  be a tag. It tracks code fences by line, so a tag inside a fence streams
  unchanged. It does not collapse repeats (that needs the whole reply); the
  persisted copy is authoritative.
"""

from __future__ import annotations

import re
from typing import Optional

_TAGS = ("<answer>", "</answer>")
_TAG_RE = re.compile(r"</?answer\s*>", re.IGNORECASE)
_PARA_SPLIT_RE = re.compile(r"\n[ \t]*\n")
# Longest run of paragraphs treated as one repeated block (ABAB...).
_MAX_BLOCK = 8


def _fence_marker(line: str) -> Optional[str]:
    s = line.lstrip()
    if s.startswith("```"):
        return "```"
    if s.startswith("~~~"):
        return "~~~"
    return None


def _segments(text: str) -> list[tuple[bool, str]]:
    """Split ``text`` into (is_code, chunk) by fenced code blocks.

    A code chunk runs from its opening fence line through the closing one
    (or to the end of the text when the fence is never closed).
    """
    out: list[tuple[bool, str]] = []
    buf: list[str] = []
    fence: Optional[str] = None
    for line in text.splitlines(keepends=True):
        marker = _fence_marker(line)
        if fence is None:
            if marker is not None:
                if buf:
                    out.append((False, "".join(buf)))
                buf = [line]
                fence = marker
            else:
                buf.append(line)
        else:
            buf.append(line)
            if marker == fence:
                out.append((True, "".join(buf)))
                buf = []
                fence = None
    if buf:
        out.append((fence is not None, "".join(buf)))
    return out


_MIN_REPEAT_CHARS = 40


def _collapse_runs(units: list[str]) -> tuple[list[str], int]:
    """Collapse consecutive repeats of a unit or a block of units.

    Returns the collapsed list and how many repeated copies were removed.
    """
    keys = [u.strip() for u in units]
    out: list[str] = []
    removed = 0
    i = 0
    n = len(units)
    while i < n:
        done = False
        for k in range(1, min(_MAX_BLOCK, (n - i) // 2) + 1):
            block = keys[i:i + k]
            if keys[i + k:i + 2 * k] != block:
                continue
            # Short repeats ("---" twice, a lone "Output:") are formatting,
            # not a loop; only a block with some substance counts.
            if sum(len(b) for b in block) < _MIN_REPEAT_CHARS:
                continue
            j = i + k
            while keys[j:j + k] == block:
                j += k
                removed += 1
            out.extend(units[i:i + k])
            i = j
            done = True
            break
        if not done:
            out.append(units[i])
            i += 1
    return out, removed


def clean_reply(text: str) -> tuple[str, int, int]:
    """Return (cleaned_text, tags_stripped, repeats_collapsed).

    Text with neither tags nor repeats comes back unchanged (byte for byte).
    Never raises: on any internal error the input is returned as is.
    """
    if not text or not isinstance(text, str):
        return text, 0, 0
    try:
        units: list[str] = []
        n_tags = 0
        for is_code, chunk in _segments(text):
            if is_code:
                units.append(chunk.strip("\n"))
                continue
            chunk, n = _TAG_RE.subn("", chunk)
            n_tags += n
            for para in _PARA_SPLIT_RE.split(chunk):
                para = para.strip("\n").rstrip()
                if para.strip():
                    units.append(para)
        collapsed, n_rep = _collapse_runs(units)
        if not n_tags and not n_rep:
            return text, 0, 0
        return "\n\n".join(collapsed), n_tags, n_rep
    except Exception:  # a cleanup must never break a turn
        return text, 0, 0


def strip_answer_tags(text: str) -> str:
    """``clean_reply`` without the counts."""
    return clean_reply(text)[0]


def has_repeats(text: str) -> bool:
    """True when ``text`` (after dropping tags) repeats a paragraph or block."""
    return clean_reply(text)[2] > 0


class AnswerTagStreamFilter:
    """Drop ``<answer>`` / ``</answer>`` from streamed content.

    ``feed`` returns what is safe to show now; ``flush`` returns any held-back
    partial at the end of the stream (a ``<ans`` that never completed is
    ordinary text). Tags inside fenced code blocks pass through.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._in_fence = False
        self._fence: Optional[str] = None
        self._line = ""  # start of the current output line, for fence checks
        self.stripped = 0

    def _track(self, piece: str) -> None:
        for c in piece:
            if c == "\n":
                marker = _fence_marker(self._line)
                if marker is not None:
                    if not self._in_fence:
                        self._in_fence, self._fence = True, marker
                    elif marker == self._fence:
                        self._in_fence, self._fence = False, None
                self._line = ""
            elif len(self._line) < 16:
                self._line += c

    def feed(self, chunk: str) -> str:
        s = self._pending + (chunk or "")
        self._pending = ""
        out: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            lt = s.find("<", i)
            if lt < 0:
                piece = s[i:]
                out.append(piece)
                self._track(piece)
                break
            if lt > i:
                piece = s[i:lt]
                out.append(piece)
                self._track(piece)
                i = lt
            if self._in_fence:
                out.append("<")
                self._track("<")
                i += 1
                continue
            head = s[i:i + 9].lower()
            tag = next((t for t in _TAGS if head.startswith(t)), None)
            if tag is not None:
                self.stripped += 1
                i += len(tag)
                continue
            rest = s[i:].lower()
            if any(t.startswith(rest) for t in _TAGS):
                self._pending = s[i:]  # could still become a tag
                break
            out.append("<")
            self._track("<")
            i += 1
        return "".join(out)

    def flush(self) -> str:
        out, self._pending = self._pending, ""
        self._track(out)
        return out


# Live loop guard (LAB-BUGFIX-PLAN.md F9). One step of s3_bars#3 streamed
# 80,496 characters of the same few paragraphs with no tool call; the saved
# copy was collapsed (2128 repeats) but the user watched it stream and the
# loop text filled two thirds of the context. The stream is cut once its tail
# has repeated LOOP_MIN_COPIES times: the last LOOP_TAIL_CHARS characters
# occurring that often within the last LOOP_WINDOW_CHARS. Legitimate text
# practically never repeats a 300-character stretch three times in a row.
LOOP_TAIL_CHARS = 300
LOOP_WINDOW_CHARS = 6000
LOOP_MIN_COPIES = 3


def tail_is_looping(text: str) -> bool:
    """True when the end of ``text`` is a loop (see the constants above)."""
    if not text or len(text) < LOOP_TAIL_CHARS * LOOP_MIN_COPIES:
        return False
    tail = text[-LOOP_TAIL_CHARS:]
    if not tail.strip():
        return False
    return text[-LOOP_WINDOW_CHARS:].count(tail) >= LOOP_MIN_COPIES


def cut_loop(text: str) -> str:
    """Text of a stream cut for looping: repeated blocks collapsed, and when
    the loop had no paragraph breaks, the text up to the first repeat of the
    looping tail."""
    cleaned, _, _ = clean_reply(text)
    if not tail_is_looping(cleaned):
        return cleaned
    tail = cleaned[-LOOP_TAIL_CHARS:]
    first = cleaned.find(tail)
    if first < 0:
        return cleaned
    # Keep one copy of the repeating unit: everything before the second
    # occurrence of the tail.
    second = cleaned.find(tail, first + 1)
    return cleaned[:second] if second > 0 else cleaned
