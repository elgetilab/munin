"""
MCP tool: source (the read agent).

`source(refs[], mode)` reads 1..N documents and returns a grounded result. It
consolidates and supersedes `read_paper` (summary) and `compare_papers`
(compare), and adds the two modes the diagnosis showed were missing:

  mode=qa       answer a specific question from the FULL text (the bottleneck
                fix - read_paper summarised-and-discarded, dropping the buried
                numeric the answer needed; the full-text oracle flipped 9/11
                over-abstentions to correct at 0.82).
  mode=extract  pull structured rows against a schema; returns a HANDLE + a
                5-row preview, never the rows (the model can't transcribe values
                it never held). Feeds the Compute agent.

Design contract (AGENT-IMPLEMENTATION-PLAN.md, D14-D18):
  - Named `source` (not paper/document): neutral across corpus papers,
    preprints, and web.
  - Tagged refs, fuzzy allowed: {doi}|{arxiv}|{id}|{url}|{title,...}. A fuzzy
    match resolves or returns `ambiguous(candidates[])` - never silently takes
    the top hit. A {doi} never seen in a prior tool result is flagged in the
    trace as generated (confabulated-DOI audit).
  - Four-way(+out_of_scope) outcome: the envelope always says WHICH way a call
    failed (not a single `found=false` bool).
  - Interior pinned: resolve -> extract -> one LLM call. Full text in one call;
    chunking only as an overflow fallback.
  - Honours the egress / corpus_scope provenance controls; emits a trace.

Reuses read_paper's building blocks (resolution, PDF fetch/cache, GROBID
extraction, summarisers) rather than duplicating them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Optional

import httpx

import provenance as P
from agent_trace import AgentTrace
from url_guard import BlockedURL, guarded_client, read_text_capped
from database import VLLM_MODEL_NAME, VLLM_URL, thinking_off, LLM_HEADERS
import site_config

logger = logging.getLogger(__name__)

# Unpaywall is the canonical open-access PDF resolver (far broader coverage than
# Semantic Scholar's openAccessPdf field, and it returns direct PDF links). It is
# the OA full-text lever: without it, OA papers not in the local corpus fall back
# to abstract-only, which qa mode then abstains on. Requires a contact email per
# their API policy.
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL") or site_config.CONTACT_EMAIL


_PDF_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Browser-like headers for web-page fetches. A bare UA is enough for some
# hosts; the Accept* pair gets past a few naive blocks (not hard bot walls).
_WEB_HEADERS = {
    "User-Agent": _PDF_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Web fetch retries. The DR loop fires several source reads at once; NCBI/PMC
# in particular rate-limits the burst (a page that 200s when fetched alone can
# 429/403 mid-run). Retrying with backoff recovers those. Hard datacenter-IP
# bot walls (e.g. MDPI Cloudflare) 403 on every attempt and are given up on -
# nothing at the fetch layer bypasses them; OA/corpus tiers cover those papers.
_WEB_FETCH_ATTEMPTS = 3
_WEB_RETRY_STATUSES = frozenset({401, 403, 429, 500, 502, 503, 504})
_WEB_MIN_TEXT_CHARS = 200


def _web_should_retry(status_code: int) -> bool:
    """Whether an HTTP status warrants another attempt (transient block/error)."""
    return status_code in _WEB_RETRY_STATUSES


def _web_backoff(attempt: int, retry_after: Optional[str]) -> float:
    """Seconds to wait before the next attempt. Honours a numeric Retry-After
    header (capped), else exponential 0.5/1/2s (capped at 4s)."""
    if retry_after:
        try:
            return min(float(retry_after), 5.0)
        except (TypeError, ValueError):
            pass
    return min(0.5 * (2 ** attempt), 4.0)


async def _unpaywall_pdf_urls(doi: str) -> list[str]:
    """Candidate OA PDF URLs for a DOI via Unpaywall, best first. Repository
    copies (PMC, institutional) are ordered ahead of publisher copies, which
    frequently block scripted downloads with an HTML interstitial or 403."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"https://api.unpaywall.org/v2/{doi}",
                                 params={"email": UNPAYWALL_EMAIL})
        if r.status_code != 200:
            return []
        locs = [l for l in (r.json().get("oa_locations") or []) if l.get("url_for_pdf")]
        # repository before publisher; PMC first within repositories.
        def rank(l: dict) -> tuple:
            host = l.get("host_type") or ""
            url = l.get("url_for_pdf") or ""
            return (0 if host == "repository" else 1, 0 if "ncbi.nlm.nih.gov" in url else 1)
        locs.sort(key=rank)
        return [l["url_for_pdf"] for l in locs]
    except Exception as exc:  # noqa: BLE001 - resolver failure is non-fatal
        logger.info("unpaywall lookup failed for %s: %s", doi, exc)
        return []


async def _download_valid_pdf(url: str) -> Optional[bytes]:
    """Download a URL with a browser UA and return the bytes only if they are an
    actual PDF (magic `%PDF-`). Publishers often return an HTML anti-bot page
    with a 200, which would otherwise be 'extracted' into garbage and abstained
    on; validating the magic bytes lets the caller skip to the next candidate."""
    try:
        async with guarded_client(timeout=60.0, follow_redirects=True,
                                  headers={"User-Agent": _PDF_UA}) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    return None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
                    if len(buf) > 50 * 1024 * 1024:
                        break
        data = bytes(buf)
        return data if data[:5] == b"%PDF-" else None
    except Exception:  # noqa: BLE001
        return None

