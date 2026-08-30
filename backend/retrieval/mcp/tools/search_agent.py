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

# Escalation thresholds for the staged fan-out. `..._TAGGED` is deliberately
# lower: when the user pinned a corpus with a slash command, one strong in-scope
# hit is enough to answer from it, and a miss is a finding to report rather than
# a reason to silently widen to the open web.
CORPUS_SUFFICIENT_MIN = int(os.getenv("CORPUS_SUFFICIENT_MIN", "3") or 3)
CORPUS_SUFFICIENT_MIN_TAGGED = int(os.getenv("CORPUS_SUFFICIENT_MIN_TAGGED", "1") or 1)
# Max documents `search(read=N)` will open. `source`'s single-ref modes read ONE
# ref per call, so this is N vLLM calls; reads stop early on the first
# non-abstained answer, so the common case costs one.
READ_MAX = int(os.getenv("SEARCH_READ_MAX", "3") or 3)

# Relevance floor for the RESERVED external slots. Calibrated 2026-07-24 over 30
# real retrieved candidates on the capstone sub-questions (see
# docs/paper-track/OA-RELEVANCE-PLAN.md): the clearly off-topic tail sits below 0.62
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
        # `paper_search` emits the abstract excerpt as `excerpt`, NOT `abstract`
        # (see papers.py::_qdrant_search_one, "Surface a short abstract excerpt").
        # Reading only `abstract` here meant EVERY corpus hit in `search` came
        # back with an empty snippet, so the model had a bare title and could
        # neither judge relevance nor extract a value without a follow-up
        # `source` read. That manufactured tool calls on every research turn.
        # Found 2026-08-27 on the iLOV reproducer. `abstract` stays as a
        # fallback so any other caller shape still works.
        snippet = (r.get("excerpt") or r.get("abstract") or "")[:300]
        out.append({"source_type": TIER_CORPUS, "title": r.get("title"),
                    "doi": r.get("doi"), "snippet": snippet,
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
    """Web hits, carrying bibliographic metadata when web_search resolved it.

    `authors` is ALWAYS present, as a list or as None, and `metadata_available`
    says which. A missing key reads as "not applicable" and invites the model
    to supply the author from memory; an explicit null says "unknown, do not
    guess". That is the failure this shape exists to prevent (chat 61443530:
    three author names invented for web hits that carried a title and nothing
    else).
    """
    out = []
    for r in rows:
        authors = r.get("authors") or None
        entry = {"source_type": TIER_WEB, "title": r.get("title"),
                 "url": r.get("url"), "snippet": (r.get("snippet") or "")[:300],
                 "score": r.get("matched_by"),
                 "authors": authors,
                 "year": r.get("year"),
                 "metadata_available": bool(authors)}
        if r.get("doi"):
            entry["doi"] = r["doi"]
        if r.get("bibliographic"):
            entry["bibliographic"] = r["bibliographic"]
        out.append(entry)
    return out


# Semantic Scholar's /paper/search is a KEYWORD endpoint, not a semantic one.
# Measured 2026-07-24: a long natural-language sub-question returns ZERO results
# from S2, while the same question reduced to its content words returns hits. The
# corpus (BGE dense) and Brave both handle natural language fine, so only the OA
# tier gets this treatment.
_KEYWORD_STOP = frozenset("""
a an the of for to in on by and or with without into from as at is are was were
be been being do does did how what which why when where that this these those it
its their there can could may might must should would will shall we you they he
she not no than then so such very more most other another each any all some
between among during through including within under over upon eg ie
""".split())
# Deliberately generous: truncating drops the trailing entity ("... kinase
# inhibitors"), and an entity-less keyword query is exactly what returns the
# broad off-topic papers this whole change exists to stop. Measured: dropping to
# 10 words returned 18 BROAD hits with the entity gone; keeping all 16 returned 4
# SPECIFIC hits with it intact. Four on-topic beats eighteen off-topic when the
# tier only has OA_QUOTA slots.
_KEYWORD_MAX_WORDS = 16


def _keywordize(q: str, max_words: int = _KEYWORD_MAX_WORDS) -> str:
    """Reduce a natural-language query to content words for a keyword backend.

    Preserves word order and de-duplicates. Returns "" when nothing meaningful
    survives, so the caller can fall back to the original string.
    """
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", q or "")
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        k = w.lower()
        if k in _KEYWORD_STOP or len(w) <= 2 or k in seen:
            continue
        seen.add(k)
        out.append(w)
        if len(out) >= max_words:
            break
    return " ".join(out)


def _oa_queries(variants: list[str]) -> list[str]:
    """Keyword-shaped queries for the OA tier, de-duplicated, order preserved.
    Falls back to the original variant when keywordization strips too much."""
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        kw = _keywordize(v)
        q = kw if len(kw.split()) >= 2 else v
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out


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


# A query that wants a FACT out of the literature, as opposed to a set of
# documents. Deliberately crude and cheap: the cost of a false positive is one
# extra batched LLM call, the cost of a false negative is the model hand-rolling
# a dozen reads, which is what the iLOV reproducer measured.
_ANSWER_SHAPED = re.compile(
    r"\b(what|which|how (?:much|many|long|fast)|when|value|values|"
    r"coefficient|constant|affinity|kd\b|ic50|ec50|rate|yield|"
    r"temperature|wavelength|concentration|lifetime|efficiency)\b",
    re.IGNORECASE)


def _wants_answer(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    return bool(q.endswith("?") or _ANSWER_SHAPED.search(q))

async def search(query: str, filters: Optional[dict] = None,
                 depth: str = "normal", top_k: int = 10,
                 read: int = 0, extra_queries: Any = None) -> dict:
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
    # `queries` is NOT part of this tool's schema, but models send it anyway
    # (paper_search and web_search both take one, so it is a reasonable guess).
    # The dispatcher reads arguments by name, so it used to vanish silently: the
    # model believed it had issued a 4-query fan-out, one query ran, and it got
    # no signal either way. Observed on the iLOV reproducer, where the model
    # then spent 25 further calls doing by hand what it thought it had asked
    # for. `search` expands internally, so the right answer is to say so rather
    # than to accept a second, conflicting expansion.
    if extra_queries:
        return {"error": (
            "search does not take a 'queries' argument: it expands 'query' "
            "internally and runs the fan-out across all tiers itself. Pass the "
            "single best natural-language query as 'query'. (If you want to "
            "control the exact variants, use paper_search or web_search, which "
            "do take 'queries'.)"
        )}
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

    # Each tier gets the query SHAPE its backend can actually match. Corpus is a
    # dense BGE index and Brave handles prose, so both take the natural-language
    # variants. S2 is keyword-based and returns nothing for a full question, so
    # it gets the content words. Relevance scoring below still uses the
    # natural-language variants, independent of what each tier was sent.
    oa_variants = _oa_queries(variants)

    # ESCALATING LADDER (2026-08-27). Was: one parallel gather over every
    # permitted tier, every time, regardless of whether the corpus already
    # answered. Now stage 1 is the corpus alone and the external tiers are only
    # paid for if it comes up short. Rationale beyond latency: an explicit
    # `/group` tag is a statement about WHERE the answer should be, so quietly
    # substituting open-web results when the corpus misses hides the finding
    # that the data is not in the corpus (operator decision, 2026-08-27).
    corpus_res_raw = await paper_search(queries=variants, top_k=top_k, tags=tags)
    corpus_res = corpus_res_raw if not isinstance(corpus_res_raw, Exception) else {}
    corpus_hits = _norm_corpus((corpus_res or {}).get("results", []))

    # Score the corpus alone so sufficiency is judged on the same relevance axis
    # the final ranking uses, not on a raw count.
    await _attach_relevance(variants, corpus_hits)
    corpus_strong = [h for h in corpus_hits
                     if isinstance(h.get("relevance"), (int, float))
                     and h["relevance"] >= OA_RELEVANCE_FLOOR]
    explicit_tags = bool(tags)          # caller passed them, not inherited
    need = (CORPUS_SUFFICIENT_MIN_TAGGED if explicit_tags
            else CORPUS_SUFFICIENT_MIN)
    corpus_sufficient = len(corpus_strong) >= need
    tr.decide("stage 1 corpus", n=len(corpus_hits), n_strong=len(corpus_strong),
              need=need, explicit_tags=explicit_tags,
              sufficient=corpus_sufficient)

    oa_res = web_res = {}
    escalated = False
    if not corpus_sufficient:
        # Stage 2: widen. Web joins at depth="deep", OR when the corpus AND OA
        # both came up short and egress permits it: "don't go deep from the
        # get-go, escalate if you can't find it".
        escalated = True
        tasks = []
        if do_oa:
            tr.decide("oa keyword queries", n=len(oa_variants), sample=oa_variants[:1])
            tasks.append(semantic_scholar_search(queries=oa_variants,
                                                 top_k=top_k, year=year))
        if do_web:
            tasks.append(web_search(queries=variants, top_k=top_k))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            idx = 0
            if do_oa:
                oa_res = results[idx] if not isinstance(results[idx], Exception) else {}
                idx += 1
            if do_web:
                web_res = results[idx] if not isinstance(results[idx], Exception) else {}

    oa_hits = _norm_oa((oa_res or {}).get("results", []))
    web_hits = _norm_web((web_res or {}).get("results", []))

    # Stage 2b: the corpus AND OA both missed and the web was not permitted by
    # `depth`. Escalate to it anyway rather than returning nothing, unless the
    # caller pinned an explicit corpus (where the miss is the answer).
    if (not corpus_strong and not oa_hits and not web_hits
            and not explicit_tags and P.may_fetch(P.NET_WEB)):
        tr.decide("stage 2b escalate to web", reason="corpus and OA both empty")
        w = await web_search(queries=variants, top_k=top_k)
        if not isinstance(w, Exception):
            web_res = w
            web_hits = _norm_web((web_res or {}).get("results", []))
            escalated = True
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
    # thin_evidence used to be a bare COUNT (`len(scholarly) < 3`), which made it
    # assert "evidence is fine" whenever enough rows came back, regardless of
    # whether any of them were on topic. On the iLOV reproducer it reported
    # False while two of the ten hits were ruthenium solar-cell papers matched
    # on the phrase "molar extinction coefficient" and none carried the answer.
    # The model reasonably took that as "good hits found" and fanned out by hand
    # instead of narrowing. Count the hits that clear the same relevance floor
    # the reserved external slots use, and fall back to the count when the
    # encoder was unavailable and no relevance was attached.
    strong = [h for h in scholarly
              if isinstance(h.get("relevance"), (int, float))
              and h["relevance"] >= OA_RELEVANCE_FLOOR]
    scored = any(isinstance(h.get("relevance"), (int, float)) for h in scholarly)
    n_effective = len(strong) if scored else len(scholarly)
    thin_evidence = n_effective < THIN_EVIDENCE_MIN
    if thin_evidence:
        tr.decide("thin evidence", n_scholarly=len(scholarly),
                  n_above_floor=len(strong), floor=OA_RELEVANCE_FLOOR)

    # ---- stage 3: READ ----------------------------------------------------
    # `search` used to stop at snippets, which is why the model hand-rolled
    # source/web_fetch loops: ~60% of the calls in every reproducer run were
    # reads. A snippet cannot carry a measured value (it lives in the body), so
    # for a specific number the read is not optional, it is the answer.
    #
    # SEQUENTIAL with early stop, not a parallel fan-out. `source(mode="qa")`
    # reports `abstained` when the document does not answer, which is exactly
    # the escalation signal: read the best candidate, and only open the next one
    # if that abstained. The common case therefore costs ONE extra vLLM call,
    # and reasoning tokens are cheap relative to another round of tool calls.
    # ---- stage 3a: chunk-level EVIDENCE (the depth axis) -------------------
    # Ranked papers answer "which documents are about this". A measured value
    # lives inside one of them, so for an answer-shaped query we pull the
    # PASSAGES via source(mode="evidence") over the chunk index. This is what
    # the model was hand-rolling with 11-14 web_fetch/source calls per turn.
    # One extra LLM call (the batched passage judge), not one per document.
    evidence: list[dict] = []
    if _wants_answer(query):
        try:
            from mcp.tools.source import source as _src
            ev = await _src(refs=[], mode="evidence", question=query)
            evidence = (ev or {}).get("evidence") or []
            tr.decide("chunk evidence", n=len(evidence),
                      scored=(ev or {}).get("scored"))
        except Exception as e:                       # never break search
            logger.info("evidence stage failed: %s", e)

    answers: list[dict] = []
    n_read = max(0, min(int(read or 0), READ_MAX))
    # Whole-paper reads are the fallback, not the default: they cost ~40s each
    # and re-parse the PDF. Skip them when chunk evidence already answered.
    if n_read and not any(e.get("score") is not None and e["score"] >= 7
                          for e in evidence):
        from mcp.tools.source import source as _source
        # Corpus first: those are local full-text PDFs. Then OA hits that
        # actually have something fetchable.
        candidates = [h for h in ranked if h["source_type"] == TIER_CORPUS]
        candidates += [h for h in ranked
                       if h["source_type"] == TIER_OA and h.get("open_access_pdf")]
        candidates += [h for h in ranked if h["source_type"] == TIER_WEB and h.get("url")]
        for h in candidates[:n_read]:
            ref = {k: v for k, v in (("doi", h.get("doi")), ("url", h.get("url")),
                                     ("title", h.get("title"))) if v}
            if not ref:
                continue
            try:
                res = await _source(refs=[ref], mode="qa", question=query)
            except Exception as e:                      # a read must never
                logger.info("search read failed for %s: %s", ref, e)  # kill the search
                continue
            if not isinstance(res, dict):
                continue
            # GROUNDEDNESS, not merely "did it answer". `_QA_SYSTEM` tells the
            # reader it "may also draw on your own knowledge", so `abstained`
            # is False even when the model answered from memory about a paper
            # that never mentions the subject (observed: a UV-microscopy paper
            # returning an iLOV extinction coefficient with abstained=False).
            # Stopping there would attribute a parametric number to a document
            # that does not contain it. `quote` is the verbatim supporting
            # sentence, or None when there is none, so it is the real signal.
            abstained = bool(res.get("abstained"))
            quote = res.get("quote")
            grounded = bool(quote) and not abstained
            answers.append({
                "ref": ref,
                "source_type": h.get("source_type"),
                "answer": res.get("answer"),
                "quote": quote,
                "abstained": abstained,
                "grounded": grounded,
            })
            tr.decide("read", ref=ref.get("doi") or ref.get("url"),
                      abstained=abstained, grounded=grounded)
            if grounded:
                break      # the document really answered it; stop paying

    env = {"ranked": ranked, "coverage_note": coverage_note,
           "thin_evidence": thin_evidence,
           **({"answers": answers} if answers else {}),
           **({"evidence": evidence} if evidence else {}),
           "escalated": escalated,
           "counts": {TIER_CORPUS: len(corpus_hits), TIER_OA: len(oa_hits),
                      TIER_WEB: len(web_hits)}}
    env["trace"] = tr.finish(outcome="resolved", n_ranked=len(ranked),
                             thin_evidence=thin_evidence)
    return env
