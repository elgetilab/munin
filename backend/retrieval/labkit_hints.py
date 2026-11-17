"""Pointers to `labkit` at the moment the model needs it (LAB-ADOPTION-PLAN.md).

Phase 4 run 1 found the model hand-rolling `scipy.optimize` fits in 199 of
228 S1 runs although the persona named `labkit`; the failures it then made
are conventions `labkit` encodes (one-phase decay Y0 reported as the drop).
A pointer in the tool result, where the model decides its next step, is what
moved `edit_python` adoption; this is the same lever.

Two hints, both narrow:
- `fit_hint(code)`: a run_python script that fits with curve_fit,
  least_squares or lmfit a model `labkit.fit` has (exponential decay or
  association, 3/4/5PL dose response, Michaelis-Menten, Morrison, competitive
  inhibition), or reads a Bruker experiment by hand (nmrglue or raw bytes),
  and does not use `labkit`. General optimisation is left alone: a fit call
  alone is not enough.
- `upload_hint(filename, data, paths)`: an attached file in a format
  `labkit.read` or `labkit.nmr.read_bruker` knows, sniffed the way
  `labkit.read` sniffs it (backend/sandbox/labkit/labkit/read.py).

`HintState` keeps each hint to once per conversation, and stops hinting once
the conversation's code has used `labkit`.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from typing import Optional

_FIT_CALL = re.compile(r"\b(curve_fit|least_squares|lmfit|Minimizer)\b")

# (key, what the code looks like, labkit model names). Checked in order; the
# first match names the model in the hint.
_MODELS: list[tuple[str, re.Pattern, str]] = [
    ("morrison", re.compile(r"morrison", re.I), '"morrison" (constants={"Et": ...})'),
    ("competitive", re.compile(r"competitive|\bKi\b.*\bKm\b|\bKm\b.*\bKi\b", re.S), '"competitive_inhibition"'),
    ("michaelis_menten", re.compile(r"michaelis|\bvmax\b|\bv_?max\b|\bKm\b", re.I), '"michaelis_menten"'),
    ("dose_response", re.compile(
        r"hill|logec50|log_?ec50|\bec50\b|\bic50\b|\b[345]\s*-?\s*pl\b|logistic", re.I),
     '"3pl", "4pl" or "5pl" (x_scale="log" or "linear" is required)'),
    ("exponential", re.compile(r"\bexp\s*\(\s*-|half_?life|plateau", re.I),
     '"one_phase_decay", "two_phase_decay" or "one_phase_association"'),
]
# nmrglue on Bruker data, or any code touching a Bruker experiment folder
# (in Phase 4 run 1 the model read the FID with np.fromfile as int16, 3/3).
_BRUKER = re.compile(r"\bng\.bruker\b|nmrglue\.bruker|\bacqus\b|[/'\"]fid['\"]")
_USES_LABKIT = re.compile(r"\b(import\s+labkit|from\s+labkit\b|labkit\.)")


def fit_hint(code: str) -> Optional[tuple[str, str]]:
    """(key, hint) when `code` hand-rolls something labkit has, else None."""
    if not isinstance(code, str) or _USES_LABKIT.search(code):
        return None
    if _BRUKER.search(code):
        return ("bruker", (
            "This reads Bruker data by hand. labkit.nmr has the "
            "processing chain (read_bruker takes the data type from DTYPA, "
            "process_1d, reference, peaks, integrate); see labkit.guide('nmr'). "
            "Keep your own code for steps labkit.nmr does not cover."))
    if not _FIT_CALL.search(code):
        return None
    for key, pat, names in _MODELS:
        if pat.search(code):
            return (key, (
                f"This fits by hand a model labkit.fit has: {names}, with "
                "GraphPad Prism's parameter names and definitions (for example "
                "Y0 is the value at X=0, not the drop), Wald CIs and warnings "
                "for ambiguous fits: r = labkit.fit(model, x, y); print(r); "
                "see labkit.guide('fit'). Keep your own code if the model is "
                "not one of these."))
    return None


class HintState:
    """Which hints a conversation has had; bounded so it cannot grow forever."""

    def __init__(self, max_conversations: int = 2000) -> None:
        self._seen: "OrderedDict[str, set[str]]" = OrderedDict()
        self._max = max_conversations

    def _entry(self, conv_id: str) -> set[str]:
        entry = self._seen.pop(conv_id, None) or set()
        self._seen[conv_id] = entry
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return entry

    def hint_for(self, conv_id: str, code: str) -> Optional[str]:
        """The hint for this run, or None (already given, or labkit in use)."""
        entry = self._entry(conv_id)
        if isinstance(code, str) and _USES_LABKIT.search(code):
            entry.add("uses_labkit")
        if "uses_labkit" in entry:
            return None
        found = fit_hint(code)
        if found is None or found[0] in entry:
            return None
        entry.add(found[0])
        return found[1]


# --- uploads ------------------------------------------------------------------

_BINARY_EXPORTS = {
    ".pcrd": "a Bio-Rad CFX run file",
    ".sda": "a SoftMax Pro document",
}


def _decode_head(data: bytes) -> str:
    head = data[:8192]
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return head.decode("utf-16", errors="replace")
    if len(head) > 8 and head[1::2].count(0) > 0.4 * (len(head) // 2):
        return head.decode("utf-16-le", errors="replace")
    return head.decode("utf-8", errors="replace").lstrip("﻿")


def upload_hint(filename: str, data: bytes, paths: list[str]) -> str:
    """One sentence naming the labkit reader for a known lab format, or ''."""
    name = (filename or "").lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if any(p.rsplit("/", 1)[-1] in ("acqus", "fid", "ser") for p in paths):
        parts = paths[0].split("/")
        target = "/".join(parts[:2]) if len(parts) > 2 else "inputs"
        return (f"It is Bruker NMR data: labkit.nmr.read_bruker({target!r}) reads it "
                "(see labkit.guide('nmr')).")
    if ext in _BINARY_EXPORTS:
        return (f"It is {_BINARY_EXPORTS[ext]}, a binary format: labkit.read refuses it "
                "and says which export to ask the user for.")
    if ext in (".xlsx", ".xlsm"):
        return "If it is a plate or instrument export, labkit.read(path) reads it (see labkit.guide('read'))."
    if ext not in (".txt", ".csv", ".tsv", ".dat", ".asc", ""):
        return ""
    head = _decode_head(data)
    up = head.upper()
    if "XYDATA" in up and ("JASCO" in up or "YUNITS" in up):
        return "It is a JASCO spectrum export: labkit.read_jasco(path) reads it with its units."
    if re.search(r"^Time\t", head, re.M) and ("Reader Type" in head or "Software Version" in head):
        return "It is a BioTek Gen5 export: labkit.read_gen5(path) reads it (times in minutes, OVER wells reported)."
    if "A260/A280" in head or "Nucleic Acid" in head:
        return "It is a NanoDrop export: labkit.read_nanodrop(path) reads it in any locale or encoding."
    return ""
