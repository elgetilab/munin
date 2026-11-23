"""
Citation verification (docs/agent-track/CITATION-VERIFY-PLAN.md).

The three post-turn audits in chat_service only annotate. This module checks
the citations themselves: every DOI in the final answer is looked up in the
DOI registry, and the first author, year, journal and pages written next to
it are compared with the record. Then, by MUNIN_CITECHECK:

    off      nothing
    warn     verify; mark citations that fail with [unverified] (default)
    enforce  verify; one correction pass on the backbone fixes them from the
             records; whatever still fails is marked [unverified]

Lookups are verify-only (Crossref, then the doi.org handle API, both keyless
and quota-free) and add no content to the answer, so they run under every
egress level. MUNIN_CITECHECK=off is the switch for an air-gapped install.

Every check is conservative. A false flag puts [unverified] on a correct
citation, which costs more trust than a missed one; a lookup that times out
or errors verifies nothing and flags nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

CROSSREF = "https://api.crossref.org/works/"
DOI_HANDLES = "https://doi.org/api/handles/"
LOOKUP_TIMEOUT_S = 5.0
# Typical turns finish the lookups in about a second; the cap only bounds a
# slow registry. 8 s cut off real errors during calibration.
OVERALL_TIMEOUT_S = 15.0
MAX_DOIS = 40
# Crossref's limits: the polite pool (a mailto in the User-Agent, which
# production sends) allows 3 concurrent requests, the anonymous public pool 1.
# Above that it answers 429, which verifies nothing.
POLITE_CONCURRENCY = 3
# Above this the correction pass is skipped (marks only): rewriting a very
# long answer costs a long call and risks truncation.
MAX_CORRECT_CHARS = 40_000

MODES = ("off", "warn", "enforce")


def mode() -> str:
    m = (os.environ.get("MUNIN_CITECHECK") or "warn").strip().lower()
    return m if m in MODES else "warn"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------
# Old Elsevier DOIs contain parentheses (10.1016/0022-2836(91)90127-X), so
# they are allowed inside and trimmed only when unbalanced at the end. Same
# rule as munin_bench/userfail/checks.py::extract_dois.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>\[\]`{}|\\]+", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+")
_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[0-3]\d)(?!\d)")
# Pages only in citation position: "558:309-314", ", 721-723", "pp. 7-16".
_PAGES_RE = re.compile(r"(?:[:,]\s*|\bpp?\.\s*)(\d{1,6})\s*[–—-]\s*(\d{1,6})(?!\d|\.\d)")
_ET_AL_RE = re.compile(r"([A-ZÀ-ÖØ-Þ][\w'\-]+(?:\s+[A-ZÀ-ÖØ-Þ][\w'\-]*)?)\s+et\s+al\.", re.UNICODE)
_LIST_MARK_RE = re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)]|\[\^?\d{1,3}\^?\]:?|\(\d{1,3}\))\s*")
# "Lagane B," (Vancouver) / "Lagane, B." (APA) / "Lagane, Bernard" at the start
# of a reference-list item.
_LEAD_AUTHOR_RE = re.compile(
    r"^\**([A-ZÀ-ÖØ-Þ][\w'\-]+(?:[ -][A-ZÀ-ÖØ-Þ][\w'\-]+)?)(?:,\s*[A-ZÀ-ÖØ-Þ]|\s+[A-Z]{1,3}[,.])",
    re.UNICODE)
_ITALIC_RE = re.compile(r"(?<![*\w])\*(?!\*)([^*\n]{3,150})\*(?!\*)|(?<![_\w])_(?!_)([^_\n]{3,150})_(?!_)")
_JOURNAL_PHRASE_RE = re.compile(
    r"\b((?:[A-Z][a-z]+ )?Journal of (?:the )?[A-Z][\w\-]*(?: (?:and |of |the |&)?[A-Z][\w\-]*){0,5})")
_INITIALS_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ]{1,3}$")
_STOP = {"of", "the", "and", "&", "for", "in", "on", "a", "an", "de", "der", "und", "fur", "et"}


def _trim_doi(d: str) -> str:
    while True:
        d0 = d
        d = d.rstrip(".,;:*_~")
        if d.endswith(")") and d.count(")") > d.count("("):
            d = d[:-1]
        if d == d0:
            return d


@dataclass
class Unit:
    """One cited DOI and the text that describes it."""
    doi: str
    start: int          # offset of the DOI in the answer
    end: int
    text: str           # the describing text, DOI and URLs removed
    line_end: int       # offset where the DOI's line ends (for marks)
    pre: str = ""       # the last ~150 chars before the DOI (inline attribution)
    item: bool = False  # the description starts at its line (a reference-list item)
    problems: list = field(default_factory=list)
    record: Optional[dict] = None
    status: str = "unknown"   # ok | missing | registered | unknown


def extract_units(content: str) -> list[Unit]:
    """DOIs in the answer, each with the text of its line (a reference-list
    item) or, inside a long paragraph, the stretch since the previous DOI."""
    units: list[Unit] = []
    seen: set[str] = set()
    for m in _DOI_RE.finditer(content or ""):
        doi = _trim_doi(m.group(0))
        key = doi.lower()
        if key in seen:
            continue
        seen.add(key)
        line_start = content.rfind("\n", 0, m.start()) + 1
        line_end = content.find("\n", m.end())
        line_end = len(content) if line_end < 0 else line_end
        lo = line_start
        # Previous DOI on the same line bounds this one's description.
        for prev in units:
            if line_start <= prev.end <= m.start():
                lo = max(lo, prev.end)
        lo = max(lo, m.start() - 300)
        hi = min(line_end, m.start() + len(m.group(0)) + 80)
        nxt = _DOI_RE.search(content, m.end())
        if nxt and nxt.start() < hi:
            hi = nxt.start()
        clean = lambda t: _DOI_RE.sub(" ", _URL_RE.sub(" ", t))
        units.append(Unit(doi=doi, start=m.start(), end=m.start() + len(doi),
                          text=clean(content[lo:hi]), line_end=line_end,
                          pre=clean(content[max(lo, m.start() - 150):m.start()]),
                          item=(lo == line_start)))
        if len(units) >= MAX_DOIS:
            break
    return units


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple] = {}
_CACHE_MAX = 4096


def _user_agent() -> str:
    try:
        import site_config
        return site_config.bot_user_agent()
    except Exception:
        return "munin-citecheck"


def _crossref_record(msg: dict) -> dict:
    def first(key):
        v = msg.get(key) or []
        return v[0] if v else ""
    year = None
    for k in ("issued", "published-print", "published-online", "published", "created"):
        parts = ((msg.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = int(parts[0])
            break
    authors = [a for a in msg.get("author") or [] if isinstance(a, dict)]
    return {
        "title": first("title"),
        "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in authors][:12],
        # Crossref sometimes leaves markup in names ("<Sali").
        "families": [re.sub(r"[^\w' \-]", "", a.get("family") or a.get("name") or "")
                     for a in authors],
        "year": year,
        "journal": first("container-title"),
        "journal_short": first("short-container-title"),
        "journals": [*(msg.get("container-title") or []), *(msg.get("short-container-title") or [])],
        "volume": msg.get("volume") or "",
        "page": msg.get("page") or "",
    }


async def lookup(doi: str, client: httpx.AsyncClient) -> tuple[str, Optional[dict]]:
    """(status, record). status: ok (Crossref record), registered (exists, no
    Crossref record, e.g. DataCite), missing (both registries say 404),
    unknown (any error: verifies nothing)."""
    key = doi.lower()
    if key in _CACHE:
        return _CACHE[key]
    out: tuple[str, Optional[dict]] = ("unknown", None)
    try:
        r = await client.get(CROSSREF + quote(doi, safe=""), timeout=LOOKUP_TIMEOUT_S)
        if r.status_code == 429:   # throttled: one retry after the advertised wait
            try:
                wait = min(2.0, float(r.headers.get("retry-after") or 1))
            except ValueError:
                wait = 1.0
            await asyncio.sleep(wait)
            r = await client.get(CROSSREF + quote(doi, safe=""), timeout=LOOKUP_TIMEOUT_S)
        if r.status_code == 200:
            out = ("ok", _crossref_record(r.json().get("message") or {}))
        elif r.status_code == 404:
            h = await client.get(DOI_HANDLES + quote(doi, safe="/"), timeout=LOOKUP_TIMEOUT_S)
            if h.status_code == 404:
                out = ("missing", None)
            elif h.status_code == 200:
                out = ("registered", None)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("citecheck lookup failed for %s: %s", doi, exc)
    if out[0] != "unknown":
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = out
    return out


def cache_clear() -> None:
    _CACHE.clear()


# ---------------------------------------------------------------------------
# Checks (pure)
# ---------------------------------------------------------------------------
def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _fold(s)) if t not in _STOP]


def _surname_of(attribution: str) -> str:
    toks = attribution.split()
    if len(toks) > 1 and _INITIALS_RE.match(toks[-1].rstrip(".")):
        return toks[-2]
    return toks[-1] if toks else ""


def cited_surnames(u: "Unit") -> list[str]:
    """The author the citation names: the leading author of a reference-list
    item, else the nearest "X et al." right before the DOI. Earlier names in
    the paragraph usually belong to other citations, so they are ignored."""
    if u.item:
        lead = _LEAD_AUTHOR_RE.match(_LIST_MARK_RE.sub("", u.text.lstrip()))
        if lead:
            return [lead.group(1)]
    found = _ET_AL_RE.findall(u.pre)
    return [_surname_of(found[-1])] if found else []


def _surname_ok(name: str, families: list[str]) -> bool:
    n = _fold(name).replace("-", " ").split()
    for fam in families:
        f = _fold(fam).replace("-", " ").split()
        if not f:
            continue
        if n == f or n[-1] == f[-1] or set(n) <= set(f) or set(f) <= set(n):
            return True
    return False


def _phrase_matches(phrase: str, name: str) -> bool:
    """Could `phrase` name the journal `name`? Token-wise, allowing ISO
    abbreviations ("Biochim Biophys Acta" for "Biochimica et Biophysica Acta"),
    a shortened prefix ("Acta Cryst" for "Acta Crystallographica Section D")
    and acronyms ("PNAS", "JBC")."""
    p, n = _tokens(phrase), _tokens(name)
    if not p or not n:
        return False
    if len(p) <= len(n) and all(b.startswith(a) or a.startswith(b) for a, b in zip(p, n)):
        return True
    if len(p) == 1 and re.fullmatch(r"[A-Z]{2,7}", phrase.strip(" .")):
        return True   # any acronym: too ambiguous to call wrong
    return False


# An italic run that could be a journal name: capitalised words, no maths.
_JOURNALISH_RE = re.compile(r"^[A-Z][A-Za-z.&\-]*(?: [A-Za-z.&\-]+){0,9}$")


def _mentions(text: str, name: str) -> bool:
    """Is journal `name` (full or abbreviated) anywhere in `text`?"""
    n = _tokens(name)
    t = _tokens(text)
    if not n:
        return False
    for i in range(len(t) - len(n) + 1):
        if all(a.startswith(b) or b.startswith(a) for a, b in zip(t[i:i + len(n)], n)):
            return True
    return False


def _similar(a: str, b: str) -> float:
    ta, tb = set(_tokens(a)), set(_tokens(b))
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def check_unit(u: Unit) -> list[str]:
    """Problems with one resolved unit. Empty when nothing is provably wrong."""
    if u.status == "missing":
        return [f"DOI {u.doi} is not registered (doi.org and Crossref both return 404)"]
    rec = u.record
    if u.status != "ok" or not rec:
        return []
    problems: list[str] = []
    text = u.text

    families = [f for f in rec.get("families") or [] if f]
    if families:
        for name in cited_surnames(u):
            if not _surname_ok(name, families):
                problems.append(f"names '{name}' as author, but the record's authors are "
                                f"{', '.join(families[:6])}{' et al.' if len(families) > 6 else ''}")
                break

    ry = rec.get("year")
    years = [int(y) for y in _YEAR_RE.findall(text)]
    if ry and years and all(abs(y - ry) > 1 for y in years):
        problems.append(f"gives the year {years[0]}, but the record says {ry}")

    # All container titles: a book chapter carries the book and its series.
    names = [x for x in (rec.get("journals") or [rec.get("journal"), rec.get("journal_short")]) if x]
    if names and not any(_mentions(text, n) for n in names):
        cands = [a or b for a, b in _ITALIC_RE.findall(text)] + _JOURNAL_PHRASE_RE.findall(text)
        for c in cands:
            c = c.strip(" .,;:")
            if not _JOURNALISH_RE.match(c):
                continue      # maths, a formula or prose in italics
            if _similar(c, rec.get("title") or "") >= 0.6:
                continue      # the italic run is the article title
            if any(_phrase_matches(c, n) for n in names):
                break
            if len(_tokens(c)) >= 1:
                problems.append(f"names the journal '{c}', but the record says '{names[0]}'")
                break

    page = str(rec.get("page") or "")
    pm = re.match(r"^\s*(\d+)\s*[-–]\s*(\d+)\s*$", page)
    if pm:
        first = pm.group(1)
        ranges = [(a, b) for a, b in _PAGES_RE.findall(text)
                  if not (1900 <= int(a) <= 2039 and 1900 <= int(b) <= 2039)]
        if ranges and not any(a == first for a, _ in ranges) and first not in re.findall(r"\d+", text):
            problems.append(f"gives pages {ranges[0][0]}-{ranges[0][1]}, but the record says {page}")
    return problems


# ---------------------------------------------------------------------------
# Verify, correct, mark
# ---------------------------------------------------------------------------
async def verify(content: str) -> list[Unit]:
    units = extract_units(content)
    if not units:
        return []
    ua = _user_agent()
    sem = asyncio.Semaphore(POLITE_CONCURRENCY if "mailto:" in ua else 1)
    async with httpx.AsyncClient(headers={"User-Agent": ua},
                                 follow_redirects=True) as client:
        async def one(u: Unit):
            async with sem:
                u.status, u.record = await lookup(u.doi, client)
        try:
            await asyncio.wait_for(asyncio.gather(*(one(u) for u in units)), OVERALL_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.info("citecheck: lookups timed out after %ss", OVERALL_TIMEOUT_S)
    for u in units:
        u.problems = check_unit(u)
    return units


def _record_line(u: Unit) -> str:
    r = u.record or {}
    if u.status == "missing":
        return "no record: this DOI does not exist"
    return (f"authors: {', '.join(r.get('authors') or []) or 'n/a'}; year: {r.get('year')}; "
            f"title: {r.get('title')}; journal: {r.get('journal')}; volume: {r.get('volume')}; "
            f"pages: {r.get('page')}")


CORRECT_SYSTEM = """You fix the citations in an answer written by a scientific research assistant. A verification step looked up every cited DOI in the DOI registry and lists the citations that failed, each with the registry record.

