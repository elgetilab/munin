"""Mechanical checks over a replayed case. Pure functions except dois_resolve.

A record is the dict the runner writes per case:
    {"turns": [{"answer", "tools", "error", "seconds"}, ...],
     "artifacts": [{"id", "filename", "turn"}, ...], "error": ...}
Checks look at the final turn unless their docstring says otherwise.
"""

from __future__ import annotations

import re
import urllib.request
from typing import Callable, Optional

ERROR_MARKERS = re.compile(
    r"stream interrupted|vLLM (returned|produced|unreachable)|Traceback \(most recent call last\)"
    r"|\[backend warning\]|maximum context length|produced no output",
    re.IGNORECASE)
ARTIFACT_LINK = re.compile(r"/api/artifacts/(?:[\w-]+/)?([\w-]{8,})")
# Same rules as backend/retrieval/citecheck.py (_DOI_RE, _trim_doi): old
# Elsevier DOIs contain parentheses (10.1016/0022-2836(91)90127-X), kept unless
# unbalanced at the end; old Wiley SICI DOIs contain <...>; publisher URL tails
# (#Sec2, ?via=ihub, .full, bioRxiv v2) are not part of the DOI.
DOI = re.compile(r"\b10\.\d{4,9}/\(SICI\)[^\s\"'\[\]`{}|\\]+|\b10\.\d{4,9}/[^\s\"'<>\[\]`{}|\\]+",
                 re.IGNORECASE)
_URL_TAIL = re.compile(
    r"(?:[#?].*|/-/DC\w*.*|\.full\.pdf|\.full|\.abstract|\.pdf|\.html?|/full|/abstract|/pdf|/epdf|/fulltext)$",
    re.IGNORECASE)
_BIORXIV_VERSION = re.compile(r"^(10\.1101/.*\d{6,})v\d+$", re.IGNORECASE)


def extract_dois(text: str) -> list[str]:
    out = set()
    for d in DOI.findall(text or ""):
        while True:
            d0 = d
            d = _BIORXIV_VERSION.sub(r"\1", _URL_TAIL.sub("", d))
            d = d.rstrip(".,;:*_~^")
            depth = 0   # the first ")" that closes nothing ends the DOI (citecheck._cut_unopened_paren)
            for i, ch in enumerate(d):
                depth += (ch == "(") - (ch == ")")
                if depth < 0:
                    d = d[:i]
                    break
            if d == d0:
                break
        out.add(d)
    return sorted(out)
CLARIFY_TOOL = "ask_clarification"

_DE = {"der", "die", "das", "und", "ist", "nicht", "mit", "für", "sie", "ein", "eine", "auf", "auch", "werden"}
_EN = {"the", "and", "is", "not", "with", "for", "you", "a", "an", "on", "also", "are", "this", "that"}

Check = Callable[[dict, dict], tuple[bool, str]]
_REGISTRY: dict[str, Check] = {}
_doi_cache: dict[str, bool] = {}


def check(name: str):
    def deco(fn: Check) -> Check:
        _REGISTRY[name] = fn
        return fn
    return deco


def _final(rec: dict) -> dict:
    return rec["turns"][-1] if rec.get("turns") else {"answer": "", "tools": [], "seconds": 0}


@check("nonempty")
def _nonempty(c, rec):
    ans = (_final(rec).get("answer") or "").strip()
    return bool(ans), f"{len(ans)} chars"


@check("no_error_marker")
def _no_error(c, rec):
    """Every turn: no transport error, no empty answer, no error marker text."""
    if rec.get("error"):
        return False, f"run error: {rec['error'][:120]}"
    for i, t in enumerate(rec.get("turns") or [], 1):
        if t.get("error"):
            return False, f"turn {i} error: {t['error'][:120]}"
        if not (t.get("answer") or "").strip():
            return False, f"turn {i} empty"
        m = ERROR_MARKERS.search(t.get("answer") or "")
        if m:
            return False, f"turn {i} marker: {m.group(0)}"
    return True, ""


@check("no_phantom_artifact")
def _no_phantom(c, rec):
    """Every turn: artifact links in answers point at artifacts the run created."""
    made = {a["id"] for a in rec.get("artifacts") or []}
    for i, t in enumerate(rec.get("turns") or [], 1):
        for aid in ARTIFACT_LINK.findall(t.get("answer") or ""):
            if not any(aid.startswith(m) or m.startswith(aid) for m in made):
                return False, f"turn {i} links unknown artifact {aid}"
    return True, ""


@check("tool_used")
def _tool_used(c, rec):
    tools = [x for t in rec.get("turns") or [] for x in t.get("tools") or []]
    return c["name"] in tools, f"tools: {sorted(set(tools))}"


