"""
Web-related MCP tools.

Provides:
- web_search: Search the web via the Brave Search API (primary, when
  BRAVE_API_KEY is set) plus a SearXNG multi-query fan-out (supplement)
- web_fetch_content: Fetch and extract content from URLs
"""

import asyncio
import logging
import os
import re
import time
from typing import Optional

import httpx

from database import SEARXNG_URL
from url_guard import BlockedURL, guarded_client, read_text_capped
from mcp.context import current_search_urls
from .llm import llm_summarize
from .query_expansion import expand_queries

logger = logging.getLogger(__name__)


# Engines passed to SearXNG on every web_search. Google is intentionally
# excluded (see docker/searxng/settings.yml for the rationale). All four
# of these are keyless HTML scrapers and are routinely CAPTCHA'd or
# access-denied from the cluster's datacenter IP (probe 2026-07-22:
# every one suspended). They are kept only as a zero-cost supplement;
# the primary web source is the keyed Brave Search API below.
_SEARXNG_ENGINES = "startpage,duckduckgo,qwant,mojeek"

# Brave Search API (api.search.brave.com) — the keyed JSON API, NOT the
# SearXNG `brave` engine (an HTML scraper, dropped 2026-06-01 after
# months of "Suspended: too many requests"). When BRAVE_API_KEY is set,
# Brave is the primary web source and SearXNG becomes best-effort
# supplement; when unset, behaviour is identical to the SearXNG-only
# path.
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
BRAVE_API_URL = "https://api.search.brave.com/res/v1/web/search"
# Free tier allows 1 req/s; paid tiers lift this. Calls are serialized
# through a min-interval throttle so a multi-query fan-out can't 429.
BRAVE_SEARCH_QPS = float(os.getenv("BRAVE_SEARCH_QPS", "1"))
# How many of the expanded query variants go to Brave (each is one
# billed request; SearXNG still gets the full fan-out for free).
BRAVE_MAX_QUERIES = int(os.getenv("BRAVE_MAX_QUERIES", "3"))

_brave_lock = asyncio.Lock()
_brave_next_ok = 0.0


async def _brave_throttle() -> None:
    """Serialize Brave calls to at most BRAVE_SEARCH_QPS requests/sec."""
    global _brave_next_ok
    async with _brave_lock:
        wait = _brave_next_ok - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _brave_next_ok = time.monotonic() + 1.0 / max(BRAVE_SEARCH_QPS, 0.1)


async def _brave_one(client: httpx.AsyncClient, q: str, count: int = 10) -> dict:
    """Single Brave Search API query.

    Returns the same batch shape as `_searxng_one` so the merge loop in
    `web_search` consumes both identically. Failures are reported as an
    unresponsive-engine entry named "brave" (never as transport_error —
    that field means "SearXNG itself unreachable" in the degradation
    logic). One retry on 429, honouring Retry-After up to 2s.
    """
    for attempt in (0, 1):
        await _brave_throttle()
        try:
            response = await client.get(
                BRAVE_API_URL,
                params={
                    "q": q,
                    "count": max(1, min(count, 20)),
                    "search_lang": "en",
                },
                headers={
                    "X-Subscription-Token": BRAVE_API_KEY,
                    "Accept": "application/json",
                },
            )
            if response.status_code == 429 and attempt == 0:
                try:
                    delay = float(response.headers.get("Retry-After", "1"))
                except ValueError:
                    delay = 1.0
                await asyncio.sleep(min(delay, 2.0))
                continue
            response.raise_for_status()
            body = response.json()
        except Exception as e:
            logger.warning("brave search %r failed: %s", q, e)
            return {
                "results": [],
                "unresponsive": [["brave", str(e)]],
                "transport_error": None,
            }
        rows = ((body.get("web") or {}).get("results")) or []
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("description", ""),
                "engine": "brave",
            }
            for r in rows
            if r.get("url")
        ]
        return {"results": results, "unresponsive": [], "transport_error": None}


def _record_url(url: str) -> None:
    """Add a result URL to the per-request allowlist consulted by
    `web_fetch_content`. Silently no-op when the ContextVar is unbound
    (which is the case in standalone tool unit tests; the gate is
    closed only when chat_service binds the set per request)."""
    bucket = current_search_urls.get()
    if bucket is not None and isinstance(url, str) and url:
        bucket.add(url)


