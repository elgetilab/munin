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

import logging
import os
import re
from typing import Any, Optional

import provenance as P
from agent_trace import AgentTrace

logger = logging.getLogger(__name__)

# source_type tiers, in trust order (drives the quota-based ranking).
TIER_CORPUS = "corpus_paper"
TIER_OA = "oa_paper"
TIER_WEB = "web"
_TIER_RANK = {TIER_CORPUS: 0, TIER_OA: 1, TIER_WEB: 2}

WEB_QUOTA = 3            # web caps at N regardless of volume (it wins on volume)
OA_QUOTA = 4             # OA slots reserved within top_k (see _dedup_and_rank)
THIN_EVIDENCE_MIN = 3    # fewer than N scholarly hits => thin_evidence

# Relevance floor for the RESERVED external slots. Calibrated 2026-07-24 over 30
# real retrieved candidates on the capstone sub-questions (see
# todo_v2/OA-RELEVANCE-PLAN.md): the clearly off-topic tail sits below 0.62
# while every genuinely relevant external hit scored above it. BGE cosine has a
# high compressed baseline (observed band 0.57-0.77), so this is NOT a
# general-purpose "similarity is high" threshold - it is tied to this encoder.
# Env-overridable because an absolute floor may need adjusting per domain.
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


OA_RELEVANCE_FLOOR = _env_float("OA_RELEVANCE_FLOOR", 0.62)
WEB_RELEVANCE_FLOOR = _env_float("WEB_RELEVANCE_FLOOR", 0.62)


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


def _doc_text(hit: dict) -> str:
    """Candidate text to embed. Must match how corpus documents were embedded
    (``title\\n\\nabstract``) or the cosines are not comparable."""
    return f"{hit.get('title') or ''}\n\n{hit.get('snippet') or ''}".strip()


def _max_cosine(qmat, dmat) -> list[float]:
    """Pure math: max cosine of each doc row against any query row.

    `qmat` (n_queries x dim) and `dmat` (n_docs x dim) are row-wise L2
    normalised here, so this is correct whether or not the encoder already
    normalises. Returns one score per doc row.
    """
    import numpy as np

    q = np.asarray(qmat, dtype="float32")
    d = np.asarray(dmat, dtype="float32")
    if q.ndim == 1:
        q = q.reshape(1, -1)
    if d.ndim == 1:
        d = d.reshape(1, -1)
    qn = np.linalg.norm(q, axis=1, keepdims=True)
    dn = np.linalg.norm(d, axis=1, keepdims=True)
    q = q / np.where(qn == 0, 1, qn)
    d = d / np.where(dn == 0, 1, dn)
    return (d @ q.T).max(axis=1).tolist()


async def _attach_relevance(variants: list[str], hits: list[dict]) -> bool:
    """Attach a `relevance` cosine to every OA/web hit, on the SAME axis as the
    corpus tier (same encoder, same query prefix, same document format).

    Corpus hits already carry a Qdrant cosine in `score`, so they are simply
    copied across. Returns True if scoring succeeded; on any failure the hits
    are left without `relevance` and the caller falls back to the legacy sort -
    search must never break because the encoder is unavailable.
    """
    external = [h for h in hits if h["source_type"] != TIER_CORPUS]
    for h in hits:
        if h["source_type"] == TIER_CORPUS:
            h["relevance"] = h.get("score")
    if not external or not variants:
        return True

    try:
        import asyncio

        from database import PAPER_QUERY_PREFIX, get_paper_encoder

        encoder = get_paper_encoder()
        if encoder is None:
            return False

        docs = [_doc_text(h) for h in external]
        queries = [PAPER_QUERY_PREFIX + v for v in variants]

        # Encoding is CPU-bound and blocking (no GPU in the retrieval
        # container), so run it in the executor like paper_search does. ONE
        # batched encode call for all queries + all candidates.
        loop = asyncio.get_running_loop()
        qmat, dmat = await loop.run_in_executor(
            None, lambda: (encoder.encode(queries), encoder.encode(docs))
        )
        for hit, rel in zip(external, _max_cosine(qmat, dmat)):
            hit["relevance"] = float(rel)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("relevance scoring unavailable, falling back: %s", exc)
        return False