Rules:
- For each flagged citation, rewrite its bibliographic details (authors, year, title, journal, volume, pages) to match the record exactly.
- If a DOI does not exist, delete that DOI. If the sentence relied on that citation alone, keep the statement but drop the citation, or delete the sentence if it only described that non-existent source.
- For each unsupported author attribution, delete the attribution or rephrase the sentence without the name.
- Change nothing else. Keep the wording, structure, headings, formatting, links, code and language of every other part.
- Output only the corrected answer, with no preface and no note about what you changed."""


def correction_prompt(content: str, bad: list[Unit], ungrounded: list[str]) -> str:
    lines = []
    for i, u in enumerate(bad, 1):
        lines.append(f"{i}. DOI {u.doi}: " + "; ".join(u.problems) + f"\n   registry record: {_record_line(u)}")
    for name in ungrounded:
        lines.append(f"- '{name} et al.' appears in no source the assistant retrieved")
    return f"<answer>\n{content}\n</answer>\n\n<failed_citations>\n" + "\n".join(lines) + "\n</failed_citations>"


_ARTIFACT_RE = re.compile(r"/api/artifacts/[^\s)\]\"']+")


def revision_ok(original: str, revised: Optional[str]) -> bool:
    """Reject a correction that lost most of the answer or an artifact link."""
    if not revised or len(revised.strip()) < 0.7 * len(original.strip()):
        return False
    return set(_ARTIFACT_RE.findall(original)) <= set(_ARTIFACT_RE.findall(revised))


def mark_unverified(content: str, bad: list[Unit]) -> str:
    """Append an [unverified] mark at the end of each failing DOI's line."""
    if not bad:
        return content
    by_end: dict[int, list[Unit]] = {}
    for u in bad:
        by_end.setdefault(u.line_end, []).append(u)
    out = content
    for end in sorted(by_end, reverse=True):
        reasons = "; ".join(p for u in by_end[end] for p in u.problems)
        out = out[:end] + f" **[unverified: {reasons}]**" + out[end:]
    return out