# qa/extract send the FULL paper text (not llm_summarize's 30k cap). 140k chars
# ~= 35k tokens, comfortably inside the 65k context with room for the question
# and answer. Above this we fall back to chunk-ranking (overflow only, D15).
MAX_FULLTEXT_CHARS = 140_000

# Where extract-mode rows land as a handle (handle-not-payload). A thin on-disk
# JSON store until the general store lands with Deep Research.
EXTRACT_DIR = os.getenv("AGENT_EXTRACT_DIR", "/data/agent_extracts")

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Ref resolution (tagged, fuzzy allowed)
# ---------------------------------------------------------------------------

def _normalize_doi(doi: str) -> str:
    return (doi or "").strip().lower()


def _coerce_ref(ref: Any) -> dict:
    """Accept a tagged dict or a bare string; return a {kind: value} dict.

    A bare string that looks like a DOI is tagged {doi}; anything else is
    treated as a {title} query (fuzzy)."""
    if isinstance(ref, dict):
        return ref
    if isinstance(ref, str):
        s = ref.strip()
        m = _DOI_RE.search(s)
        if m:
            return {"doi": m.group(0)}
        if s.lower().startswith(("http://", "https://")):
            return {"url": s}
        return {"title": s}
    return {}


def _doi_from_ref(ref: dict) -> Optional[str]:
    """Best-effort DOI for the non-fuzzy tags."""
    if ref.get("doi"):
        return str(ref["doi"]).strip()
    if ref.get("arxiv"):
        return f"10.48550/arXiv.{str(ref['arxiv']).strip()}"
    if ref.get("url"):
        m = _DOI_RE.search(str(ref["url"]))
        if m:
            return m.group(0)
    if ref.get("id") and _DOI_RE.search(str(ref["id"])):
        return str(ref["id"]).strip()
    return None


def _ref_was_seen(doi: str) -> Optional[bool]:
    """Was this DOI in a prior tool result this conversation? None = audit not
    wired (can't tell); True/False otherwise (confabulated-DOI audit, D16)."""
    try:
        from mcp.context import current_seen_dois
        seen = current_seen_dois.get()
    except Exception:
        seen = None
    if seen is None:
        return None
    return _normalize_doi(doi) in {_normalize_doi(d) for d in seen}


async def _resolve_title(title: str, tr: AgentTrace) -> dict:
    """Fuzzy title -> {resolved_doi} or {ambiguous: [candidates]}."""
    from mcp.tools.papers import paper_search
    res = await paper_search(query=title, top_k=5)
    hits = (res or {}).get("results") or (res or {}).get("papers") or []
    cands = [{"doi": h.get("doi"), "title": h.get("title"),
              "score": h.get("score")} for h in hits if h.get("doi")]
    tr.decide("fuzzy title resolve", title=title, n_candidates=len(cands))
    if not cands:
        return {"ambiguous": []}
    # Clear winner only if the top score meaningfully beats the runner-up.
    top = cands[0]
    if len(cands) == 1:
        return {"resolved_doi": top["doi"]}
    s0, s1 = top.get("score") or 0, cands[1].get("score") or 0
    if isinstance(s0, (int, float)) and isinstance(s1, (int, float)) and s0 - s1 >= 0.05:
        return {"resolved_doi": top["doi"], "candidates": cands}
    return {"ambiguous": cands}


# ---------------------------------------------------------------------------
# Fetch + extract (reuses read_paper's building blocks; returns FULL text)
# ---------------------------------------------------------------------------

