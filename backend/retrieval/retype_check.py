"""Arrays typed into code that start like an uploaded file and then depart.

LABKIT-018-PLAN.md, change 1. The model copies the rows it was shown (the
upload preview, or `head(10)`) into an array literal and invents the rest:
16 of 2,019 lab bundles held such a literal, 15 right after the rows the
model had seen, and every one that reached the final run failed. Faithful
copies all passed. Uploads keep their numeric columns here; each run's
bracketed literals of 10 or more numbers are lined up against them, and a
literal that matches 5 or more positions and then differs (3 or more
positions off by more than 0.2%, or running past the end of the file) gets
an advisory hint naming the column, the file and how long the file is.

Pure apart from `RetypeState`, which keeps the columns per conversation in
this process. It is bounded, and after a restart it is empty: the hint then
just does not fire.
"""
from __future__ import annotations

import io
import re
import zipfile
from array import array
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Optional

# Files above this are not kept (a 3 MB text export is about 400k numbers).
MAX_FILE_BYTES = 3_000_000
MIN_LITERAL = 10     # numbers in a bracketed literal before it is checked
MIN_MATCH = 5        # exact matches at one alignment
MIN_DIFFER = 3       # positions off by more than REL_DIFF, or past the file
REL_DIFF = 2e-3

_NUM = re.compile(r"(?<![\w.])[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?(?![\w.])")
_BRACKET = re.compile(r"\[[^\[\]]*\]")
_ONLY_NUMBERS = re.compile(r"^[\s,0-9eE.+\-]*$")
_COMMENT = re.compile(r"#[^\n]*")
_SPLIT = re.compile(r"\s*[,\t;]\s*|\s+")


def _nums(s: str) -> list[float]:
    return [float(m) for m in _NUM.findall(s)]


def literals(code: str, min_len: int = MIN_LITERAL) -> list[list[float]]:
    """Bracketed literals holding only numbers, at least `min_len` of them."""
    out = []
    for m in _BRACKET.finditer(code or ""):
        body = _COMMENT.sub("", m.group(0)[1:-1])
        if _ONLY_NUMBERS.match(body):
            v = _nums(body)
            if len(v) >= min_len:
                out.append(v)
    return out


@dataclass
class Column:
    file: str        # file name as the user knows it
    path: str        # where code reads it ('inputs/thurber.csv')
    name: str        # header name, or 'column 2'
    values: array


def parse_columns(text: str) -> list[tuple[str, list[float]]]:
    """(name, values) for each column of the numeric rows of a text table:
    the rows with the most common count of numbers, their header taken from
    the last line without numbers before the first such row."""
    lines = text.splitlines()
    rows: list[tuple[int, list[float]]] = []
    for i, line in enumerate(lines):
        v = _nums(line)
        if v:
            rows.append((i, v))
    if not rows:
        return []
    width = Counter(len(v) for _, v in rows).most_common(1)[0][0]
    kept = [(i, v) for i, v in rows if len(v) == width]
    first = kept[0][0]
    names: list[str] = []
    for line in reversed(lines[:first]):
        if line.strip() and not _nums(line):
            parts = [p.strip().strip("\"'") for p in _SPLIT.split(line.strip()) if p.strip()]
            if len(parts) == width:
                names = parts
            break
        if line.strip():
            break
    if not names:
        names = [f"column {j + 1}" for j in range(width)]
    return [(names[j], [v[j] for _, v in kept]) for j in range(width)]


