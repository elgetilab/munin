"""
MCP tools: s2_get_citations, s2_get_references (§20).

S2-wide citation lookups via Semantic Scholar's ``/graph/v1`` API.
Complementary to the existing Neo4j-backed ``get_citations`` /
``get_references`` tools, NOT a replacement: the Neo4j tools are
fast and cover only our curated local corpus; these tools hit the
full S2 graph (~200M papers) and are meaningfully slower. The
model picks based on whether it wants fast-local or wide-S2
coverage, guided by the tool descriptions in ``mcp/schemas.py``.

Design notes (§20 decisions 2026-04-14):

- **Two parallel API calls per invocation**: one to
  ``/paper/DOI:{doi}?fields=title,citationCount`` for the paper's
  metadata and total citation count, one to
  ``/paper/DOI:{doi}/{citations|references}`` for the list.
  Parallel via ``asyncio.gather`` so the total wall time is
  roughly one RTT.
- **Response dedup by DOI**: S2 occasionally returns the same
  paper twice (different paperId, same DOI). We dedupe after
  normalisation and before injection.
- **Local download_url injection**: for each returned item, call
  ``get_pdf_path(doi)`` - if the paper exists in the local corpus,
  add ``download_url`` and ``local_pdf_available: True`` so the
  frontend can render a direct download link. Same shape used by
  §13 in ``paper_search`` / ``semantic_scholar_search``.
- **``year_from`` filter**: applies only to citations. References
  ignore it (a paper's reference list is bounded by its own
  publication date anyway).
- **``include_contexts`` opt-in**: S2 can return the sentence
  where paper A cites paper B. Useful for "how is this cited"
  questions but noisy for plain "who cites this" lookups, so
  it's opt-in via a boolean argument.
- **Rate limit handling**: HTTP 429 from S2 returns a clean error
  telling the caller to retry in ~60s. No retry loop - let the
  model decide.
- **Hard max ``limit`` of 100**: anything beyond 100 is usually
  more than the model can usefully reason about in one turn,
  and the user can always paginate by ``year_from`` windows.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Optional
from urllib.parse import quote

import httpx

from .papers import (
    SEMANTIC_SCHOLAR_API_KEY,
    SEMANTIC_SCHOLAR_BASE_URL,
    _local_download_fields,
    get_pdf_path,
)


MAX_LIMIT = 100

# Inner paper fields to request on the /citations and /references
# endpoints. S2's nested endpoints accept either bare field names
# (auto-nested under citingPaper/citedPaper) or explicit dotted
# notation like `citingPaper.title`. We use dotted notation because
# it's unambiguous across S2 API versions. Note: `tldr` is NOT
# supported on the /citations and /references endpoints - S2
# returns HTTP 400 `Unrecognized or unsupported fields: [tldr]`.
# Callers that want tldr on specific results can follow up with
# paper_lookup(doi).
_INNER_PAPER_FIELDS = (
    "paperId",
    "title",
    "authors",
    "year",
    "citationCount",
    "externalIds",
    "openAccessPdf",
)
_PAPER_METADATA_FIELDS = "title,citationCount,externalIds"


# ---------------------------------------------------------------------------
# Shared S2 call machinery
# ---------------------------------------------------------------------------

def _s2_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    if SEMANTIC_SCHOLAR_API_KEY:
        headers["x-api-key"] = SEMANTIC_SCHOLAR_API_KEY
    return headers


async def _fetch_paper_metadata(
    client: httpx.AsyncClient, doi: str
) -> tuple[Optional[str], Optional[int]]:
    """Return (title, total_citation_count) for the source paper, or
    ``(None, None)`` if S2 doesn't know about the DOI."""
    try:
        r = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/DOI:{quote(doi, safe='')}",
            params={"fields": _PAPER_METADATA_FIELDS},
            headers=_s2_headers(),
            timeout=15.0,
        )
    except httpx.RequestError:
        return None, None
    if r.status_code == 404:
        return None, None
    if r.status_code != 200:
        return None, None
    data = r.json() or {}
    title = data.get("title")
    citation_count = data.get("citationCount")
    if isinstance(citation_count, int):
        return title, citation_count
    return title, None


async def _fetch_citation_list(
    client: httpx.AsyncClient,
    doi: str,
    direction: str,
    limit: int,
    include_contexts: bool,
) -> list[dict]:
    """Pull the raw /citations or /references list. Returns an empty
    list on any failure so callers can continue with whatever they
    have. HTTP 429 propagates as a sentinel dict so the outer tool
    can surface a clean rate-limit message."""
    assert direction in ("citations", "references")
    inner_prefix = "citingPaper" if direction == "citations" else "citedPaper"
    inner = [f"{inner_prefix}.{f}" for f in _INNER_PAPER_FIELDS]
    wrapper: list[str] = []
    if include_contexts:
        wrapper.extend(["contexts", "intents", "isInfluential"])
    fields = ",".join(inner + wrapper)
    try:
        r = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE_URL}/paper/DOI:{quote(doi, safe='')}/{direction}",
            params={"fields": fields, "limit": str(limit)},
            headers=_s2_headers(),
            timeout=20.0,
        )
    except httpx.RequestError as exc:
        return [{"_error": f"s2 unreachable: {type(exc).__name__}: {exc}"}]
    if r.status_code == 429:
        return [{"_error": (
            "Semantic Scholar rate-limited this request (HTTP 429). "
            "Retry in ~60s. If this happens repeatedly, configure "
            "SEMANTIC_SCHOLAR_API_KEY for a higher quota."
        )}]
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        # Surface S2's error body so the caller can diagnose bad
        # fields parameters, unknown DOIs, etc.
        try:
            body_snippet = r.text[:300]
        except Exception:
            body_snippet = ""
        return [{
            "_error": f"s2 returned HTTP {r.status_code}: {body_snippet}"
        }]
    data = r.json() or {}
    return data.get("data") or []


