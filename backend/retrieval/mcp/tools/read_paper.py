"""
MCP tool: read_paper (§7).

Fetch + parse + summarise a paper in one call. Chains infrastructure
we already have:

  paper_lookup      → resolve DOI to title / authors / OA PDF URL
  /papers/pdf/      → local corpus for papers we've already crawled
  /data/papers_cached/ → on-disk cache for on-demand downloads
  document_store._extract_pdf → GROBID first, pypdf fallback
  llm_summarize     → vLLM-backed summariser

Two LLM calls per invocation:

  1. Narrative summary biased by the optional ``focus`` argument
  2. Bulleted key-findings list

Two calls (not one) by design: parsing a single structured response
for two fields is brittle; two calls are more expensive but every
call has a clean prompt and a clean parse. Latency is dominated by
PDF fetch + GROBID anyway.

Cache policy (§7 Stage A decisions 2026-04-14):
  - PDFs downloaded from an OA URL are stored at
    ``/data/papers_cached/{sanitised_doi}.pdf`` inside the
    retrieval container.
  - There is NO eviction. The directory grows forever until the
    operator intervenes. To help them notice growth, every write
    computes the total cache size and logs ``[WARN] papers_cached
    exceeded Ngb`` when it crosses ``PAPERS_CACHE_WARN_GB`` (default
    5 GB). The tool also returns ``cache_size_mb`` on every call
    so a diligent user can see it.
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional

import httpx

# Lazy imports for paper_lookup / document_store / llm_summarize are done
# at call time to avoid circular-init headaches.


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_DIR = os.environ.get("PAPERS_CACHE_DIR", "/data/papers_cached")
MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB per PDF, matches /api/documents/upload
PAPERS_CACHE_WARN_GB = float(os.environ.get("PAPERS_CACHE_WARN_GB", "5"))


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _sanitise_doi_for_filename(doi: str) -> str:
    """Turn a DOI into a safe filename. Matches the scheme paper_crawler.py
    uses in /papers/pdf/ (slashes and colons → underscores)."""
    return (doi or "").strip().replace("/", "_").replace(":", "_")


def _cache_path_for(doi: str) -> str:
    return os.path.join(CACHE_DIR, f"{_sanitise_doi_for_filename(doi)}.pdf")


def _compute_cache_size_bytes() -> int:
    try:
        total = 0
        for entry in os.listdir(CACHE_DIR):
            full = os.path.join(CACHE_DIR, entry)
            if os.path.isfile(full):
                try:
                    total += os.path.getsize(full)
                except OSError:
                    continue
        return total
    except FileNotFoundError:
        return 0
    except OSError:
        return 0


def _warn_if_cache_bloated() -> float:
    """Return cache size in MB. Print a warning to container logs if it
    exceeds the configured threshold so the operator notices growth
    without having to check proactively."""
    size_bytes = _compute_cache_size_bytes()
    size_mb = size_bytes / (1024 * 1024)
    threshold_bytes = PAPERS_CACHE_WARN_GB * 1024 * 1024 * 1024
    if size_bytes > threshold_bytes:
        print(
            f"[WARN] papers_cached exceeded {PAPERS_CACHE_WARN_GB} GB "
            f"(current: {size_mb:.1f} MB). No eviction runs automatically; "
            f"manually clean {CACHE_DIR} if needed."
        )
    return size_mb


def _write_cache(doi: str, pdf_bytes: bytes) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        path = _cache_path_for(doi)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(pdf_bytes)
        os.replace(tmp, path)
    except OSError as exc:
        print(f"[WARNING] papers_cached write failed for {doi}: {exc}")


def _read_cache(doi: str) -> Optional[bytes]:
    path = _cache_path_for(doi)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# PDF fetch
# ---------------------------------------------------------------------------

async def _download_pdf(url: str) -> Optional[bytes]:
    """Download a PDF URL with a 50 MB cap. Streaming so a pathological
    huge URL can't OOM the container."""
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None
                chunks: list[bytes] = []
                received = 0
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > MAX_PDF_BYTES:
                        return None
                    chunks.append(chunk)
                return b"".join(chunks)
    except httpx.RequestError:
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Summarisation (two separate LLM calls)
# ---------------------------------------------------------------------------

_NARRATIVE_FOCUS_SUFFIX = (
    " The user is particularly interested in: {focus}. Emphasise any "
    "material the paper has on this topic; if the paper does not cover "
    "it, say so explicitly in the first sentence."
)


async def _summarise_narrative(
    text: str, focus: Optional[str], title: str
) -> str:
    """One LLM call that produces a 3-5 sentence narrative summary."""
    from .llm import llm_summarize

    instruction = (
        f"Summarise this paper in 3-5 sentences. Cover what the paper is "
        f"about, the main method or approach, and the headline result. "
        f"Write for a technically literate reader. Do not add caveats "
        f"about being an AI. Do not repeat the title {title!r}."
    )
    if focus:
        instruction += _NARRATIVE_FOCUS_SUFFIX.format(focus=focus)
    result = await llm_summarize(text, instruction, max_tokens=600)
    if isinstance(result, dict) and result.get("summary"):
        return str(result["summary"]).strip()
    return ""