def _tier_sort_key(hit: dict):
    """Rank within a tier by relevance, with the tier's legacy score demoted to
    a tie-break. Hits with no relevance (scoring unavailable) fall back to the
    legacy score alone, which reproduces the pre-2026-07 ordering."""
    rel = hit.get("relevance")
    legacy = hit.get("score") or 0
    if rel is None:
        return (0.0, -legacy)
    return (-float(rel), -legacy)


def _dedup_and_rank(hits: list[dict], top_k: int,
                    gate_stats: Optional[dict] = None) -> list[dict]:
    """Merge on the alias set (keeping the highest-trust tier), then rank with
    tier-aware quotas.

    Ranking (2026-07-24): every tier is ordered by `relevance` (a cosine on one
    shared axis, see `_attach_relevance`), with the tier's legacy score
    (corpus=qdrant, OA=citations, web=matched_by) demoted to a tie-break. The
    TIER STRUCTURE is deliberately kept: corpus stays first for trust and the
    external tiers keep reserved slots. We do not merge onto one global axis,
    because trust is not uniform across tiers (D18/D19).

    Reserved external slots are additionally gated by a relevance floor: an
    off-topic OA tier used to fill 4 read slots on citation count alone, and
    every one of those reads then honestly abstained. Below the floor the slot
    goes back to corpus instead. `gate_stats`, if given, receives the drop
    counts so a mis-set floor is visible rather than silently starving a tier.
    """
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

    def _gate(rows: list[dict], floor: float, tier: str) -> list[dict]:
        """Drop reserved-slot candidates below the relevance floor. Hits with no
        relevance (scoring unavailable) pass through, so a missing encoder
        degrades to the old behaviour rather than emptying the tier."""
        kept = [h for h in rows
                if h.get("relevance") is None or float(h["relevance"]) >= floor]
        if gate_stats is not None and len(kept) != len(rows):
            gate_stats[tier] = len(rows) - len(kept)
        return kept

    corpus = sorted([h for h in items if h["source_type"] == TIER_CORPUS],
                    key=_tier_sort_key)
    oa = _gate(sorted([h for h in items if h["source_type"] == TIER_OA],
                      key=_tier_sort_key), OA_RELEVANCE_FLOOR, TIER_OA)[:OA_QUOTA]
    web = _gate(sorted([h for h in items if h["source_type"] == TIER_WEB],
                       key=_tier_sort_key), WEB_RELEVANCE_FLOOR, TIER_WEB)[:WEB_QUOTA]
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
    from mcp.tools.query_expansion import expand_queries
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

    # Expand ONCE here and hand the same variant list to every tier. Passing
    # `query=` instead would make each tool expand independently: three vLLM
    # calls per search, and three different variant lists, so the tiers would be
    # answering different questions and their scores would not be comparable.
    # `expand_queries` always returns [base, ...variants], so the user's exact
    # wording is still executed verbatim against each tier.
    variants = await expand_queries(query, n=5)
    if not variants:
        variants = [query]
    tr.decide("query expansion", n_variants=len(variants))

    tasks = [paper_search(queries=variants, top_k=top_k, tags=tags)]
    if do_oa:
        tasks.append(semantic_scholar_search(queries=variants, top_k=top_k, year=year))
    if do_web:
        tasks.append(web_search(queries=variants, top_k=top_k))
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

    # Put every tier on ONE relevance axis before ranking. Without this, OA is
    # ordered by citation count and web by query-overlap count, so a famous
    # off-topic paper outranks an on-topic one and then burns a read slot.
    all_hits = corpus_hits + oa_hits + web_hits
    scored = await _attach_relevance(variants, all_hits)
    tr.decide("relevance scoring", ok=scored, n_scored=len(all_hits))

    gate_stats: dict[str, int] = {}
    ranked = _dedup_and_rank(all_hits, top_k, gate_stats=gate_stats)
    if gate_stats:
        tr.decide("relevance gate dropped reserved candidates", **gate_stats)
        logger.info("search relevance gate dropped %s (floors oa=%.2f web=%.2f)",
                    gate_stats, OA_RELEVANCE_FLOOR, WEB_RELEVANCE_FLOOR)

    # Coverage note (D18): if a sub-corpus scope is active, compare scoped vs
    # unscoped corpus hits so a scoping artifact is never read as a corpus gap.
    applied_tags = (corpus_res or {}).get("applied_tags")
    coverage_note = None
    if applied_tags:
        # Same variants as the scoped call, or the comparison would be against a
        # different expansion (and would burn a fourth expansion call).
        unscoped = await paper_search(queries=variants, top_k=top_k, tags=[])
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