# ---------------------------------------------------------------------------
# Response normalisation
# ---------------------------------------------------------------------------

def _unwrap_entry(raw: dict, direction: str) -> tuple[Optional[dict], dict]:
    """
    S2 wraps each list entry in ``citingPaper`` (for /citations) or
    ``citedPaper`` (for /references) and attaches context/intents on
    the outer dict. Pull the inner paper out and return
    ``(paper, outer_metadata)`` where outer_metadata has the context
    fields if present.
    """
    wrapper_key = "citingPaper" if direction == "citations" else "citedPaper"
    inner = raw.get(wrapper_key)
    if not isinstance(inner, dict):
        return None, {}
    outer_extras: dict[str, Any] = {}
    for key in ("contexts", "intents", "isInfluential"):
        if key in raw:
            outer_extras[key] = raw[key]
    return inner, outer_extras


def _normalise_paper(inner: dict, outer_extras: dict) -> Optional[dict]:
    """Flatten an S2 paper entry into the shape we return to callers.
    Returns None if the entry has no DOI (we dedupe by DOI, so
    undoi'd entries can't be returned safely)."""
    doi = ""
    external_ids = inner.get("externalIds") or {}
    if isinstance(external_ids, dict):
        doi = external_ids.get("DOI") or external_ids.get("doi") or ""
    if not doi:
        return None

    authors = []
    for a in (inner.get("authors") or [])[:10]:
        name = a.get("name") if isinstance(a, dict) else None
        if name:
            authors.append(name)

    oa_obj = inner.get("openAccessPdf")
    oa_pdf = None
    if isinstance(oa_obj, dict):
        oa_pdf = oa_obj.get("url") or None

    out: dict[str, Any] = {
        "doi": doi,
        "title": inner.get("title"),
        "authors": authors,
        "year": inner.get("year"),
        "citation_count": inner.get("citationCount") or 0,
        "open_access_pdf": oa_pdf,
    }
    # §13 local download injection
    local = _local_download_fields(doi)
    if local:
        out.update(local)
    # Context fields (populated only when include_contexts=True).
    # We preserve empty lists/nulls here instead of dropping them,
    # so callers can distinguish "contexts not requested" from
    # "contexts requested but S2 has none for this citing paper"
    # (the latter is common because S2 only extracts contexts from
    # papers in its full-text S2ORC corpus, and many citing papers
    # are metadata-only).
    if "contexts" in outer_extras:
        out["contexts"] = outer_extras["contexts"]
    if "intents" in outer_extras:
        out["intents"] = outer_extras["intents"]
    if "isInfluential" in outer_extras:
        out["is_influential"] = outer_extras["isInfluential"]
    return out


def _dedupe_by_doi(entries: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for entry in entries:
        doi = (entry.get("doi") or "").lower()
        if not doi or doi in seen:
            continue
        seen.add(doi)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

async def _s2_citation_call(
    doi: str,
    direction: str,
    limit: int,
    year_from: Optional[int],
    include_contexts: bool,
) -> dict:
    assert direction in ("citations", "references")
    doi = (doi or "").strip()
    if not doi:
        return {"error": "doi must be a non-empty string"}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, MAX_LIMIT))

    async with httpx.AsyncClient() as client:
        paper_task = _fetch_paper_metadata(client, doi)
        list_task = _fetch_citation_list(
            client, doi, direction, limit, include_contexts
        )
        (title, total_count), raw_entries = await asyncio.gather(
            paper_task, list_task
        )

    # Rate-limit / error sentinel surfaced from the list call
    if raw_entries and isinstance(raw_entries[0], dict) and raw_entries[0].get("_error"):
        return {
            "error": raw_entries[0]["_error"],
            "doi": doi,
        }

    normalised: list[dict] = []
    for raw in raw_entries:
        inner, outer_extras = _unwrap_entry(raw, direction)
        if inner is None:
            continue
        flat = _normalise_paper(inner, outer_extras)
        if flat is None:
            continue
        normalised.append(flat)

    # Year filter (citations only - applying to references is
    # conceptually incoherent).
    if direction == "citations" and year_from is not None:
        try:
            cutoff = int(year_from)
            normalised = [
                e for e in normalised
                if isinstance(e.get("year"), int) and e["year"] >= cutoff
            ]
        except (TypeError, ValueError):
            pass

    normalised = _dedupe_by_doi(normalised)

    out: dict = {
        "doi": doi,
        "paper_title": title,
        "direction": direction,
        "count_returned": len(normalised),
    }
    if direction == "citations":
        out["citations"] = normalised
        if total_count is not None:
            out["total_citations"] = total_count
        if year_from is not None:
            out["year_from"] = year_from
    else:
        out["references"] = normalised
    return out


async def s2_get_citations(
    doi: str,
    limit: int = 50,
    year_from: Optional[int] = None,
    include_contexts: bool = False,
) -> dict:
    """Papers that cite the given DOI, from the full Semantic Scholar
    citation graph. See module docstring for design notes."""
    return await _s2_citation_call(
        doi=doi,
        direction="citations",
        limit=limit,
        year_from=year_from,
        include_contexts=bool(include_contexts),
    )


async def s2_get_references(
    doi: str,
    limit: int = 50,
    include_contexts: bool = False,
) -> dict:
    """Papers that the given DOI cites, from the full Semantic Scholar
    citation graph. See module docstring for design notes."""
    return await _s2_citation_call(
        doi=doi,
        direction="references",
        limit=limit,
        year_from=None,
        include_contexts=bool(include_contexts),
    )