async def _searxng_one(client: httpx.AsyncClient, q: str) -> dict:
    """Single SearXNG query.

    Returns a dict with:
        results: raw list of result dicts (possibly empty)
        unresponsive: list of (engine_name, reason) pairs for engines
                      that failed on this query. SearXNG returns this
                      as `unresponsive_engines` in its JSON envelope
                      and we propagate it so callers can distinguish
                      "no hits because the topic is obscure" from
                      "no hits because every engine was rate-limited."
        transport_error: str|None, set if the HTTP call itself
                      raised (timeout, DNS, etc.). Treated as "every
                      engine on this query is unavailable" by
                      web_search's degradation logic.
    """
    try:
        response = await client.get(
            f"{SEARXNG_URL}/search",
            params={
                "q": q,
                "format": "json",
                "engines": _SEARXNG_ENGINES,
                "language": "en",
            },
        )
        response.raise_for_status()
        body = response.json()
    except Exception as e:
        logger.warning("web_search %r failed: %s", q, e)
        return {"results": [], "unresponsive": [], "transport_error": str(e)}
    return {
        "results": body.get("results", []) or [],
        "unresponsive": body.get("unresponsive_engines", []) or [],
        "transport_error": None,
    }


# A scholarly result whose URL carries an identifier gets its real
# bibliographic record attached (chat 61443530, 2026-07-29: the model cited
# "Bazzi et al." for a PMC id and "Gonzalez-Rodriguez et al." for a PMID
# because a web hit carries a title and a URL and nothing else, so there was
# no author list to be faithful to). Bounded so a search of ordinary web
# pages costs nothing extra: only URLs that actually contain an identifier
# are resolved, at most _BIBREF_MAX_PER_SEARCH of them, concurrently.
_BIBREF_MAX_PER_SEARCH = 6


async def _attach_bibliographic(results: list[dict]) -> None:
    """Attach `bibliographic` to scholarly results, in place.

    Every result gets an explicit `metadata_available` flag: a MISSING
    authors field reads to the model as "not applicable", while an explicit
    false reads as "unknown, do not guess". That distinction is the whole
    point of this function.
    """
    import bibref

    candidates = []
    for r in results:
        r["metadata_available"] = False
        ident = bibref.extract_identifier(r.get("url") or "")
        if ident and len(candidates) < _BIBREF_MAX_PER_SEARCH:
            candidates.append((r, ident))

    if not candidates:
        return

    async def _one(entry: dict, ident: tuple) -> None:
        meta = await bibref.resolve(*ident)
        if not meta or not meta.get("authors"):
            return
        entry["bibliographic"] = {
            "title": meta.get("title"),
            "authors": meta.get("authors"),
            "year": meta.get("year"),
            "journal": meta.get("journal"),
            "doi": meta.get("doi"),
            "pmid": meta.get("pmid"),
            "pmcid": meta.get("pmcid"),
            "metadata_source": meta.get("source"),
        }
        # Mirrored at the top level so the ranking layers and the model see
        # the same field names they get from the corpus and OA tiers.
        entry["authors"] = meta.get("authors")
        entry["year"] = meta.get("year")
        if meta.get("doi"):
            entry["doi"] = meta["doi"]
        entry["metadata_available"] = True

    try:
        await asyncio.gather(*(_one(e, i) for e, i in candidates))
    except Exception:
        # Enrichment is strictly additive: a failure here must never fail
        # the search that would otherwise have succeeded.
        logger.exception("bibliographic enrichment failed")


