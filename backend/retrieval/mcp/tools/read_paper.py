"""
Paper fetch/parse/summarise building blocks (formerly the read_paper
MCP tool, §7).

The read_paper tool was retired once the `source` agent superseded it;
this module is kept because `source` (mcp/tools/source.py) reuses its
helpers: PDF resolution/cache (`_download_pdf`, `_read_cache`,
`_write_cache`, `_warn_if_cache_bloated`) and the two summarisers
(`_summarise_narrative`, `_summarise_key_findings`). The top-level
`read_paper()` entry point is gone; the helpers below are the surface.

Chains infrastructure we already have:

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
import logging
import os
from typing import Optional

import httpx

from url_guard import guarded_client

logger = logging.getLogger(__name__)

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
        logger.warning(
            "papers_cached exceeded %d GB (current: %.1f MB). No eviction "
            "runs automatically; manually clean %s if needed.",
            PAPERS_CACHE_WARN_GB, size_mb, CACHE_DIR,
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
        logger.warning("papers_cached write failed for %s: %s", doi, exc)


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
        async with guarded_client(timeout=60.0, follow_redirects=True) as client:
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

