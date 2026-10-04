"""
MCP tool: export_citations.

Hits doi.org content negotiation in parallel for a list of DOIs and returns
the formatted citation strings. Researchers can drop the output straight
into Zotero, Mendeley, EndNote, or a LaTeX bibliography without scraping
HTML pages.

Content negotiation is registrar-agnostic: doi.org redirects to whichever
service backs the DOI (Crossref, DataCite, mEDRA, ...) and we get back the
right format. No need to special-case Crossref-only DOIs.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

import site_config

# Map from frontend-friendly format names → HTTP Accept header values.
# Anything in this dict is a valid `format` parameter for the tool.
_FORMAT_ACCEPT: dict[str, str] = {
    "bibtex": "application/x-bibtex",
    "ris": "application/x-research-info-systems",
    "csl-json": "application/vnd.citationstyles.csl+json",
    "apa": "text/x-bibliography; style=apa",
    "chicago": "text/x-bibliography; style=chicago-fullnote-bibliography",
    "nature": "text/x-bibliography; style=nature",
    "ieee": "text/x-bibliography; style=ieee",
    "vancouver": "text/x-bibliography; style=vancouver",
}

_USER_AGENT = site_config.bot_user_agent()
_PER_DOI_TIMEOUT = 15.0
_MAX_DOIS_PER_CALL = 50


async def _fetch_one(client: httpx.AsyncClient, doi: str, accept: str) -> dict:
    """Resolve one DOI via content negotiation. Returns a per-DOI result dict."""
    doi = (doi or "").strip()
    if not doi:
        return {"doi": "", "error": "empty DOI"}

    headers = {
        "Accept": accept,
        "User-Agent": _USER_AGENT,
    }
    try:
        # follow_redirects=True because doi.org always redirects to the
        # registrar's content-negotiation endpoint
        response = await client.get(
            f"https://doi.org/{quote(doi, safe='')}",
            headers=headers,
            follow_redirects=True,
            timeout=_PER_DOI_TIMEOUT,
        )
    except httpx.TimeoutException:
        return {"doi": doi, "error": "request timed out"}
    except Exception as e:
        return {"doi": doi, "error": f"fetch failed: {e}"}

    if response.status_code == 404:
        return {"doi": doi, "error": "DOI not found"}
    if response.status_code == 406:
        return {
            "doi": doi,
            "error": (
                "registrar does not support this format for this DOI"
            ),
        }
    if response.status_code != 200:
        return {
            "doi": doi,
            "error": f"HTTP {response.status_code}",
        }

    text = response.text.strip()
    if not text:
        return {"doi": doi, "error": "empty response"}
    return {"doi": doi, "text": text}


async def export_citations(
    dois: list[str],
    format: str = "bibtex",
) -> dict:
    """
    Export formatted citations for a list of DOIs.

    Args:
        dois: List of DOI strings. No prefix needed (e.g.
            "10.1038/s41586-021-03819-2", not
            "https://doi.org/10.1038/s41586-021-03819-2").
        format: One of bibtex, ris, csl-json, apa, chicago, nature,
            ieee, vancouver. Default: bibtex.

    Returns:
        Dict with:
          format: the format that was requested
          total: number of DOIs in the input
          successful: number of DOIs that resolved
          failed: number that errored
          citations: per-DOI result list. Each entry has {"doi", "text"}
            on success or {"doi", "error"} on failure. Order matches the
            input order.
    """
    if not isinstance(dois, list) or not dois:
        return {"error": "dois must be a non-empty list of DOI strings"}

    if format not in _FORMAT_ACCEPT:
        return {
            "error": (
                f"Unsupported format: {format!r}. "
                f"Choose from: {', '.join(sorted(_FORMAT_ACCEPT))}"
            )
        }

    if len(dois) > _MAX_DOIS_PER_CALL:
        return {
            "error": (
                f"Too many DOIs ({len(dois)}). "
                f"Maximum {_MAX_DOIS_PER_CALL} per call."
            )
        }

    accept = _FORMAT_ACCEPT[format]

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *(_fetch_one(client, d, accept) for d in dois)
        )

    successful = sum(1 for r in results if "text" in r)
    failed = sum(1 for r in results if "error" in r)

    return {
        "format": format,
        "total": len(dois),
        "successful": successful,
        "failed": failed,
        "citations": results,
    }