async def web_search(
    query: Optional[str] = None,
    queries: Optional[list[str]] = None,
    top_k: int = 10,
) -> dict:
    """
    Search the web: Brave Search API (primary, when BRAVE_API_KEY is set)
    plus SearXNG multi-query fan-out (supplement / keyless fallback).

    Behaviour:
        - If `queries` is a non-empty list: run exactly those queries in
          parallel. No expansion.
        - Elif `query` is a non-empty string: expand it into 3-5 variants via
          `query_expansion.expand_queries`, then run each variant in parallel.
        - Results are deduped by URL and returned ranked by how many queries
          surfaced each URL (ties broken by the best per-query position).

    Args:
        query: Single search query. Expanded automatically.
        queries: Explicit list of queries. Takes precedence; no expansion.
        top_k: Max number of deduped results to return globally.

    Returns:
        Dict with:
            results: list of {title, url, snippet, matched_by: int, engine}
            queries_executed: list of the query strings actually run
            total_hits: total raw results before dedup
    """
    # Egress guard (cost/privacy): when the request set egress != full (e.g. a
    # benchmark run sending X-Munin-Egress: off), do NOT hit the paid Brave key
    # or the keyless engines. Returns the same degraded shape as an
    # all-engines-down call so the model treats it as a tool failure, not "no
    # results". Default egress is "full", so real users are unaffected.
    import provenance
    if not provenance.may_fetch(provenance.NET_WEB):
        return {"queries_executed": [], "total_hits": 0, "results": [],
                "engines_unresponsive": [["egress", "disabled (egress=off)"]],
                "warning": "Web search is disabled for this request "
                           "(egress=off). Treat as TOOL FAILURE, not 'no "
                           "information found'."}
    # Decide which queries to execute.
    if queries:
        query_list = [q.strip() for q in queries if q and q.strip()]
    elif query:
        query_list = await expand_queries(query, n=5)
    else:
        return {"error": "web_search requires either 'query' or 'queries'"}

    if not query_list:
        return {"error": "web_search got empty query list after normalization"}

    # Brave (keyed API, primary when configured) gets the first
    # BRAVE_MAX_QUERIES variants — each is a billed request, serialized
    # by the QPS throttle. SearXNG gets the full fan-out for free and
    # runs concurrently.
    brave_queries = query_list[:BRAVE_MAX_QUERIES] if BRAVE_API_KEY else []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            all_batches = await asyncio.gather(
                *(_searxng_one(client, q) for q in query_list),
                *(_brave_one(client, q, count=top_k) for q in brave_queries),
            )
    except Exception as e:
        return {"error": f"Web search failed: {str(e)}"}
    raw_batches = all_batches[: len(query_list)]
    brave_batches = all_batches[len(query_list):]

    # Merge + dedupe by URL, counting how many queries surfaced each url.
    # Also aggregate engine status across all queries so the caller can
    # tell "no hits because obscure" from "no hits because every engine
    # was rate-limited / blocked / behind a CAPTCHA" (chat 689f8df3,
    # 2026-05-06).
    seen: dict[str, dict] = {}
    total_hits = 0
    unresponsive: dict[str, str] = {}  # engine_name -> reason (last wins)
    transport_errors = 0  # SearXNG-only: "the search backend is unreachable"
    query_batch_pairs = list(zip(query_list, raw_batches)) + list(
        zip(brave_queries, brave_batches)
    )
    for q, batch in query_batch_pairs:
        for rank, item in enumerate(batch.get("results", [])):
            total_hits += 1
            url = item.get("url", "")
            if not url:
                continue
            if url not in seen:
                seen[url] = {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": (item.get("content") or "")[:500],
                    "engine": item.get("engine", ""),
                    "matched_by": 1,
                    "_best_rank": rank,
                }
            else:
                entry = seen[url]
                entry["matched_by"] += 1
                if rank < entry["_best_rank"]:
                    entry["_best_rank"] = rank
        for pair in batch.get("unresponsive", []):
            # SearXNG returns either ["name", "reason"] or {...} depending
            # on version; accept both shapes.
            if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                name, reason = str(pair[0]), str(pair[1])
            elif isinstance(pair, dict):
                name = str(pair.get("name") or pair.get("engine") or "")
                reason = str(pair.get("reason") or pair.get("error") or "")
            else:
                continue
            if name:
                unresponsive[name] = reason
        if batch.get("transport_error"):
            transport_errors += 1

    # Rank: more matches first, then earlier best rank. Drop the internal
    # tracking key before returning.
    merged = sorted(
        seen.values(),
        key=lambda r: (-r["matched_by"], r["_best_rank"]),
    )
    for r in merged:
        r.pop("_best_rank", None)

    # Bug 4b: register every result URL with the per-request allowlist so
    # web_fetch_content can distinguish "URL the model saw" from
    # hallucinated URLs (chat d3b4c98b, 2026-06-01).
    for r in merged[:top_k]:
        _record_url(r.get("url", ""))

    top = merged[:top_k]
    await _attach_bibliographic(top)

    out: dict = {
        "queries_executed": query_list,
        "total_hits": total_hits,
        "results": top,
    }

    # Surface engine degradation to the caller.
    requested_engines = [e for e in _SEARXNG_ENGINES.split(",") if e]
    if BRAVE_API_KEY:
        requested_engines.append("brave")
    if unresponsive:
        out["engines_unresponsive"] = [
            [name, reason] for name, reason in sorted(unresponsive.items())
        ]
    # Total-degradation warning: if every requested engine was unresponsive
    # AND we got no results, the tool is effectively broken on this call.
    # Emit an explicit warning string so the model surfaces "my tool is
    # degraded" to the user instead of concluding the topic doesn't exist.
    all_engines_down = (
        len(unresponsive) >= len(requested_engines) and len(requested_engines) > 0
    )
    transport_total = transport_errors == len(query_list) and len(query_list) > 0
    # When Brave ran and answered cleanly, a zero-hit response is a real
    # "no information found" even if every SearXNG scraper was down or
    # SearXNG itself was unreachable — don't cry TOOL FAILURE.
    brave_ok = bool(brave_queries) and "brave" not in unresponsive
    if total_hits == 0 and (all_engines_down or transport_total) and not brave_ok:
        if transport_total:
            reason = (
                f"Could not reach the search backend ({SEARXNG_URL}) on "
                f"any of the {len(query_list)} queries."
            )
        else:
            engine_list = ", ".join(sorted(unresponsive.keys()))
            reason = (
                f"All configured search engines ({engine_list}) were "
                "unresponsive on this call (rate-limited, blocked, or "
                "behind a CAPTCHA)."
            )
        out["warning"] = (
            f"{reason} Treat this zero-result response as TOOL FAILURE, "
            "not 'no information found'. Tell the user the search "
            "backend is degraded and ask them to provide a direct URL "
            "or DOI if they have one. Do not infer that the topic is "
            "obscure or non-existent from this result."
        )

    return out