@check("tool_not_used")
def _tool_not_used(c, rec):
    tools = [x for t in rec.get("turns") or [] for x in t.get("tools") or []]
    return c["name"] not in tools, f"tools: {sorted(set(tools))}"


@check("artifact")
def _artifact(c, rec):
    ext = c["ext"].lower()
    names = [a.get("filename", "") for a in rec.get("artifacts") or []]
    return any(n.lower().endswith(ext) for n in names), f"artifacts: {names[:8]}"


@check("dois_resolve")
def _dois(c, rec):
    dois = extract_dois(_final(rec).get("answer") or "")
    bad = [d for d in dois if not doi_exists(d)]
    return not bad, (f"unresolved: {bad[:5]}" if bad else f"{len(dois)} DOIs ok")


@check("contains")
def _contains(c, rec):
    ok = re.search(c["pattern"], _final(rec).get("answer") or "", re.IGNORECASE) is not None
    return ok, "" if ok else f"missing /{c['pattern']}/"


@check("not_contains")
def _not_contains(c, rec):
    m = re.search(c["pattern"], _final(rec).get("answer") or "", re.IGNORECASE)
    return m is None, "" if m is None else f"found {m.group(0)[:60]!r}"


@check("language")
def _language(c, rec):
    got = detect_language(_final(rec).get("answer") or "")
    return got == c["lang"], f"detected {got}"


@check("no_clarification")
def _no_clar(c, rec):
    t = _final(rec)
    if CLARIFY_TOOL in (t.get("tools") or []):
        return False, "asked via ask_clarification"
    tail = (t.get("answer") or "").strip()[-300:]
    asks = tail.endswith("?") and len((t.get("answer") or "").strip()) < 1500
    return not asks, "short answer ending in a question" if asks else ""


@check("max_words")
def _max_words(c, rec):
    n = len((_final(rec).get("answer") or "").split())
    return n <= int(c["n"]), f"{n} words"


@check("max_seconds")
def _max_seconds(c, rec):
    s = float(_final(rec).get("seconds") or 0)
    return s <= float(c["n"]), f"{s:.0f}s"


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    de = sum(w in _DE for w in words)
    en = sum(w in _EN for w in words)
    return "de" if de > en else "en"


def doi_exists(doi: str, timeout: float = 10.0) -> bool:
    """doi.org handle lookup; sends the DOI only. Cached per process."""
    if doi in _doi_cache:
        return _doi_cache[doi]
    url = "https://doi.org/api/handles/" + urllib.request.quote(doi, safe="/")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            ok = r.status == 200
    except urllib.error.HTTPError as exc:
        ok = False if exc.code == 404 else True   # unknown errors do not fail the case
    except OSError:
        ok = True
    _doi_cache[doi] = ok
    return ok


def run_checks(checks: list[dict], rec: dict) -> list[dict]:
    out = []
    for c in checks:
        fn: Optional[Check] = _REGISTRY.get(c["type"])
        if fn is None:
            out.append({"type": c["type"], "pass": False, "detail": "unknown check"})
            continue
        ok, detail = fn(c, rec)
        out.append({"type": c["type"], "pass": bool(ok), "detail": detail})
    return out


@check("min_dois")
def _min_dois(c, rec):
    dois = extract_dois(_final(rec).get("answer") or "")
    return len(dois) >= int(c["n"]), f"{len(dois)} DOIs"


@check("dois_grounded")
def _dois_grounded(c, rec):
    """Every DOI in the final answer appears in a tool result of the run (any
    turn) or in a prompt: it was copied, not recalled. Needs records that keep
    tool results (runner since 2026-11-24)."""
    seen = " ".join(r.get("result") or "" for t in rec.get("turns") or []
                    for r in t.get("tool_results") or []).lower()
    seen += " " + " ".join(rec.get("prompts") or []).lower()
    dois = extract_dois(_final(rec).get("answer") or "")
    loose = [d for d in dois if d.lower() not in seen]
    return not loose, (f"not in any tool result: {loose[:5]}" if loose else f"{len(dois)} DOIs grounded")


@check("citations_clean")
def _citations_clean(c, rec):
    """No [unverified] mark in the answer and no citecheck problem: every DOI
    exists and its authors, year, journal, pages and title match the record."""
    ans = _final(rec).get("answer") or ""
    if "[unverified:" in ans:
        return False, "answer carries an [unverified] mark"
    import asyncio
    cc = _citecheck()
    units = asyncio.run(cc.verify(ans))
    bad = [(u.doi, u.problems[0][:80]) for u in units if u.problems]
    return not bad, (f"{len(bad)} bad: {bad[:3]}" if bad else f"{len(units)} citations clean")


def _citecheck():
    import sys
    from pathlib import Path
    retrieval = str(Path(__file__).resolve().parents[3] / "retrieval")
    if retrieval not in sys.path:
        sys.path.insert(0, retrieval)
    import citecheck
    return citecheck