def _text_of(data: bytes) -> Optional[str]:
    if not data or len(data) > MAX_FILE_BYTES or b"\0" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def file_columns(filename: str, data: bytes, paths: list[str]) -> list[Column]:
    """The numeric columns of an uploaded text file, or of the text files in
    an uploaded .zip. Binary files and files above MAX_FILE_BYTES give none."""
    members: list[tuple[str, bytes]] = []
    if data[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for info in zf.infolist():
                    if not info.is_dir() and info.file_size <= MAX_FILE_BYTES:
                        members.append((info.filename, zf.read(info)))
        except (zipfile.BadZipFile, OSError, RuntimeError):
            return []
    else:
        members.append((filename, data))
    out: list[Column] = []
    for name, blob in members:
        text = _text_of(blob)
        if text is None:
            continue
        base = name.rsplit("/", 1)[-1]
        path = next((p for p in paths if p.rsplit("/", 1)[-1] == base), None)
        path = path or (paths[0] if len(paths) == 1 else f"inputs/{base}")
        for col, values in parse_columns(text):
            if len(values) >= MIN_MATCH:
                out.append(Column(base, path, col, array("d", values)))
    return out


@dataclass
class Finding:
    column: Column
    first_row: int   # 1-based, the longest run of exact matches
    last_row: int
    then_differs: bool  # the departure comes after that run

    @property
    def key(self) -> tuple[str, str]:
        return (self.column.path, self.column.name)

    def sentence(self) -> str:
        c = self.column
        how = "and then differs" if self.then_differs else "but differs elsewhere"
        rows = (f"row {self.first_row}" if self.first_row == self.last_row
                else f"rows {self.first_row}-{self.last_row}")
        return f"This array matches {rows} of `{c.name}` in {c.file} {how}; the file has {len(c.values)} rows."


def _best_alignment(lit: list[float], col: array) -> tuple[int, int]:
    """(exact matches, offset) of the offset with the most exact matches;
    offset o puts lit[k] against col[o + k]."""
    where: dict[float, list[int]] = {}
    for k, x in enumerate(lit):
        where.setdefault(x, []).append(k)
    votes: Counter = Counter()
    for i, x in enumerate(col):
        ks = where.get(x)
        if ks:
            for k in ks:
                votes[i - k] += 1
    if not votes:
        return 0, 0
    off, eq = min(votes.items(), key=lambda kv: (-kv[1], abs(kv[0])))
    return eq, off


def check_literal(lit: list[float], columns: list[Column]) -> Optional[Finding]:
    """A Finding when `lit` lines up with one of `columns` and then departs."""
    best: Optional[tuple[int, int, Column]] = None
    for col in columns:
        eq, off = _best_alignment(lit, col.values)
        if best is None or eq > best[0]:
            best = (eq, off, col)
    if best is None or best[0] < MIN_MATCH:
        return None
    eq, off, col = best
    n = len(col.values)
    same = []
    far = 0
    for k, x in enumerate(lit):
        j = off + k
        if j < 0 or j >= n:
            far += 1            # runs past the file
            same.append(False)
            continue
        y = col.values[j]
        same.append(x == y)
        if abs(x - y) > REL_DIFF * max(abs(y), 1e-12):
            far += 1
    if far < MIN_DIFFER:
        return None
    # Longest run of exact matches: the rows the model had.
    run_start = run_len = 0
    cur_start = cur_len = 0
    for k, s in enumerate(same):
        if s:
            if cur_len == 0:
                cur_start = k
            cur_len += 1
            if cur_len > run_len:
                run_start, run_len = cur_start, cur_len
        else:
            cur_len = 0
    return Finding(col, off + run_start + 1, off + run_start + run_len, run_start == 0)


def check(code: str, columns: list[Column]) -> list[Finding]:
    """Findings for every literal in `code`, at most one per (file, column)."""
    if not columns or not isinstance(code, str):
        return []
    out: list[Finding] = []
    seen = set()
    for lit in literals(code):
        f = check_literal(lit, columns)
        if f and f.key not in seen:
            seen.add(f.key)
            out.append(f)
    return out


def hint_text(findings: list[Finding]) -> str:
    paths = ", ".join(repr(p) for p in dict.fromkeys(f.column.path for f in findings))
    return (" ".join(f.sentence() for f in findings)
            + f" Read the file instead of typing values ({paths}); the kernel state "
            "persists, so data read in an earlier run is still loaded.")


class RetypeState:
    """Numeric columns of each conversation's uploads, in this process.

    Bounded by conversations and by numbers kept in all; the least recently
    used conversation goes first. Losing an entry only silences the hint."""

    def __init__(self, max_conversations: int = 256, max_numbers: int = 4_000_000) -> None:
        self._cols: "OrderedDict[str, list[Column]]" = OrderedDict()
        self._fired: dict[str, set] = {}
        self._max_conv = max_conversations
        self._max_numbers = max_numbers

    def _size(self) -> int:
        return sum(len(c.values) for cols in self._cols.values() for c in cols)

    def remember(self, conv_id: str, filename: str, data: bytes, paths: list[str]) -> int:
        """Keep the columns of an uploaded file; returns how many were kept."""
        if not conv_id or not isinstance(data, (bytes, bytearray)):
            return 0
        new = file_columns(filename or "", bytes(data), list(paths or []))
        if not new:
            return 0
        cols = self._cols.pop(conv_id, [])
        replaced = {c.path for c in new}
        cols = [c for c in cols if c.path not in replaced] + new
        self._cols[conv_id] = cols
        while len(self._cols) > self._max_conv or (len(self._cols) > 1 and self._size() > self._max_numbers):
            old, _ = self._cols.popitem(last=False)
            self._fired.pop(old, None)
        return len(new)

    def new_turn(self, conv_id: str) -> None:
        """A new user message: each (file, column) may fire again once."""
        self._fired.pop(conv_id, None)

    def hint_for(self, conv_id: str, code: str) -> Optional[str]:
        cols = self._cols.get(conv_id)
        if not cols:
            return None
        self._cols.move_to_end(conv_id)
        fired = self._fired.setdefault(conv_id, set())
        found = [f for f in check(code, cols) if f.key not in fired]
        if not found:
            return None
        fired.update(f.key for f in found)
        return hint_text(found)