# Map-reduce summarization parameters (tunable)
_FETCH_CHUNK_CHARS = 2000       # ~500 tokens per chunk
_FETCH_MAX_CHUNKS = 20          # hard cap: 40k chars of original content
_FETCH_CHUNK_SUMMARY_TOKENS = 400   # per-chunk summary budget
_FETCH_META_SUMMARY_TOKENS = 900    # final meta-summary budget
_FETCH_SHORT_SUMMARY_TOKENS = 700   # single-chunk direct summary budget
_FETCH_MAX_TOTAL_CHARS = _FETCH_CHUNK_CHARS * _FETCH_MAX_CHUNKS
# vLLM serves at --max-num-seqs 2; cap our fan-out to a small over-subscription
# so the loop doesn't queue 20 requests behind 2 GPU slots.
_FETCH_CHUNK_CONCURRENCY = 4


def _chunk_for_summarization(text: str, target_chars: int = _FETCH_CHUNK_CHARS) -> list[str]:
    """
    Split text into ~target_chars chunks along paragraph boundaries.
    Oversized paragraphs are hard-split; the last chunk absorbs whatever's
    left. Returns `[text]` unchanged if the whole thing already fits.
    """
    import re

    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= target_chars:
        return [text]

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        # No paragraph breaks found — slice by char count.
        return [text[i : i + target_chars] for i in range(0, len(text), target_chars)]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) > target_chars:
            if current:
                chunks.append(current)
                current = ""
            while len(para) > target_chars:
                chunks.append(para[:target_chars])
                para = para[target_chars:]
            current = para
            continue
        if not current:
            current = para
        elif len(current) + len(para) + 2 <= target_chars:
            current = f"{current}\n\n{para}"
        else:
            chunks.append(current)
            current = para
    if current:
        chunks.append(current)
    return chunks