async def _fetch_and_extract(doi: str, tr: AgentTrace) -> dict:
    """Resolve a DOI to metadata + full extracted text, tagging the origin and
    honouring egress/corpus_scope. Never summarises.

    Returns {outcome, ref_resolved, source, full_text, abstract, ...}.
    """
    from mcp.tools.papers import paper_lookup, get_pdf_path
    from mcp.tools.read_paper import (
        _download_pdf, _read_cache, _write_cache, _warn_if_cache_bloated,
    )
    import document_store

    lookup = await paper_lookup(doi)
    if not isinstance(lookup, dict) or lookup.get("error"):
        return {"outcome": "unresolved",
                "reason": (lookup or {}).get("error", "paper_lookup failed"),
                "ref_resolved": {"doi": doi}}

    title = lookup.get("title") or ""
    authors = lookup.get("authors") or []
    abstract = lookup.get("abstract") or lookup.get("tldr") or ""
    ref_resolved = {"doi": doi, "title": title, "authors": authors[:10],
                    "internal_id": lookup.get("id")}

    # Locate the PDF, tagging where it came from (origin drives provenance).
    origin = url = None
    pdf_bytes = None
    local_path = get_pdf_path(doi)
    if local_path and os.path.isfile(local_path):
        origin, url = P.ORIGIN_LOCAL_KB, local_path
        with open(local_path, "rb") as f:
            pdf_bytes = f.read()
    else:
        cached = _read_cache(doi)
        if cached:
            origin, pdf_bytes = P.ORIGIN_OA_CACHE, cached
        elif P.may_fetch(P.NET_OA_DOWNLOAD):
            # Try OA PDF URLs until one yields a VALID pdf: the S2 lookup's URL,
            # then Unpaywall's (repository copies first). Each download is
            # magic-byte checked, so a publisher HTML interstitial is skipped
            # rather than mis-extracted - this is what turns OA papers from
            # abstract-only (abstain) into full-text reads.
            oa_urls: list[str] = []
            if lookup.get("open_access_pdf"):
                oa_urls.append(lookup["open_access_pdf"])
            for u in await _unpaywall_pdf_urls(doi):
                if u not in oa_urls:
                    oa_urls.append(u)
            for oa_url in oa_urls[:5]:
                fetched = await _download_valid_pdf(oa_url)
                if fetched:
                    origin, url, pdf_bytes = P.ORIGIN_OA_DOWNLOAD, oa_url, fetched
                    _write_cache(doi, fetched)
                    _warn_if_cache_bloated()
                    tr.decide("OA full-text fetched", url=oa_url)
                    break

    # Provenance gate: a source may exist but be out of the active corpus_scope
    # (e.g. an OA download under corpus_scope=curated_only). That is a distinct
    # outcome from "no source at all".
    if origin and not P.may_use(origin):
        tr.decide("blocked by corpus_scope", origin=origin,
                  scope=P.get_corpus_scope())
        return {"outcome": "out_of_scope", "reason": f"origin={origin} not in scope",
                "ref_resolved": ref_resolved, "abstract": abstract,
                "source": {"origin": origin, "url": url}}

    if pdf_bytes is None:
        # No in-scope full text. Abstract-only is a shallow read, not a full one.
        if abstract:
            return {"outcome": "resolved", "read_depth": "abstract",
                    "ref_resolved": ref_resolved, "abstract": abstract,
                    "full_text": abstract,
                    "source": {"origin": P.ORIGIN_LOCAL_KB if local_path else "s2_abstract",
                               "url": url, "extraction_method": "abstract"}}
        return {"outcome": "unresolved",
                "reason": "no in-scope PDF and no abstract",
                "ref_resolved": ref_resolved}

    try:
        text = await document_store._extract_pdf(pdf_bytes)
    except Exception as exc:
        logger.warning("source extraction failed for %s: %s", doi, exc)
        text = ""
    if not text or len(text) < 200:
        if abstract:
            return {"outcome": "resolved", "read_depth": "abstract",
                    "ref_resolved": ref_resolved, "abstract": abstract,
                    "full_text": abstract,
                    "source": {"origin": origin, "url": url,
                               "extraction_method": "abstract_fallback"}}
        return {"outcome": "extraction_failed",
                "reason": "PDF extraction empty (scanned image or GROBID failure)",
                "ref_resolved": ref_resolved,
                "source": {"origin": origin, "url": url}}

    tr.source(ref_resolved)
    return {"outcome": "resolved", "read_depth": "full_text",
            "ref_resolved": ref_resolved, "abstract": abstract,
            "full_text": text,
            "source": {"origin": origin, "url": url, "extraction_method": "pdf_text"}}


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

async def _vllm_answer(system: str, user: str, *, max_tokens: int = 4096,
                       temperature: float = 0.7,
                       enable_thinking: bool = True) -> dict:
    """Direct vLLM call over the FULL text (no llm_summarize 30k truncation).
    Mirrors the ceiling-oracle call that reached 0.82."""
    body = {
        "model": VLLM_MODEL_NAME,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens, "temperature": temperature, "stream": False,
    }
    if not enable_thinking:
        thinking_off(body)
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body,
                                  headers=LLM_HEADERS)
        if r.status_code != 200:
            return {"error": f"vLLM {r.status_code}: {r.text[:200]}"}
        choices = r.json().get("choices") or []
        if not choices:
            return {"error": "vLLM returned no choices"}
        return {"content": (choices[0].get("message") or {}).get("content") or ""}
    except httpx.TimeoutException:
        return {"error": "vLLM request timed out"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"vLLM call failed: {exc}"}


_QA_SYSTEM = (
    "You are a research assistant. Use the FULL paper text provided to answer "
    "the question; you may also draw on your own knowledge. The exact answer (a "
    "number, a value, a specific result) may be buried in the body, a table, or "
    "a figure caption. Give brief reasoning, then two final lines EXACTLY:\n"
    "Answer: <your answer, or the string INSUFFICIENT if the text and your "
    "knowledge genuinely cannot determine it>\n"
    "Quote: \"<the exact supporting sentence copied verbatim from the text, or "
    "NONE>\""
)

_INSUFFICIENT_RE = re.compile(r"answer:\s*insufficient", re.IGNORECASE)
_QUOTE_RE = re.compile(r"quote:\s*\"?(.+?)\"?\s*$", re.IGNORECASE | re.MULTILINE)


def _parse_qa(content: str) -> dict:
    """Split the model's answer into {answer, quote, abstained}. The full
    `content` is kept as `answer` so downstream MCQ letter-parsing still works.

    An EMPTY answer counts as abstained. `_vllm_answer` runs with thinking on
    and max_tokens=4096, so on a long full-text prompt the <think> trace can
    consume the whole budget and vLLM returns empty content. Without this the
    envelope reported `outcome: resolved, abstained: False` with `answer: ""`,
    i.e. a confident non-answer, and every caller (the read path, the LitQA2
    answering path) took it at face value. Observed 2026-08-27 on the iLOV
    reproducer. This makes the signal honest; it does NOT recover the answer,
    for which the budget itself has to move."""
    if not (content or "").strip():
        return {"answer": "", "quote": None, "abstained": True,
                "empty_completion": True}
    abstained = bool(_INSUFFICIENT_RE.search(content))
    qm = _QUOTE_RE.search(content)
    quote = None
    if qm:
        q = qm.group(1).strip()
        if q and q.upper() != "NONE":
            quote = q
    return {"answer": content.strip(), "quote": quote, "abstained": abstained}


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

