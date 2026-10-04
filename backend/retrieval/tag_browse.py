"""
Tag browse: paginated enumeration of the corpus by topic / group / contributor.

This is the BROWSE axis, deliberately separate from search. `paper_search`
answers "what is relevant to this question" with a ranked similarity query;
this answers "what is in this collection", which is a different question and
must not be served by a ranker. A user asking "list the first 10 papers in the
Deibel group" wants an inventory, and every similarity-based answer to that is
a sample dressed up as a list.

Extracted from main.py's `/api/tags/{kind}/{slug}/papers` route (2026-09-01) so
the MCP tool `browse_tag_papers` and the Knowledge page run the same code.
Before this, only the HTTP endpoint could enumerate, so the model told a user
"there isn't a tool available to me that can list, enumerate, or browse all
papers in the Deibel group sub-corpus" while the web UI beside it was doing
exactly that.
"""

from __future__ import annotations

import os
from typing import Any, Optional
from urllib.parse import quote

import database
import site_config
from database import get_qdrant, get_pdf_path

TAG_KIND_TO_FILTER_KEY = {
    "topic": "topic_slug",
    "group": "contributors[].group_slug",
    "contributor": "contributors[].username",
}

SORTS = ("year_desc", "year_asc", "upload_desc")

# Public host used to build `download_url` fields on browse results.
# Same convention as retrieval/mcp/tools/papers.py.
PUBLIC_MUNIN_URL = site_config.PUBLIC_URL

# One page of a scroll. The walk below is O(offset) in pages, which is cheap at
# corpus scale; a >100k corpus would want a DB-side ordering column instead.
_PAGE_CAP = 256


class TagBrowseError(ValueError):
    """Bad kind/slug, or the index is unavailable. Carries an HTTP status so
    the route can translate without re-deriving the reason."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def paper_stub(payload: dict) -> dict:
    """Shape a Qdrant payload into the public paper-card shape used by
    browse + search results. Mirrors what paper_search returns, minus
    the score/matched_query fields."""
    authors = payload.get("authors") or []
    if isinstance(authors, list):
        authors = [a if isinstance(a, str) else (a or {}).get("name", "") for a in authors][:5]
    doi = payload.get("doi")
    stub = {
        "title": payload.get("title"),
        "doi": doi,
        "year": payload.get("year"),
        "authors": authors,
        "journal": payload.get("journal"),
    }
    contributors = payload.get("contributors")
    if contributors:
        stub["contributors"] = [
            {
                "display_name": (c or {}).get("display_name"),
                "group_slug": (c or {}).get("group_slug"),
                "group_display_name": (c or {}).get("group_display_name"),
                "upload_time": (c or {}).get("upload_time"),
            }
            for c in contributors
            if isinstance(c, dict)
        ]
    topic_label = payload.get("topic_label")
    if topic_label and payload.get("topic_slug") != "unclustered":
        stub["topic"] = {
            "label": topic_label,
            "slug": payload.get("topic_slug"),
        }
    if doi:
        # Convention: only return a download_url when the PDF is on disk.
        # Must use the same resolver the download route uses, or the UI
        # offers links that 404 (and hides links that would have worked).
        if get_pdf_path(doi):
            stub["download_url"] = f"{PUBLIC_MUNIN_URL}/paper/{quote(doi, safe='')}/pdf"
    return stub


def browse_tag_papers(
    kind: str,
    slug: str,
    offset: int = 0,
    limit: int = 50,
    sort: str = "year_desc",
) -> dict:
    """Paginated list of papers matching a tag. No ranking, no query.

    Raises TagBrowseError for an unknown kind, an empty slug, or an
    unavailable index. Callers translate that into their own error shape.
    """
    filter_key = TAG_KIND_TO_FILTER_KEY.get(kind)
    if filter_key is None:
        raise TagBrowseError(f"unknown tag kind: {kind!r}")
    slug = (slug or "").strip().lower()
    if not slug:
        raise TagBrowseError("slug is required")
    if sort not in SORTS:
        raise TagBrowseError(f"unknown sort: {sort!r}")
    offset = max(0, int(offset))
    limit = max(1, min(200, int(limit)))

    qdrant = get_qdrant()
    if qdrant is None:
        raise TagBrowseError("Qdrant unavailable", status=503)

    from qdrant_client.http import models as qm
    flt = qm.Filter(
        must=[qm.FieldCondition(key=filter_key, match=qm.MatchValue(value=slug))]
    )

    # Total count is cheap with payload indexes — the frontend uses it
    # to render "1 of 418", and the model needs it to say how much of a
    # collection a page represents rather than implying it saw all of it.
    try:
        count_res = qdrant.count(
            collection_name=database.PAPERS_COLLECTION, count_filter=flt, exact=True
        )
        total = getattr(count_res, "count", 0)
    except Exception:
        total = 0

    # Qdrant scroll doesn't do offset directly — we paginate by walking
    # pages until we've skipped `offset`.
    papers: list[dict] = []
    seen = 0
    scroll_offset: Optional[Any] = None
    page_size = min(_PAGE_CAP, offset + limit)
    try:
        while seen < offset + limit:
            points, scroll_offset = qdrant.scroll(
                collection_name=database.PAPERS_COLLECTION,
                scroll_filter=flt,
                limit=page_size,
                offset=scroll_offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for p in points:
                payload = p.payload or {}
                if seen >= offset:
                    papers.append(paper_stub(payload))
                seen += 1
                if len(papers) >= limit:
                    break
            if scroll_offset is None:
                break
    except Exception as e:
        raise TagBrowseError(f"Qdrant scroll failed: {e}", status=500)

    # Sort within the page (Qdrant doesn't sort-by-payload natively).
    # For a true corpus-wide sort we'd need to materialise everything,
    # which isn't scalable — but for a single page of <=200 this is fine.
    if sort == "year_desc":
        papers.sort(key=lambda p: (p.get("year") or 0), reverse=True)
    elif sort == "year_asc":
        papers.sort(key=lambda p: (p.get("year") or 9999))
    elif sort == "upload_desc":
        papers.sort(
            key=lambda p: ((p.get("contributors") or [{}])[-1].get("upload_time") or ""),
            reverse=True,
        )

    return {
        "kind": kind,
        "slug": slug,
        "total": total,
        "offset": offset,
        "limit": limit,
        "sort": sort,
        "papers": papers,
    }