# Publisher and index pages carry their bibliographic record in Highwire /
# Dublin Core meta tags. We used to throw that away: trafilatura extracts the
# body text, the summarizer condenses it, and the metadata never reaches the
# caller. Asked "extract all authors" from a PubMed page, the summarizer
# answered "the provided text does not list any authors" (chat 61443530)
# because by then the authors really were gone. Parse them structurally,
# before any LLM sees the page.
_META_RE = re.compile(
    r"<meta\s+[^>]*?(?:name|property)\s*=\s*[\"']([^\"']+)[\"'][^>]*?"
    r"content\s*=\s*[\"']([^\"']*)[\"']",
    re.I | re.S,
)
# Same attributes in the other order (content= before name=), which is just
# as common in the wild.
_META_RE_REV = re.compile(
    r"<meta\s+[^>]*?content\s*=\s*[\"']([^\"']*)[\"'][^>]*?"
    r"(?:name|property)\s*=\s*[\"']([^\"']+)[\"']",
    re.I | re.S,
)

# Interstitials that are not the page you asked for. A PMC fetch returned 131
# characters of "Checking your browser" and the summarizer dutifully described
# the security notice as though it were the article.
_BOT_CHECK_MARKERS = (
    "checking your browser",
    "security verification",
    "verifying you are human",
    "enable javascript and cookies",
    "captcha",
    "unusual traffic",
    "request could not be processed at this time",
    "preparing to download",
)
_BOT_CHECK_MAX_CHARS = 600


def _meta_pairs(html: str) -> list[tuple[str, str]]:
    pairs = [(n.lower().strip(), v.strip()) for n, v in _META_RE.findall(html or "")]
    pairs += [(n.lower().strip(), v.strip()) for v, n in _META_RE_REV.findall(html or "")]
    return pairs


def extract_meta_bibliographic(html: str) -> dict:
    """Pull citation metadata out of a page's meta tags.

    Handles Highwire (`citation_author`, repeated once per author),
    Dublin Core (`dc.creator`), and the OpenGraph title as a last resort.
    Returns {} when the page carries none, which is the normal case for an
    ordinary web page.
    """
    if not html:
        return {}
    authors: list[str] = []
    out: dict = {}
    for name, value in _meta_pairs(html):
        if not value:
            continue
        if name in ("citation_author", "dc.creator", "citation_authors"):
            # citation_authors (plural) packs several names into one tag.
            for part in re.split(r";|\s+and\s+", value):
                part = part.strip()
                if part and part not in authors:
                    authors.append(part)
        elif name in ("citation_title", "dc.title") and "title" not in out:
            out["title"] = value
        elif name in ("citation_journal_title", "citation_journal") and "journal" not in out:
            out["journal"] = value
        elif name == "citation_doi" and "doi" not in out:
            out["doi"] = value.replace("doi:", "").strip()
        elif name == "citation_pmid" and "pmid" not in out:
            out["pmid"] = value
        elif name in ("citation_date", "citation_publication_date",
                      "citation_online_date") and "year" not in out:
            m = re.search(r"\b(1[89]\d{2}|20\d{2})\b", value)
            if m:
                out["year"] = int(m.group(1))
    if authors:
        out["authors"] = authors[:25]
    if out:
        out["metadata_source"] = "page_meta"
    return out


# Canonical resolvers: hosts where the URL IS an identifier, so there is
# nothing to hallucinate about the address itself. The phantom-URL gate was
# written for invented artifact and paper links; it also blocked a legitimate
# PubMed metadata lookup for a PMID the model had just read off a PMC page
# (chat 61443530), which is exactly the check we want it to be able to do.
# Publisher pages stay gated: only these hosts, and only with an identifier.
_CANONICAL_REF_HOSTS = (
    "doi.org", "dx.doi.org",
    "pubmed.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
    "www.ncbi.nlm.nih.gov/pmc", "arxiv.org",
)


def _is_canonical_reference_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    # Match the parsed host, not a substring: "http://127.0.0.1:6333/?x=//doi.org/10.1/x"
    # contains "//doi.org" and once passed this gate.
    try:
        parsed = httpx.URL(url)
    except Exception:
        return False
    host = (parsed.host or "").lower()
    path = parsed.path.lstrip("/").lower()
    if parsed.scheme not in ("http", "https"):
        return False
    if not any(host in (h_host, f"www.{h_host}") and path.startswith(h_path)
               for h_host, _, h_path in (h.partition("/") for h in _CANONICAL_REF_HOSTS)):
        return False
    try:
        import bibref
        return bibref.extract_identifier(url) is not None
    except Exception:
        return False


