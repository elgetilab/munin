"""
Bibliographic reference resolution by identifier.

Why this exists (chat 61443530, 2026-07-29): the model cited "Bazzi et al."
for PMC2098716 and "Gonzalez-Rodriguez et al." for PMID 23274277. Neither
name appeared in any tool result. It had a title and an identifier but no
author list, because the web tier never carries one: `web_search` returns
title/url/snippet, and `web_fetch` returns an LLM summary of the page body
with the metadata stripped. Asked point blank for authors, the summarizer
answered "not provided in the text". So the model filled the gap from
parametric memory, twice, with names that are plausible in that subfield.

The fix is to make the metadata available at the point a web result is
produced. This module is the single resolver: give it any identifier (DOI,
PMID, PMCID, arXiv id) or a scholarly URL, get back canonical metadata.

Source cascade, cheapest and most trustworthy first:

    1. local corpus   (papers.py::_paper_lookup_local, no network)
    2. Semantic Scholar
    3. Crossref
    4. NCBI eutils esummary  (the only leg that resolves a bare PMID/PMCID)

Legs 2-4 are network calls, so every one of them is gated on
``provenance.may_fetch(NET_SCHOLARLY_API)``: an ``egress=off`` run resolves
from the corpus only, and a benchmark can never quietly spend quota here.

NCBI asks for <= 3 requests/second without an API key, which is what we do
(see ``_ncbi_throttle``). No key is configured deliberately: our volume is a
handful of lookups per turn.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Optional
from urllib.parse import quote, unquote

import httpx

import provenance

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
# NCBI asks every automated client to identify itself. Same identity the
# Crossref client in papers.py uses.
_NCBI_TOOL = "munin"
_NCBI_EMAIL = "research@muninai.org"
_NCBI_MIN_INTERVAL_S = 0.34        # <= 3 req/s, the keyless NCBI ceiling
_HTTP_TIMEOUT_S = 12.0

# Resolution is idempotent and identifiers are immutable, so an in-process
# memo is safe. Bounded because a long-lived container would otherwise grow
# it without limit; papers are not re-resolved often enough for eviction
# policy to matter, so plain FIFO clearing is enough.
_CACHE: dict[str, Optional[dict]] = {}
_CACHE_MAX = 2048

_ncbi_lock = asyncio.Lock()
_ncbi_last_call = 0.0


# ---------------------------------------------------------------------------
# Identifier parsing
# ---------------------------------------------------------------------------
# DOIs: the 10.x/suffix form. Deliberately permissive on the suffix (DOIs
# legitimately contain parens, semicolons, angle brackets) but stops at
# whitespace and at markdown/HTML delimiters that are never part of a DOI in
# our inputs.
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\)\]}]+)", re.I)
_PMID_URL_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{4,9})", re.I)
_PMC_URL_RE = re.compile(r"(?:pmc\.ncbi\.nlm\.nih\.gov/articles/|/)(PMC\d{5,9})", re.I)
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}|[a-z\-]+/\d{7})", re.I)
_BARE_PMID_RE = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)
_BARE_PMC_RE = re.compile(r"\b(PMC\d{5,9})\b", re.I)
_BARE_ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})\b", re.I)

# Trailing punctuation that comes from prose, not from the DOI itself.
_DOI_TRAILING = ".,;:"


def extract_identifier(text: str) -> Optional[tuple[str, str]]:
    """Find the first resolvable identifier in ``text`` (a URL or free text).

    Returns ``(kind, value)`` with kind in {doi, pmid, pmcid, arxiv}, or
    None. Ordering matters: a PubMed URL is checked before the generic DOI
    pattern so ``pubmed.../23274277`` does not get mis-read, and DOIs inside
    publisher URLs are recognised so a Wiley or Elsevier link still resolves.
    """
    if not text or not isinstance(text, str):
        return None

    m = _PMID_URL_RE.search(text)
    if m:
        return ("pmid", m.group(1))

    m = _PMC_URL_RE.search(text)
    if m:
        return ("pmcid", m.group(1).upper())

    m = _ARXIV_URL_RE.search(text)
    if m:
        return ("arxiv", m.group(1))

    m = _DOI_RE.search(unquote(text))
    if m:
        doi = m.group(1).rstrip(_DOI_TRAILING)
        return ("doi", doi)

    m = _BARE_PMID_RE.search(text)
    if m:
        return ("pmid", m.group(1))

    m = _BARE_PMC_RE.search(text)
    if m:
        return ("pmcid", m.group(1).upper())

    m = _BARE_ARXIV_RE.search(text)
    if m:
        return ("arxiv", m.group(1))

    return None


# ---------------------------------------------------------------------------
# NCBI eutils
# ---------------------------------------------------------------------------
async def _ncbi_throttle() -> None:
    """Serialise NCBI calls to <= 3/s process-wide (keyless limit)."""
    global _ncbi_last_call
    async with _ncbi_lock:
        wait = _NCBI_MIN_INTERVAL_S - (time.monotonic() - _ncbi_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _ncbi_last_call = time.monotonic()


def _parse_esummary(record: dict) -> Optional[dict]:
    """Map one eutils esummary record onto our metadata shape.

    esummary gives authors as ``[{"name": "Loura LM", "authtype": "Author"}]``
    and identifiers in a flat ``articleids`` list, so both need unpacking.
    """
    if not record or record.get("error"):
        return None
    title = (record.get("title") or "").strip().rstrip(".")
    if not title:
        return None

    authors = [
        a.get("name", "").strip()
        for a in (record.get("authors") or [])
        if a.get("authtype", "Author") == "Author" and a.get("name")
    ]

    ids = {i.get("idtype"): i.get("value") for i in (record.get("articleids") or [])}
    year = None
    pubdate = record.get("pubdate") or record.get("epubdate") or ""
    m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", pubdate)
    if m:
        year = int(m.group(1))

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "journal": record.get("fulljournalname") or record.get("source") or "",
        "doi": ids.get("doi"),
        "pmid": ids.get("pubmed") or record.get("uid"),
        "pmcid": (ids.get("pmc") or "").upper() or None,
        "source": "pubmed",
    }


async def _lookup_pubmed(pmid: str) -> Optional[dict]:
    """esummary for a PMID. The one leg that turns a bare PubMed identifier
    into an author list, which is exactly what was missing when the model
    invented "Gonzalez-Rodriguez" for PMID 23274277."""
    await _ncbi_throttle()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            r = await client.get(
                f"{EUTILS_BASE}/esummary.fcgi",
                params={"db": "pubmed", "id": pmid, "retmode": "json",
                        "tool": _NCBI_TOOL, "email": _NCBI_EMAIL},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("eutils esummary failed for PMID %s: %s", pmid, e)
        return None

    result = (data or {}).get("result") or {}
    for uid in result.get("uids") or []:
        parsed = _parse_esummary(result.get(uid) or {})
        if parsed:
            return parsed
    return None


async def _pmcid_to_pmid(pmcid: str) -> Optional[str]:
    """Convert PMCxxxxxxx to a PMID via eutils idconv.

    Needed because PMC article pages are the ones our web tier surfaces, and
    PMC is also the host that serves us a bot-check page instead of content
    (chat 61443530: a PMC fetch returned 131 characters of security notice).
    """
    await _ncbi_throttle()
    try:
        # The old /pmc/utils/idconv/v1.0/ path 301s to this one (verified
        # 2026-08-03); follow_redirects is belt and braces for the next move.
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S,
                                     follow_redirects=True) as client:
            r = await client.get(
                "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
                params={"ids": pmcid, "format": "json",
                        "tool": _NCBI_TOOL, "email": _NCBI_EMAIL},
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("PMC idconv failed for %s: %s", pmcid, e)
        return None

    for rec in (data or {}).get("records") or []:
        if rec.get("pmid"):
            return str(rec["pmid"])
    return None


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------
def _normalise(meta: dict, *, doi: Optional[str] = None,
               pmid: Optional[str] = None, pmcid: Optional[str] = None) -> dict:
    """Common output shape, whichever leg produced it."""
    ext = meta.get("external_ids") or {}
    return {
        "title": meta.get("title"),
        "authors": [a for a in (meta.get("authors") or []) if a],
        "year": meta.get("year"),
        "journal": meta.get("journal") or "",
        "doi": meta.get("doi") or doi or ext.get("DOI"),
        "pmid": meta.get("pmid") or pmid or ext.get("PubMed"),
        "pmcid": meta.get("pmcid") or pmcid or ext.get("PubMedCentral"),
        "source": meta.get("source", "unknown"),
    }


async def _resolve_doi(doi: str) -> Optional[dict]:
    """Local corpus, then S2, then Crossref. Reuses the existing cascade in
    papers.py rather than duplicating three HTTP clients."""
    from mcp.tools import papers

    local = papers._paper_lookup_local(doi)
    if local and local.get("title"):
        return _normalise(local, doi=doi)

    if not provenance.may_fetch(provenance.NET_SCHOLARLY_API):
        return None

    s2 = await papers._paper_lookup_semantic_scholar(doi)
    if s2 and s2.get("title"):
        return _normalise(s2, doi=doi)

    crossref = await papers._paper_lookup_crossref(doi)
    if crossref and crossref.get("title"):
        return _normalise(crossref, doi=doi)

    return None


async def resolve(kind: str, value: str) -> Optional[dict]:
    """Resolve one identifier to canonical metadata, or None.

    Never raises: a caller enriching search results must degrade to today's
    behaviour (no metadata) rather than fail the search.
    """
    if not kind or not value:
        return None
    key = f"{kind}:{value}".lower()
    if key in _CACHE:
        return _CACHE[key]

    result: Optional[dict] = None
    try:
        if kind == "doi":
            result = await _resolve_doi(value)
        elif kind == "pmid":
            if provenance.may_fetch(provenance.NET_SCHOLARLY_API):
                result = await _lookup_pubmed(value)
                # PubMed often carries the DOI; a corpus hit on it is richer
                # (citation counts, local PDF) than the esummary record.
                if result and result.get("doi"):
                    richer = await _resolve_doi(result["doi"])
                    if richer and richer.get("authors"):
                        richer.setdefault("pmid", value)
                        result = richer
        elif kind == "pmcid":
            if provenance.may_fetch(provenance.NET_SCHOLARLY_API):
                pmid = await _pmcid_to_pmid(value)
                if pmid:
                    result = await resolve("pmid", pmid)
                    if result:
                        result = dict(result, pmcid=value)
        elif kind == "arxiv":
            # arXiv preprints carry a registered DOI in the 10.48550 prefix.
            result = await _resolve_doi(f"10.48550/arXiv.{value}")
    except Exception:
        logger.exception("bibref.resolve failed for %s:%s", kind, value)
        result = None

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = result
    return result


async def resolve_url(url: str) -> Optional[dict]:
    """Resolve whatever identifier a URL carries. None when it carries none
    (an ordinary web page), which is the common case and not an error."""
    ident = extract_identifier(url)
    if not ident:
        return None
    return await resolve(*ident)


def cache_clear() -> None:
    """Test hook."""
    _CACHE.clear()
