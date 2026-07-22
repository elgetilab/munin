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
from database import VLLM_MODEL_NAME, VLLM_URL

logger = logging.getLogger(__name__)

# Unpaywall is the canonical open-access PDF resolver (far broader coverage than
# Semantic Scholar's openAccessPdf field, and it returns direct PDF links). It is
# the OA full-text lever: without it, OA papers not in the local corpus fall back
# to abstract-only, which qa mode then abstains on. Requires a contact email per
# their API policy.
UNPAYWALL_EMAIL = os.getenv("UNPAYWALL_EMAIL", "munin@muninai.org")


_PDF_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


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
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True,
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
        body["chat_template_kwargs"] = {"enable_thinking": False}
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{VLLM_URL}/v1/chat/completions", json=body)
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
    `content` is kept as `answer` so downstream MCQ letter-parsing still works."""
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


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def source(refs: list, mode: str = "summary", question: Optional[str] = None,
                 focus: Optional[str] = None, schema: Optional[list] = None) -> dict:
    """Read 1..N documents and return a grounded result. See module docstring."""
    if mode not in ("summary", "qa", "extract", "compare"):
        return {"error": f"unknown mode {mode!r}; expected summary|qa|extract|compare"}
    if not isinstance(refs, list) or not refs:
        return {"error": "refs must be a non-empty list"}
    if mode == "qa" and not question:
        return {"error": "mode=qa requires a question"}

    # compare fans out over source-summary + one synthesis call (folds in
    # compare_papers, now inheriting source's resolution + provenance).
    if mode == "compare":
        return await _compare(refs, focus)

    # Single-ref modes (summary/qa/extract operate on the first ref for v1;
    # multi-ref summary/extract loop is a thin extension).
    ref = _coerce_ref(refs[0])
    tr = AgentTrace("source", mode=mode, question=question, focus=focus)

    # Resolve the ref to a DOI (with the confabulated-DOI audit).
    doi = _doi_from_ref(ref)
    candidates = None
    if doi is None and ref.get("title"):
        r = await _resolve_title(ref["title"], tr)
        if "resolved_doi" in r:
            doi, candidates = r["resolved_doi"], r.get("candidates")
        else:
            env = {"outcome": "ambiguous", "candidates": r.get("ambiguous", []),
                   "ref_resolved": {"title": ref["title"]}}
            env["trace"] = tr.finish(outcome="ambiguous")
            return env
    if doi is None:
        env = {"outcome": "unresolved", "reason": "ref carries no resolvable id",
               "ref_resolved": ref}
        env["trace"] = tr.finish(outcome="unresolved", reason="no id")
        return env

    ref_generated = _ref_was_seen(doi) is False
    if ref_generated:
        tr.decide("DOI not seen in a prior tool result (possible confabulation)",
                  doi=doi)

    ext = await _fetch_and_extract(doi, tr)
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
    else:  # extract
        body = await _mode_extract(ext, question, schema, tr)

    if body.get("_llm_error"):
        env = {"outcome": "extraction_failed", "reason": body["_llm_error"],
               "ref_resolved": ext["ref_resolved"], "source": ext["source"]}
        env["trace"] = tr.finish(outcome="extraction_failed", reason=body["_llm_error"])
        return env

    # qa that comes back INSUFFICIENT is a not_found, not a resolved answer.
    outcome = "not_found" if (mode == "qa" and body.get("abstained")) else "resolved"
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