def looks_like_bot_check(content: str) -> bool:
    """True when the extracted text is an anti-bot interstitial rather than
    the page. Deliberately requires BOTH a marker and a very short extract:
    a real article that merely mentions "captcha" must not trip this."""
    if not content:
        return False
    if len(content) > _BOT_CHECK_MAX_CHARS:
        return False
    low = content.lower()
    return any(marker in low for marker in _BOT_CHECK_MARKERS)


# ---------------------------------------------------------------------------
# PMC full text via NCBI's documented API (not by scraping the HTML page)
# ---------------------------------------------------------------------------
# MEASURED 2026-08-26: on 24 URLs taken from real web_search results, 5 came
# back as anti-bot interstitials and every one of them was pmc.ncbi.nlm.nih.gov.
# That matters more than the count suggests: PMC is the primary open-access
# source for the biomedical literature this system is pointed at, and T11 put
# web_fetch's error rate at 0.678.
#
# The page is blocked to scrapers, but NCBI publishes the same article through
# efetch, which is the interface they ask programmatic clients to use. So this
# is not a way around the block; it is the front door. The same PMCID that
# returned a 131-character interstitial returns ~37k characters through efetch.
#
# Reuses bibref's NCBI identity and its process-wide <=3/s throttle rather than
# opening a second unthrottled channel to the same host.
_PMC_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def pmcid_from_url(url: str) -> Optional[str]:
    """PMCID for an NCBI-hosted PMC article URL, else None.

    Host-checked on purpose: a PMCxxxx substring in some other site's path must
    not send us to efetch with an id that host never meant.
    """
    if not url or not isinstance(url, str):
        return None
    if "ncbi.nlm.nih.gov" not in url.lower():
        return None
    m = re.search(r"/(PMC\d+)", url, re.I)
    return m.group(1).upper() if m else None


def _jats_body_text(xml_text: str) -> str:
    """Readable text from a JATS <body>, or "" when there is no body.

    Abstract-only records (no <body>) return "" so the caller reports an honest
    failure rather than passing an abstract off as the full text.
    """
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_text)
    except Exception:
        return ""
    parts = []
    for body in root.iter():
        if body.tag.rsplit("}", 1)[-1] != "body":
            continue
        for node in body.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag in ("xref", "table-wrap", "graphic", "inline-formula"):
                continue
            if node.text and node.text.strip():
                parts.append(node.text.strip())
            if node.tail and node.tail.strip():
                parts.append(node.tail.strip())
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


async def fetch_pmc_fulltext(pmcid: str) -> Optional[str]:
    """Full text for a PMCID via efetch, or None when unavailable."""
    try:
        import bibref
        await bibref._ncbi_throttle()
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(
                _PMC_EFETCH_URL,
                params={"db": "pmc", "id": pmcid, "rettype": "xml",
                        "tool": bibref._NCBI_TOOL, "email": bibref._NCBI_EMAIL},
                headers={"User-Agent": "MuninBot/1.0 (+https://muninai.org)"},
            )
            r.raise_for_status()
            text = _jats_body_text(r.text)
            return text or None
    except Exception as e:
        logger.info("PMC efetch failed for %s: %s", pmcid, e)
        return None


# Hosts that reliably refuse automated access. We do NOT try to get around
# that; we fail fast with an error the model can act on, instead of spending a
# round trip to return a bare "HTTP 403" it cannot interpret. MEASURED: 7 of 24
# real search URLs 403'd, all publisher domains.
_HARD_BLOCKED_HINT = (
    "This publisher blocks automated access. Do not retry this URL. "
    "Try the open-access route instead: look the paper up by DOI with the "
    "`source` tool, which checks Unpaywall and PMC for a readable copy."
)