async def _mode_summary(ext: dict, focus: Optional[str], tr: AgentTrace) -> dict:
    from mcp.tools.read_paper import _summarise_narrative, _summarise_key_findings
    text = ext["full_text"]
    title = ext["ref_resolved"].get("title") or ""
    import asyncio
    summary, key_findings = await asyncio.gather(
        _summarise_narrative(text, focus, title),
        _summarise_key_findings(text, focus),
    )
    tr.llm(2)
    return {"summary": summary or ext.get("abstract") or "",
            "key_findings": key_findings}


async def _mode_qa(ext: dict, question: str, tr: AgentTrace) -> dict:
    text = ext["full_text"]
    if len(text) > MAX_FULLTEXT_CHARS:
        tr.decide("full-text over budget; truncating (chunk-rank overflow TODO)",
                  chars=len(text))
        text = text[:MAX_FULLTEXT_CHARS]
    user = f"Full text of the paper:\n{text}\n\n---\n\nQuestion: {question}"
    res = await _vllm_answer(_QA_SYSTEM, user)
    tr.llm(1)
    if res.get("error"):
        return {"_llm_error": res["error"]}
    parsed = _parse_qa(res["content"])
    return parsed


_EXTRACT_SYSTEM = (
    "You extract structured data from a research paper. Return ONLY a JSON object "
    "with keys `schema` (a list of column names you inferred) and `rows` (a list "
    "of objects using those columns). Prefer quantitative fields and include a "
    "`quote` column with the verbatim sentence each row came from. No prose."
)


async def _mode_extract(ext: dict, question: Optional[str], schema: Optional[list],
                        tr: AgentTrace) -> dict:
    text = ext["full_text"][:MAX_FULLTEXT_CHARS]
    ask = f"Extract: {question}" if question else "Extract the paper's key quantitative results."
    if schema:
        ask += f"\nUse exactly these columns: {schema}."
    res = await _vllm_answer(_EXTRACT_SYSTEM, f"{ask}\n\nText:\n{text}",
                             max_tokens=3000, enable_thinking=False)
    tr.llm(1)
    if res.get("error"):
        return {"_llm_error": res["error"]}
    try:
        obj = json.loads(re.search(r"\{.*\}", res["content"], re.DOTALL).group(0))
    except Exception:
        return {"_llm_error": "extract did not return parseable JSON"}
    rows = obj.get("rows") or []
    out_schema = schema or obj.get("schema") or (list(rows[0].keys()) if rows else [])
    # handle-not-payload: rows land on disk; the model gets a handle + preview.
    handle = "ex_" + uuid.uuid4().hex[:16]
    try:
        os.makedirs(EXTRACT_DIR, exist_ok=True)
        with open(os.path.join(EXTRACT_DIR, handle + ".json"), "w") as f:
            json.dump({"schema": out_schema, "rows": rows,
                       "provenance": ext["ref_resolved"]}, f)
    except OSError as exc:
        return {"_llm_error": f"extract handle write failed: {exc}"}
    return {"handle": handle, "schema": out_schema, "n_rows": len(rows),
            "preview": rows[:5], "provenance": ext["ref_resolved"]}


_FINDINGS_SYSTEM = (
    "You extract the DISTINCT findings in a document that bear on a specific "
    "question. Return ONLY a JSON array of up to 4 objects, each "
    "{\"claim\": \"...\", \"quote\": \"...\"}: `claim` is one specific factual "
    "finding relevant to the question; `quote` is the verbatim sentence copied "
    "from the text that supports it. Include ONLY findings the text actually "
    "states - if the text does not address the question, return []. Do not repeat "
    "the same finding, and do not invent quotes."
)


_TRIAGE_SYSTEM = (
    "You decide whether a document is worth reading in full to answer a research "
    "question. Answer YES if the document contains ANY specific finding that bears "
    "on the question. Answer NO only if the document clearly does not address the "
    "question at all. When uncertain, answer YES. "
    "Reply with exactly one word: YES or NO."
)
# Full-text triage before the expensive extraction. Measured 2026-07-24 against
# 16 papers whose real read outcome was already known: it passed 2/2 papers that
# went on to yield grounded notes and skipped 14/14 that returned nothing, at
# ~0.8s versus ~15-20s for the extraction it replaces. Metadata-level relevance
# could not do this (bi-encoder AUC 0.655, cross-encoder 0.616): the signal that
# predicts a useful read lives in the full text, not the title or abstract.
#
# Recall is what matters here, not precision. A wrongly skipped paper is a silent
# unrecoverable drop (D8), so the prompt is biased to YES, the call FAILS OPEN
# (any error proceeds to extraction), and every skip is traced.
TRIAGE_ENABLED = (os.getenv("SOURCE_TRIAGE_ENABLED", "1").strip().lower()
                  not in ("0", "false", "no"))