async def _correct(content: str, bad: list[Unit], ungrounded: list[str]) -> Optional[str]:
    from database import VLLM_MODEL_NAME, thinking_off_fields
    from usage_tracker import record_usage
    from vllm_client import VLLMRequestError, vllm_post_json

    max_tokens = min(16000, len(content) // 3 + 1500)
    try:
        data = await vllm_post_json(
            {"model": VLLM_MODEL_NAME,
             "messages": [{"role": "system", "content": CORRECT_SYSTEM},
                          {"role": "user", "content": correction_prompt(content, bad, ungrounded)}],
             "max_tokens": max_tokens, "temperature": 0.2, "stream": False,
             **thinking_off_fields()},
            timeout=180.0, foreground=True, purpose="citecheck")
    except VLLMRequestError as exc:
        logger.warning("citecheck correction call failed: %s", exc)
        return None
    record_usage("citecheck", data.get("usage"), data.get("model"))
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        return None
    text = ((choice.get("message") or {}).get("content") or "").strip()
    text = re.sub(r"^<answer>\s*|\s*</answer>$", "", text)
    return text or None


async def run(content: str, ungrounded: Optional[list[str]] = None,
              mode_override: Optional[str] = None) -> tuple[str, dict]:
    """Verify (and, in enforce mode, correct) the citations in `content`.

    Returns (content, notes); content is unchanged when nothing failed.
    Never raises: a failure here must not lose the user's answer.
    """
    m = mode_override or mode()
    notes = {"checked": 0, "corrected": 0, "unverified": 0}
    if m == "off" or not content:
        return content, notes
    try:
        units = await verify(content)
        notes["checked"] = sum(u.status != "unknown" for u in units)
        bad = [u for u in units if u.problems]
        ungrounded = list(ungrounded or [])
        if not bad and not (m == "enforce" and ungrounded):
            return content, notes
        out = content
        if m == "enforce" and len(content) <= MAX_CORRECT_CHARS:
            revised = await _correct(content, bad, ungrounded)
            if revision_ok(content, revised):
                after = await verify(revised)
                still = [u for u in after if u.problems]
                notes["corrected"] = len({u.doi for u in bad} - {u.doi for u in still})
                out, bad = revised, still
            else:
                logger.info("citecheck: correction rejected, marking instead")
        notes["unverified"] = len(bad)
        # No footer: a corrected reference now matches its registry record,
        # and an [unverified] mark explains itself. The judge read a footer
        # as leftover backend noise in the answer (uf-f0039), and so would a
        # user copying the text. The counts travel in `notes` instead.
        return mark_unverified(out, bad), notes
    except Exception:
        logger.exception("citecheck failed; answer left unchanged")
        return content, notes