async def web_fetch_content(
    url: str,
    summary_instruction: str = "Summarize the main points and key findings, focusing on factual content.",
) -> dict:
    """
    Fetch a URL, extract its main content, and return a CONDENSED SUMMARY.

    Raw content never reaches the caller — this tool always returns a
    summary. For short pages, a single `llm_summarize` call is enough. For
    long pages, the extract is chunked along paragraph boundaries, each
    chunk is summarised in parallel via separate vLLM calls, and a final
    meta-summary composes the chunk summaries into one coherent output.

    Args:
        url: The URL to fetch.
        summary_instruction: Instructions that bias the summariser (e.g.
            "focus on methodology", "only extract conclusions about X").
            Applied to both per-chunk and meta-summary prompts.

    Returns:
        Dict with:
            url: the fetched URL
            summary: condensed text (typically <2500 chars regardless of page length)
            chunks_summarized: int
            successful_chunks: int (may be < chunks_summarized if some vLLM calls failed)
            total_chars_original: int (length of extracted text before truncation)
            summary_chars: int
            truncated: bool (true when page exceeded ~40k chars)
        Or {"error": "...", "url": ...} on failure.
    """
    # Egress guard (cost/privacy): skip the live fetch when egress != full.
    import provenance
    if not provenance.may_fetch(provenance.NET_WEB):
        return {"error": "web fetch is disabled for this request (egress=off)",
                "url": url}
    # Bug 4b: gate against hallucinated URLs. The set is seeded by
    # `chat_service.stream_chat_completion` from prior tool_call results
    # AND from URLs in user-message content, then updated in place by
    # `web_search` and `web_fetch_content` (so a follow-up fetch on a
    # page that linked to another URL succeeds after that page is
    # fetched). `None` means the gate is unwired (standalone tests,
    # back-compat) and gating is skipped.
    allow = current_search_urls.get()
    if (allow is not None and isinstance(url, str) and url not in allow
            and not _is_canonical_reference_url(url)):
        return {
            "error": (
                "URL not from any recent search result. Call web_search "
                "first, then fetch a URL from the results. Do not invent "
                "URLs from prior knowledge."
            ),
            "url": url,
        }

    # PMC is served through NCBI's API, not scraped: the HTML page returns an
    # anti-bot interstitial to us, while efetch returns the same article.
    # PMC is read through NCBI's API rather than scraped: the HTML page returns
    # an anti-bot interstitial to us, while efetch returns the same article.
    # `html` stays empty on that path, so the meta-tag biblio step below finds
    # nothing and falls through to bibref, which resolves a PMCID properly.
    html = ""
    content = None
    pmcid = pmcid_from_url(url)
    if pmcid:
        content = await fetch_pmc_fulltext(pmcid)
        # No body (abstract-only or withdrawn record) leaves content None and
        # falls through: the landing page is often still readable.

    if content is None:
        try:
            import trafilatura

            async with guarded_client(timeout=30.0, follow_redirects=True) as client:
                async with client.stream("GET", url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; MuninBot/1.0; +https://muninai.org)"
                }) as response:
                    response.raise_for_status()
                    html = await read_text_capped(response)
        except BlockedURL as e:
            return {"error": f"URL not allowed: {e}", "url": url}
        except httpx.TimeoutException:
            return {"error": f"Request timed out for URL: {url}", "url": url}
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code in (401, 402, 403, 451):
                return {"error": f"HTTP {code} for URL: {url}. {_HARD_BLOCKED_HINT}",
                        "url": url, "blocked_by_publisher": True}
            return {"error": f"HTTP {code} for URL: {url}", "url": url}
        except Exception as e:
            return {"error": f"Failed to fetch URL: {str(e)}", "url": url}

        try:
            content = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            )
        except Exception as e:
            return {"error": f"Content extraction failed: {str(e)}", "url": url}

    if not content:
        return {"error": "Could not extract content from URL", "url": url}

    # Resolve the page's bibliographic record BEFORE summarising, from its
    # meta tags, falling back to the identifier in the URL. Both paths are
    # structural: no LLM is involved, so nothing can be invented here.
    biblio = extract_meta_bibliographic(html)
    if not biblio.get("authors"):
        try:
            import bibref
            resolved = await bibref.resolve_url(url)
            if resolved and resolved.get("authors"):
                biblio = {k: v for k, v in {
                    "title": resolved.get("title"),
                    "authors": resolved.get("authors"),
                    "year": resolved.get("year"),
                    "journal": resolved.get("journal"),
                    "doi": resolved.get("doi"),
                    "pmid": resolved.get("pmid"),
                    "pmcid": resolved.get("pmcid"),
                    "metadata_source": resolved.get("source"),
                }.items() if v}
        except Exception:
            logger.exception("bibref fallback failed for %s", url)

    if looks_like_bot_check(content):
        # Returning a "summary" of a security notice reads like content and
        # is worse than an honest failure: it is what let a 131-character
        # interstitial pass for an article.
        return {
            "error": (
                "Page returned an anti-bot interstitial, not the article "
                "(the host blocked automated access). Nothing was read."
            ),
            "blocked_by_bot_check": True,
            "url": url,
            **({"bibliographic": biblio} if biblio else {}),
        }

    total_chars = len(content)
    truncated = False
    if total_chars > _FETCH_MAX_TOTAL_CHARS:
        content = content[:_FETCH_MAX_TOTAL_CHARS]
        truncated = True

    chunks = _chunk_for_summarization(content)
    if not chunks:
        return {"error": "Extracted content was empty after chunking", "url": url}

    # --- Short path: one chunk, one summarizer call -------------------------
    if len(chunks) == 1:
        result = await llm_summarize(
            chunks[0],
            f"{summary_instruction}\n\nKeep the summary under 500 words.",
            max_tokens=_FETCH_SHORT_SUMMARY_TOKENS,
        )
        if isinstance(result, dict) and "error" in result:
            return {
                "error": f"Summarization failed: {result['error']}",
                "url": url,
            }
        summary = (result or {}).get("summary", "").strip()
        if not summary:
            return {"error": "Summarizer returned empty output", "url": url}
        return {
            "url": url,
            "summary": summary,
            "bibliographic": biblio or None,
            "chunks_summarized": 1,
            "successful_chunks": 1,
            "total_chars_original": total_chars,
            "summary_chars": len(summary),
            "truncated": truncated,
        }

    # --- Map: summarize each chunk in parallel, capped by a semaphore ------
    chunk_instruction = (
        f"{summary_instruction}\n\n"
        "This is one section of a longer web page. Summarize only this "
        "section in under 150 words. Preserve named entities, numbers, and "
        "any claims that would be needed to understand the page as a whole."
    )
    sem = asyncio.Semaphore(_FETCH_CHUNK_CONCURRENCY)

    async def _summarize_chunk(chunk_text: str) -> dict:
        async with sem:
            return await llm_summarize(
                chunk_text,
                chunk_instruction,
                max_tokens=_FETCH_CHUNK_SUMMARY_TOKENS,
            )

    chunk_results = await asyncio.gather(
        *(_summarize_chunk(c) for c in chunks),
        return_exceptions=True,
    )

    chunk_summaries: list[str] = []
    for i, r in enumerate(chunk_results):
        if isinstance(r, Exception):
            logger.warning("web_fetch chunk %d raised: %s", i, r)
            continue
        if not isinstance(r, dict):
            continue
        if "error" in r:
            logger.warning("web_fetch chunk %d error: %s", i, r["error"])
            continue
        text = (r.get("summary") or "").strip()
        if text:
            chunk_summaries.append(f"[Section {i + 1}]\n{text}")

    if not chunk_summaries:
        return {
            "error": "All chunk summarizations failed",
            "url": url,
            "total_chars_original": total_chars,
        }

    # --- Reduce: compose chunk summaries into one coherent summary --------
    combined_chunk_text = "\n\n".join(chunk_summaries)
    meta_instruction = (
        f"Below are summaries of sequential sections of a single web page. "
        f"Compose them into one coherent summary focused on: "
        f"{summary_instruction}\n\n"
        "Keep the final summary under 600 words. Remove duplicate points. "
        "Do not refer to the sections by number in the output."
    )
    meta_result = await llm_summarize(
        combined_chunk_text,
        meta_instruction,
        max_tokens=_FETCH_META_SUMMARY_TOKENS,
    )

    if isinstance(meta_result, dict) and "summary" in meta_result and meta_result["summary"]:
        summary = meta_result["summary"].strip()
    else:
        # Fallback: strip the `[Section N]` tags and join the chunk summaries
        # directly. Less polished but still condensed.
        import re
        summary = "\n\n".join(
            re.sub(r"^\[Section \d+\]\n?", "", s).strip()
            for s in chunk_summaries
        )
        logger.warning("web_fetch meta-summary failed, returning concatenated chunks")

    return {
        "url": url,
        "summary": summary,
        "bibliographic": biblio or None,
        "chunks_summarized": len(chunks),
        "successful_chunks": len(chunk_summaries),
        "total_chars_original": total_chars,
        "summary_chars": len(summary),
        "truncated": truncated,
    }