async def _triage_worth_reading(text: str, question: str, tr: AgentTrace) -> bool:
    """Cheap yes/no gate over the FULL text. Returns True when the document
    should go on to the expensive findings extraction. Fails open."""
    if not TRIAGE_ENABLED:
        return True
    try:
        res = await _vllm_answer(_TRIAGE_SYSTEM,
                                 f"Question: {question}\n\nDocument:\n{text}",
                                 max_tokens=8, temperature=0.0,
                                 enable_thinking=False)
        tr.llm(1)
        if res.get("error"):
            return True                      # fail open
        return "YES" in (res.get("content") or "").upper()
    except Exception as exc:  # noqa: BLE001
        logger.warning("triage failed, proceeding to extraction: %s", exc)
        return True                          # fail open


async def _mode_findings(ext: dict, question: str, tr: AgentTrace) -> dict:
    """Multi-note: up to 4 grounded findings (claim + verbatim quote) from the
    full text. Each becomes a separate citation in Deep Research, so one paper can
    contribute several findings - without loosening grounding (every claim keeps
    its quote)."""
    text = ext["full_text"][:MAX_FULLTEXT_CHARS]

    # Gate the expensive extraction on a cheap full-text relevance check. ~74% of
    # DR reads return nothing; each of those was paying for an 8000-token
    # thinking pass to discover that. Skipping them is what buys the budget to
    # read more candidates.
    if not await _triage_worth_reading(text, question, tr):
        tr.decide("triaged out before extraction", question=question,
                  title=(ext.get("ref_resolved") or {}).get("title"))
        return {"findings": [], "triaged_out": True}

    user = f"Full text:\n{text}\n\n---\n\nQuestion: {question}"
    # Thinking MUST stay on: qwen3.6 needs to reason "does this text address the
    # question -> extract the finding -> copy the quote". With thinking disabled
    # it abstains and returns [] even on plainly relevant text (empty reports;
    # observed after the 2026-07 cluster restart). `qa` mode already relies on
    # thinking. Budget 8000 so the reasoning pass has headroom before the array
    # (a 3000-token cap truncated the whole completion to empty in testing).
    res = await _vllm_answer(_FINDINGS_SYSTEM, user, max_tokens=8000)
    tr.llm(1)
    if res.get("error"):
        return {"_llm_error": res["error"]}
    # Strip any exposed reasoning block, then greedily grab the outermost JSON
    # array (`\[.*\]`, DOTALL) so a `]` inside a quote can't truncate it early.
    content = re.sub(r"<think>.*?</think>", "", res.get("content") or "", flags=re.DOTALL)
    m = re.search(r"\[.*\]", content, re.DOTALL)
    if not m:
        return {"findings": []}
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return {"findings": []}
    findings = [{"claim": str(f["claim"]).strip(), "quote": str(f.get("quote") or "").strip()}
                for f in arr if isinstance(f, dict) and f.get("claim")]
    return {"findings": findings[:4]}


async def _fetch_web_text(url: str) -> tuple[Optional[str], Optional[str], str]:
    """Fetch a web page and extract its main text (trafilatura), with bounded
    retry on transient blocks/errors and a precision->recall extraction
    fallback.

    Returns `(text, title, status)` where status is:
        "ok"      - text extracted
        "blocked" - last response was an auth/rate block (401/403/429) that
                    survived the retries (typically a datacenter-IP bot wall)
        "empty"   - fetched 200 but no article text could be extracted
        "error"   - the page could not be fetched (network / non-retryable)
    """
    import asyncio

    import trafilatura

    html = None
    status = "error"
    for attempt in range(_WEB_FETCH_ATTEMPTS):
        try:
            async with guarded_client(timeout=30.0, follow_redirects=True,
                                      headers=_WEB_HEADERS) as client:
                async with client.stream("GET", url) as r:
                    body = await read_text_capped(r) if r.status_code == 200 else ""
        except BlockedURL as exc:  # internal/non-http target: never retry
            logger.info("web fetch %s refused: %s", url, exc)
            status = "error"
            break
        except Exception as exc:  # noqa: BLE001
            logger.info("web fetch %s attempt %d error: %s", url, attempt, exc)
            status = "error"
            if attempt < _WEB_FETCH_ATTEMPTS - 1:
                await asyncio.sleep(_web_backoff(attempt, None))
            continue
        if r.status_code == 200 and body:
            html = body
            status = "ok"
            break
        status = "blocked" if r.status_code in (401, 403, 429) else "error"
        if _web_should_retry(r.status_code) and attempt < _WEB_FETCH_ATTEMPTS - 1:
            await asyncio.sleep(_web_backoff(attempt, r.headers.get("Retry-After")))
            continue
        break  # non-retryable (e.g. 404) or out of attempts

    if html is None:
        return None, None, status

    # Precision extract first; fall back to recall for pages trafilatura's
    # precision heuristics strip too aggressively.
    text = (trafilatura.extract(html, include_comments=False, include_tables=True)
            or trafilatura.extract(html, include_comments=False,
                                   include_tables=True, favor_recall=True)
            or "")
    title = None
    try:
        md = trafilatura.extract_metadata(html)
        title = getattr(md, "title", None) if md else None
    except Exception:  # noqa: BLE001
        title = None
    if len(text) < _WEB_MIN_TEXT_CHARS:
        return None, (title or url), "empty"
    return text, (title or url), "ok"