async def _summarise_key_findings(
    text: str, focus: Optional[str]
) -> list[str]:
    """Second LLM call that produces a bulleted list of key findings."""
    from .llm import llm_summarize

    instruction = (
        "Extract the 3-6 most important findings or claims from this "
        "paper. Format as a plain bullet list, one finding per line, "
        "each starting with '- '. Be specific - prefer quantitative "
        "results ('a 37% improvement in X') over vague statements "
        "('the method works well'). Do not include any other text, "
        "preamble, or closing line."
    )
    if focus:
        instruction += (
            f" Prioritise findings relevant to: {focus}. Omit findings "
            "that are unrelated."
        )
    result = await llm_summarize(text, instruction, max_tokens=700)
    if not isinstance(result, dict) or not result.get("summary"):
        return []
    raw = str(result["summary"])
    findings: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")):
            findings.append(stripped[2:].strip())
        elif stripped and stripped[0].isdigit() and "." in stripped[:3]:
            # "1. finding text" style
            findings.append(stripped.split(".", 1)[1].strip())
    return [f for f in findings if f]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def read_paper(doi: str, focus: Optional[str] = None) -> dict:
    """
    Fetch, parse, and summarise a paper by DOI. See module docstring
    for the full flow and cache policy.
    """
    import mcp.tools.papers as paper_tools  # lazy, circular-safe
    from mcp.tools.papers import paper_lookup, get_pdf_path

    doi = (doi or "").strip()
    if not doi:
        return {"error": "read_paper requires a non-empty DOI"}

    lookup = await paper_lookup(doi)
    if not isinstance(lookup, dict) or lookup.get("error"):
        return {
            "error": (lookup or {}).get("error", "paper_lookup failed"),
            "doi": doi,
        }

    title = lookup.get("title") or ""
    authors = lookup.get("authors") or []
    abstract = lookup.get("abstract") or lookup.get("tldr") or ""
    oa_pdf_url = lookup.get("open_access_pdf")
    lookup_source = lookup.get("source")  # "local" | "semantic_scholar" | "crossref"

    sources_used: list[str] = []
    pdf_bytes: Optional[bytes] = None

    # Step 1: local corpus (fastest, most reliable)
    local_path = get_pdf_path(doi)
    if local_path and os.path.isfile(local_path):
        try:
            with open(local_path, "rb") as f:
                pdf_bytes = f.read()
            sources_used.append("local")
        except OSError:
            pdf_bytes = None

    # Step 2: disk cache (previously downloaded)
    if pdf_bytes is None:
        cached = _read_cache(doi)
        if cached:
            pdf_bytes = cached
            sources_used.append("cache")

    # Step 3: fresh download from OA URL
    downloaded_fresh = False
    if pdf_bytes is None and oa_pdf_url:
        fetched = await _download_pdf(oa_pdf_url)
        if fetched:
            pdf_bytes = fetched
            sources_used.append("open_access_pdf")
            downloaded_fresh = True

    # Step 4: fall back to abstract-only summary
    if pdf_bytes is None:
        if not abstract:
            return {
                "error": (
                    "No PDF available (not in local corpus, no cache, no "
                    "open-access URL) and no abstract/TLDR to fall back on"
                ),
                "doi": doi,
                "title": title,
                "authors": authors,
                "sources_used": [],
            }
        return {
            "doi": doi,
            "title": title,
            "authors": authors,
            "abstract": abstract,
            "summary": abstract,
            "key_findings": [],
            "sources_used": ["s2_abstract"],
            "lookup_source": lookup_source,
            "cache_size_mb": round(_warn_if_cache_bloated(), 2),
        }

    # Extract text from the PDF (GROBID first, pypdf fallback).
    import document_store
    try:
        extracted_text = await document_store._extract_pdf(pdf_bytes)
    except Exception as exc:
        extracted_text = ""
        print(f"[WARNING] read_paper PDF extraction failed for {doi}: {exc}")

    if not extracted_text or len(extracted_text) < 200:
        # GROBID + pypdf both failed; treat as abstract-only.
        if abstract:
            return {
                "doi": doi,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "summary": abstract,
                "key_findings": [],
                "sources_used": sources_used + ["s2_abstract"],
                "lookup_source": lookup_source,
                "cache_size_mb": round(_warn_if_cache_bloated(), 2),
                "note": "PDF extraction failed; falling back to abstract",
            }
        return {
            "error": "PDF extraction failed and no abstract to fall back on",
            "doi": doi,
            "title": title,
            "authors": authors,
            "sources_used": sources_used,
        }

    # Persist to cache if this was a fresh download. Don't re-cache
    # bytes that came from /papers/pdf/ or the cache itself.
    if downloaded_fresh:
        _write_cache(doi, pdf_bytes)

    # Two separate summarisation calls. Run in parallel to halve wall time.
    summary, key_findings = await asyncio.gather(
        _summarise_narrative(extracted_text, focus, title),
        _summarise_key_findings(extracted_text, focus),
    )

    if not summary:
        # One last fallback: if summarisation produced nothing, surface
        # the abstract so the caller gets SOMETHING.
        summary = abstract or f"[could not summarise {title!r}]"

    return {
        "doi": doi,
        "title": title,
        "authors": authors,
        "abstract": abstract,
        "summary": summary,
        "key_findings": key_findings,
        "focus": focus,
        "sources_used": sources_used,
        "lookup_source": lookup_source,
        "extracted_text_chars": len(extracted_text),
        "cache_size_mb": round(_warn_if_cache_bloated(), 2),
    }
