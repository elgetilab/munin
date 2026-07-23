"""
MCP tool: search (the find-evidence agent).

`search(query, filters?, depth?)` finds and ranks the evidence relevant to a
topic, consolidating paper_search (local corpus), semantic_scholar_search
(external OA), and web_search behind one call. It supersedes those three for the
"find me sources" intent (they stay callable during transition).

Design contract (AGENT-IMPLEMENTATION-PLAN.md, D18/D19 + design-v2 §6):
  - source_type per hit (corpus_paper | oa_paper | web) is LOAD-BEARING: trust
    isn't uniform, so a single similarity score can't rank across tiers. The
    ranker applies tier-aware quotas (prefer corpus, cap web) rather than one
    global score.
  - dedup on the ALIAS SET {doi, arxiv, normalized-title}, not a single field -
    the arXiv preprint and the published version are one paper with two DOIs.
  - coverage_note (D18): when a sub-corpus scope is active, run the query both
    scoped and unscoped and report "X in scope, Y consortium-wide" so a scoping
    artifact is never misread as a corpus gap.
  - thin_evidence flag rather than padding weak hits.
  - egress-aware: which tiers fan out is driven by the egress control, not the
    model. egress=off -> local corpus only; oa_only -> +scholarly; full -> +web.
  - emits a trace.
"""

from __future__ import annotations

import re
from typing import Any, Optional

import provenance as P
from agent_trace import AgentTrace

# source_type tiers, in trust order (drives the quota-based ranking).
TIER_CORPUS = "corpus_paper"
TIER_OA = "oa_paper"
TIER_WEB = "web"
_TIER_RANK = {TIER_CORPUS: 0, TIER_OA: 1, TIER_WEB: 2}