async def _fetch_and_extract_web(url: str, tr: AgentTrace) -> dict:
    """Web-page analogue of _fetch_and_extract, so modes read a page like a paper
    (Lever 1: the DR loop can now cite web sources, not just DOI papers)."""
    if not P.may_fetch(P.NET_WEB):
        return {"outcome": "out_of_scope", "reason": "web egress off",
                "ref_resolved": {"url": url}}
    text, title, status = await _fetch_web_text(url)
    if not text or len(text) < _WEB_MIN_TEXT_CHARS:
        reason = {
            "blocked": "publisher blocked the request (bot protection / datacenter IP)",
            "empty": "fetched but no extractable article text",
            "error": "could not fetch the page (network or non-retryable error)",
        }.get(status, "empty or unreadable web page")
        return {"outcome": "extraction_failed", "reason": reason,
                "ref_resolved": {"url": url, "title": title}}
    ref_resolved = {"url": url, "title": title}
    tr.source(ref_resolved)
    return {"outcome": "resolved", "read_depth": "full_text", "ref_resolved": ref_resolved,
            "abstract": "", "full_text": text,
            "source": {"origin": P.ORIGIN_WEB, "url": url, "extraction_method": "web_text"}}



# ---------------------------------------------------------------------------
# mode="evidence": chunk-level retrieval with citable provenance
# ---------------------------------------------------------------------------
# Why this exists. `papers_bge` holds ONE vector per paper, built from title +
# abstract, so a measured value that lives in a methods section or a table is
# unreachable by paper-level retrieval. Measured 2026-08-27 on the iLOV
# reproducer: the model compensated by hand-fetching 11-14 documents per turn,
# ~60% of all its tool calls. This mode retrieves CHUNKS from `papers_chunks`
# instead, which is the missing depth axis (paper -> chunk -> scored evidence).
#
# It returns EVIDENCE, not prose: ranked passages, each carrying the identity
# and locator needed to cite it. Composing an answer is the caller's job.

CHUNKS_COLLECTION = os.getenv("CHUNKS_COLLECTION", "papers_chunks")
EVIDENCE_TOP_K = int(os.getenv("EVIDENCE_TOP_K", "8") or 8)
EVIDENCE_FETCH = int(os.getenv("EVIDENCE_FETCH", "40") or 40)
# At most N chunks from any one paper, so a single verbose document cannot
# occupy the whole evidence set and crowd out corroboration.
EVIDENCE_PER_PAPER = int(os.getenv("EVIDENCE_PER_PAPER", "2") or 2)

_EVIDENCE_SYSTEM = (
    "You score passages for whether they ANSWER a question. For each numbered "
    "passage return one line: `<n>: <0-10> <=15 word reason>`. 10 means the "
    "passage states the answer explicitly; 5 means it is on-topic but does not "
    "state it; 0 means irrelevant. Judge only what the passage SAYS. Never use "
    "outside knowledge. Return nothing but the numbered lines."
)


def _parse_scores(content: str, n: int) -> list[float]:
    """Scores from the judge, defaulting to -1 (unscored) on any malformed line.

    Degrades to the cosine order rather than raising: a scorer failure must
    never take out the retrieval it was only meant to reorder.

    The leading bracket is OPTIONAL because the judge intermittently echoes the
    `[n]` numbering it is shown in the passage block instead of the bare `n:`
    the system prompt asks for. Measured 2026-08-31 against the live index: 2 of
    4 identical calls came back as `[1]: 5 ...`, and because every line then
    failed to match, the whole result silently fell back to cosine order with
    score=null. That is the degrade path doing its job on a parser bug rather
    than a scorer failure, which is exactly why it went unnoticed: the result
    still looks well-formed, just unranked.
    """
    out = [-1.0] * n
    for line in (content or "").splitlines():
        m = re.match(r"\s*\[?\s*(\d+)\s*\]?\s*[:.)]\s*(\d+(?:\.\d+)?)", line)
        if not m:
            continue
        i, sc = int(m.group(1)) - 1, float(m.group(2))
        if 0 <= i < n:
            out[i] = max(0.0, min(10.0, sc))
    return out


async def _mode_evidence(question: str, refs: list, tags, tr) -> dict:
    """Retrieve and score chunks.

    Returns {evidence, n_candidates, scored, n_scored, judge}. `scored` is True
    only when the judge's reply actually parsed, so a caller can tell a ranked
    result from a cosine-ordered fallback; `judge` says which of the two failure
    modes occurred. See the block above the return for why they are separate.
    """
    from qdrant_client import http as _qh  # noqa: F401  (import guard only)
    import database as _db
    from mcp.tools.papers import _build_tag_filter
    from ..context import current_query_tags

    qc = _db.get_qdrant()
    try:
        qc.get_collection(CHUNKS_COLLECTION)
    except Exception:
        return {"evidence": [], "n_candidates": 0,
                "reason": (f"{CHUNKS_COLLECTION} is not built yet; "
                           "chunk-level evidence is unavailable")}

    vec = _db.get_paper_encoder().encode(_db.PAPER_QUERY_PREFIX + question).tolist()
    eff_tags = tags if tags is not None else current_query_tags.get()
    qfilter = _build_tag_filter(eff_tags)

    # Scope to specific papers when the caller named them.
    dois = [d for d in ((_coerce_ref(r) or {}).get("doi") for r in (refs or [])) if d]
    if dois:
        from qdrant_client import models as _m
        cond = _m.FieldCondition(key="doi", match=_m.MatchAny(any=dois))
        qfilter = (_m.Filter(must=[cond]) if qfilter is None
                   else _m.Filter(must=list(getattr(qfilter, "must", []) or []) + [cond]))

    hits = qc.query_points(collection_name=CHUNKS_COLLECTION, query=vec,
                           limit=EVIDENCE_FETCH, query_filter=qfilter).points
    tr.decide("chunk retrieval", n=len(hits), scoped=bool(dois), tagged=bool(eff_tags))
    if not hits:
        return {"evidence": [], "n_candidates": 0}

    # Cap per paper BEFORE scoring so the judge's budget is spent on breadth.
    per: dict = {}
    cands = []
    for h in hits:
        pl = h.payload or {}
        key = pl.get("paper_id") or pl.get("doi") or ""
        if per.get(key, 0) >= EVIDENCE_PER_PAPER:
            continue
        per[key] = per.get(key, 0) + 1
        cands.append((h, pl))
    cands = cands[:EVIDENCE_FETCH]

    # ONE batched judge call, not one per candidate.
    numbered = "\n\n".join(
        f"[{i+1}] {(pl.get('chunk_text') or '')[:900]}" for i, (_h, pl) in enumerate(cands))
    res = await _vllm_answer(_EVIDENCE_SYSTEM,
                             f"Question: {question}\n\nPassages:\n{numbered}",
                             max_tokens=1200, enable_thinking=False)
    call_ok = not res.get("error")
    scores = _parse_scores(res.get("content", ""), len(cands)) if call_ok else [-1.0] * len(cands)
    # Cost accounting keys on the CALL, never on the parse: an unreadable reply
    # still cost a round trip and it must show up as one.
    tr.llm(1 if call_ok else 0)

    # `scored` reports whether the result is actually JUDGE-ranked, not whether
    # the judge was reachable. Until 2026-08-31 it keyed on the call, so the
    # bracketed-numbering parser bug shipped a cosine-ordered result that
    # announced itself as scored, with every passage carrying score=null. Three
    # outcomes, three different remedies, so keep them distinguishable:
    #   ok       judged and ranked
    #   unparsed judge answered but the reply could not be read -> PARSER bug
    #   error    judge never answered                           -> vLLM problem
    n_scored = sum(1 for s in scores if s >= 0)
    judge = "ok" if n_scored else ("error" if not call_ok else "unparsed")
    if call_ok and not n_scored:
        logger.warning(
            "evidence judge: 0/%d candidates parsed from %d reply lines; "
            "falling back to cosine order. First line: %r",
            len(cands), len((res.get("content") or "").splitlines()),
            ((res.get("content") or "").splitlines() or [""])[0][:120])

    out = []
    for (h, pl), sc in zip(cands, scores):
        quote = (pl.get("chunk_text") or "").strip()
        doi, pid = pl.get("doi"), pl.get("paper_id")
        # Evidence with no locator is not evidence: drop rather than return it
        # unattributed. bibref.py exists because invented attributions shipped.
        if not quote or not (doi or pid) or pl.get("chunk_index") is None:
            continue
        out.append({
            "quote": quote,                      # verbatim; never model-generated
            "relevance": round(float(h.score), 4),
            "score": None if sc < 0 else sc,
            "ref": {"doi": doi, "title": pl.get("title"), "authors": pl.get("authors"),
                    "year": pl.get("year"), "journal": pl.get("journal")},
            "locator": {"paper_id": pid, "chunk_index": pl.get("chunk_index"),
                        "total_chunks": pl.get("total_chunks")},
            "origin": P.ORIGIN_LOCAL_KB,
            "extraction_method": "grobid_chunk",
        })
    # Judge score first when we have one, cosine as the tie-break and fallback.
    out.sort(key=lambda e: (-(e["score"] if e["score"] is not None else -1),
                            -e["relevance"]))
    return {"evidence": out[:EVIDENCE_TOP_K], "n_candidates": len(cands),
            "scored": bool(n_scored), "n_scored": n_scored, "judge": judge}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def source(refs: list, mode: str = "summary", question: Optional[str] = None,
                 focus: Optional[str] = None, schema: Optional[list] = None) -> dict:
    """Read 1..N documents and return a grounded result. See module docstring."""
    if mode not in ("summary", "qa", "findings", "extract", "compare", "evidence"):
        return {"error": f"unknown mode {mode!r}; expected "
                         "summary|qa|findings|extract|compare|evidence"}
    if mode in ("qa", "findings", "evidence") and not question:
        return {"error": f"mode={mode} requires a question"}
    # evidence searches the chunk index, so refs are OPTIONAL there (they scope
    # the search to named papers). Every other mode reads a specific document.
    if mode != "evidence" and (not isinstance(refs, list) or not refs):
        return {"error": "refs must be a non-empty list"}

    if mode == "evidence":
        tr = AgentTrace("source", mode="evidence", question=question)
        body = await _mode_evidence(question, refs if isinstance(refs, list) else [],
                                    None, tr)
        n = len(body.get("evidence") or [])
        env = {"outcome": "resolved" if n else "not_found", **body}
        env["trace"] = tr.finish(outcome=env["outcome"], n_evidence=n)
        return env

    # compare fans out over source-summary + one synthesis call (folds in
    # compare_papers, now inheriting source's resolution + provenance).
    if mode == "compare":
        return await _compare(refs, focus)

    # Single-ref modes (summary/qa/extract operate on the first ref for v1;
    # multi-ref summary/extract loop is a thin extension).
    ref = _coerce_ref(refs[0])
    tr = AgentTrace("source", mode=mode, question=question, focus=focus)

    # Resolve the ref to a DOI (with the confabulated-DOI audit) or a web URL.
    doi = _doi_from_ref(ref)
    web_url = ref.get("url") if doi is None else None
    candidates = None
    if doi is None and not web_url and ref.get("title"):
        r = await _resolve_title(ref["title"], tr)
        if "resolved_doi" in r:
            doi, candidates = r["resolved_doi"], r.get("candidates")
        else:
            env = {"outcome": "ambiguous", "candidates": r.get("ambiguous", []),
                   "ref_resolved": {"title": ref["title"]}}
            env["trace"] = tr.finish(outcome="ambiguous")
            return env
    if doi is None and not web_url:
        env = {"outcome": "unresolved", "reason": "ref carries no resolvable id",
               "ref_resolved": ref}
        env["trace"] = tr.finish(outcome="unresolved", reason="no id")
        return env

    ref_generated = bool(doi) and _ref_was_seen(doi) is False
    if ref_generated:
        tr.decide("DOI not seen in a prior tool result (possible confabulation)",
                  doi=doi)

    # Paper (DOI) or web page (URL) - both yield the same ext shape.
    ext = await (_fetch_and_extract(doi, tr) if doi else _fetch_and_extract_web(web_url, tr))
    if ext["outcome"] != "resolved":
        env = {k: v for k, v in ext.items() if k not in ("full_text",)}
        env["ref_generated"] = ref_generated
        env["trace"] = tr.finish(outcome=ext["outcome"], reason=ext.get("reason"))
        return env

    # Resolved: run the mode.
    if mode == "summary":
        body = await _mode_summary(ext, focus, tr)
    elif mode == "qa":
        body = await _mode_qa(ext, question, tr)
    elif mode == "findings":
        body = await _mode_findings(ext, question, tr)
    else:  # extract
        body = await _mode_extract(ext, question, schema, tr)

    if body.get("_llm_error"):
        env = {"outcome": "extraction_failed", "reason": body["_llm_error"],
               "ref_resolved": ext["ref_resolved"], "source": ext["source"]}
        env["trace"] = tr.finish(outcome="extraction_failed", reason=body["_llm_error"])
        return env

    # A qa/findings read with nothing found is not_found, not a resolved answer.
    outcome = "not_found" if ((mode == "qa" and body.get("abstained"))
                              or (mode == "findings" and not body.get("findings"))) else "resolved"
    env = {"outcome": outcome, "ref_resolved": ext["ref_resolved"],
           "source": ext["source"], "read_depth": ext.get("read_depth"),
           "ref_generated": ref_generated, **body}
    env["trace"] = tr.finish(outcome=outcome, candidates=candidates)
    return env