WEB_QUOTA = 3            # web caps at N regardless of volume (it wins on volume)
OA_QUOTA = 4             # OA slots reserved within top_k (see _dedup_and_rank)
THIN_EVIDENCE_MIN = 3    # fewer than N scholarly hits => thin_evidence


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _alias_key(hit: dict) -> str:
    """Dedup key from the alias set: doi, else arxiv, else title, else url."""
    doi = (hit.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    if hit.get("arxiv"):
        return f"arxiv:{str(hit['arxiv']).strip().lower()}"
    title = _norm_title(hit.get("title", ""))
    if title:
        return f"title:{title}"
    return f"url:{(hit.get('url') or '').strip().lower()}"


def _ref_of(hit: dict) -> dict:
    if hit.get("doi"):
        return {"doi": hit["doi"], "title": hit.get("title")}
    if hit.get("url"):
        return {"url": hit["url"], "title": hit.get("title")}
    return {"title": hit.get("title")}


def _norm_corpus(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({"source_type": TIER_CORPUS, "title": r.get("title"),
                    "doi": r.get("doi"), "snippet": (r.get("abstract") or "")[:300],
                    "score": r.get("score"), "year": r.get("year"),
                    "authors": (r.get("authors") or [])[:6]})
    return out


def _norm_oa(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        snippet = (r.get("tldr") or r.get("abstract") or "")[:300]
        out.append({"source_type": TIER_OA, "title": r.get("title"),
                    "doi": r.get("doi"), "snippet": snippet,
                    "score": r.get("citation_count"), "year": r.get("year"),
                    "authors": (r.get("authors") or [])[:6],
                    "open_access_pdf": r.get("open_access_pdf"),
                    "local_pdf_available": r.get("local_pdf_available", False)})
    return out


def _norm_web(rows: list[dict]) -> list[dict]:
    return [{"source_type": TIER_WEB, "title": r.get("title"), "url": r.get("url"),
             "snippet": (r.get("snippet") or "")[:300], "score": r.get("matched_by")}
            for r in rows]


def _dedup_and_rank(hits: list[dict], top_k: int) -> list[dict]:
    """Merge on the alias set (keeping the highest-trust tier), then rank with
    tier-aware quotas: corpus and OA by their own score, web capped at WEB_QUOTA."""
    merged: dict[str, dict] = {}
    for h in hits:
        k = _alias_key(h)
        cur = merged.get(k)
        if cur is None:
            merged[k] = h
        else:
            # Keep the higher-trust tier; union OA's open-access link onto it.
            keep, drop = (cur, h) if _TIER_RANK[cur["source_type"]] <= _TIER_RANK[h["source_type"]] else (h, cur)
            if not keep.get("open_access_pdf") and drop.get("open_access_pdf"):
                keep["open_access_pdf"] = drop["open_access_pdf"]
            merged[k] = keep

    items = list(merged.values())
    corpus = sorted([h for h in items if h["source_type"] == TIER_CORPUS],
                    key=lambda r: -(r.get("score") or 0))
    oa = sorted([h for h in items if h["source_type"] == TIER_OA],
                key=lambda r: -(r.get("score") or 0))[:OA_QUOTA]
    web = sorted([h for h in items if h["source_type"] == TIER_WEB],
                 key=lambda r: -(r.get("score") or 0))[:WEB_QUOTA]
    # Reserve the external tiers within top_k: a plentiful corpus must NOT crowd
    # OA/web out of the read pool (that silently caps breadth to the corpus). We
    # keep corpus first for trust, but only up to (top_k - reserved) so the
    # reserved OA + web slots always survive into screening.
    reserved = oa + web
    corpus_budget = max(0, top_k - len(reserved))
    return (corpus[:corpus_budget] + reserved)[:top_k]


async def search(query: str, filters: Optional[dict] = None,
                 depth: str = "normal", top_k: int = 10) -> dict:
    """Find and rank evidence for a topic across corpus / OA / web tiers.

    depth: 'normal' (corpus + scholarly) | 'deep' (+ web). Web also requires
    egress to permit it. See module docstring for the full contract.
    """
    from mcp.tools.papers import paper_search, semantic_scholar_search
    from mcp.tools.web import web_search
    import asyncio

    if not query or not str(query).strip():
        return {"error": "search requires a non-empty query"}
    filters = filters or {}
    year = filters.get("year", "")
    tags = filters.get("tags")  # None -> inherits current_query_tags ContextVar

    tr = AgentTrace("search", query=query, depth=depth)

    # Which tiers fan out is an egress decision, not the model's.
    do_oa = P.may_fetch(P.NET_SCHOLARLY_API)
    do_web = depth == "deep" and P.may_fetch(P.NET_WEB)
    tr.decide("tier fan-out", corpus=True, oa=do_oa, web=do_web,
              egress=P.get_egress())

    tasks = [paper_search(query=query, top_k=top_k, tags=tags)]
    if do_oa:
        tasks.append(semantic_scholar_search(query=query, top_k=top_k, year=year))
    if do_web:
        tasks.append(web_search(query=query, top_k=top_k))
    results = await asyncio.gather(*tasks, return_exceptions=True)

    corpus_res = results[0] if not isinstance(results[0], Exception) else {}
    idx = 1
    oa_res = web_res = {}
    if do_oa:
        oa_res = results[idx] if not isinstance(results[idx], Exception) else {}
        idx += 1
    if do_web:
        web_res = results[idx] if not isinstance(results[idx], Exception) else {}

    corpus_hits = _norm_corpus((corpus_res or {}).get("results", []))
    oa_hits = _norm_oa((oa_res or {}).get("results", []))
    web_hits = _norm_web((web_res or {}).get("results", []))
    tr.llm(0)
    ranked = _dedup_and_rank(corpus_hits + oa_hits + web_hits, top_k)

    # Coverage note (D18): if a sub-corpus scope is active, compare scoped vs
    # unscoped corpus hits so a scoping artifact is never read as a corpus gap.
    applied_tags = (corpus_res or {}).get("applied_tags")
    coverage_note = None
    if applied_tags:
        unscoped = await paper_search(query=query, top_k=top_k, tags=[])
        scoped_n = len(corpus_hits)
        unscoped_n = len((unscoped or {}).get("results", []))
        coverage_note = (f"{scoped_n} in the active scope; "
                         f"{unscoped_n} consortium-wide.")
        tr.decide("scoped coverage", scoped=scoped_n, unscoped=unscoped_n)
        if scoped_n == 0 and unscoped_n > 0:
            coverage_note += (" The answer may be outside your current scope - "
                              "widen it to search the whole consortium corpus.")

    scholarly = [h for h in ranked if h["source_type"] in (TIER_CORPUS, TIER_OA)]
    thin_evidence = len(scholarly) < THIN_EVIDENCE_MIN
    if thin_evidence:
        tr.decide("thin evidence", n_scholarly=len(scholarly))

    env = {"ranked": ranked, "coverage_note": coverage_note,
           "thin_evidence": thin_evidence,
           "counts": {TIER_CORPUS: len(corpus_hits), TIER_OA: len(oa_hits),
                      TIER_WEB: len(web_hits)}}
    env["trace"] = tr.finish(outcome="resolved", n_ranked=len(ranked),
                             thin_evidence=thin_evidence)
    return env