async def _compare(refs: list, focus: Optional[str]) -> dict:
    """Fan out source-summary over refs + one synthesis call (folds in
    compare_papers)."""
    import asyncio
    from mcp.tools.compare_papers import _build_comparison_prompt, HARD_MAX_PAPERS
    from mcp.tools.llm import llm_summarize

    tr = AgentTrace("source", mode="compare", focus=focus)
    dois = [d for d in (_doi_from_ref(_coerce_ref(r)) for r in refs[:HARD_MAX_PAPERS]) if d]
    if not dois:
        env = {"outcome": "unresolved", "reason": "no resolvable refs", "papers": [], "failed": []}
        env["trace"] = tr.finish(outcome="unresolved")
        return env

    exts = await asyncio.gather(*(_fetch_and_extract(d, tr) for d in dois),
                                return_exceptions=True)
    successful, failed = [], []
    for d, e in zip(dois, exts):
        if isinstance(e, Exception) or not isinstance(e, dict) or e.get("outcome") != "resolved":
            failed.append({"doi": d, "error": getattr(e, "args", [e])[0]
                           if isinstance(e, Exception) else e.get("reason", e.get("outcome"))})
            continue
        s = await _mode_summary(e, focus, tr)
        successful.append({"doi": d, "title": e["ref_resolved"].get("title"),
                           "authors": e["ref_resolved"].get("authors", []),
                           **s})
    if not successful:
        env = {"outcome": "not_found", "papers": [], "failed": failed,
               "comparison": None}
        env["trace"] = tr.finish(outcome="not_found")
        return env
    if len(successful) == 1:
        env = {"outcome": "resolved", "papers": successful, "failed": failed,
               "comparison": "Only one paper resolved; nothing to compare."}
        env["trace"] = tr.finish(outcome="resolved", n_compared=1)
        return env
    system, user = _build_comparison_prompt(successful, focus)
    res = await llm_summarize(text=user, instruction=system, max_tokens=3000)
    tr.llm(1)
    comparison = res.get("summary") if isinstance(res, dict) else None
    env = {"outcome": "resolved", "papers": successful, "failed": failed,
           "comparison": comparison}
    env["trace"] = tr.finish(outcome="resolved", n_compared=len(successful))
    return env
