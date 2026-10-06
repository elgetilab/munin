"""
MCP tool: tool_search (P1 #7 from the 2026-05 internal harness audit).

The vLLM `tools` schema only carries CORE_TOOLS by default — keeping
prefill well clear of the hang cliff. tool_search lets the model
discover the rest of its persona's toolkit at runtime: it keyword-
matches a query against every tool in the calling persona's allowlist,
returns the matches' full schemas, and unlocks them into the
per-request schema (`current_unlocked_tools`) so subsequent turns can
call them directly.

Authorisation is unchanged: tool_search only ever surfaces tools that
are already in the persona's allowlist, so a discovered tool can't be
rejected at dispatch. The allowlist stays the authz boundary; this tool
only governs what's *visible in the schema*.
"""

from __future__ import annotations

import logging
import math
import re

from ..context import current_unlocked_tools
from ..schemas import MCP_TOOLS, CORE_TOOLS

logger = logging.getLogger(__name__)

# Cap on schemas returned in one call. A query that matches more than
# this gets the top-ranked slice plus a "refine your query" note. Raised
# 8 -> 10 (A2) so a genuinely-relevant tool that ranks mid-pack among
# matches is not crowded out by the cap; prefill has ample headroom (the
# resident schema is far under the 64K context limit, see A2-PLAN findings).
_MAX_RESULTS = 10

# Term matching is IDF-WEIGHTED (A2). The original scorer summed +3/+1 for
# any substring hit, so high-frequency generic terms ("papers", "search",
# "doi") and stopwords ("a", "that", "find") inflated tangentially-related
# tools and crowded the genuinely-relevant tool out of the result cap
# (verified: a "papers that cite a DOI" query failed to surface
# get_citations). IDF down-weights terms common across the tool corpus and
# up-weights rare, specific ones (cite, bibtex, remember), so the right tool
# ranks where it should.

_STOPWORDS = frozenset({
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "with",
    "by", "is", "are", "be", "that", "this", "these", "those", "find",
    "list", "get", "show", "me", "my", "our", "please", "can", "you", "it",
    "as", "at", "from", "into", "i", "we", "want", "need", "how", "do",
    "does", "give", "make", "using", "use", "specific", "given", "about",
})

_WORD_RE = re.compile(r"[a-z0-9]+")

_NAME_W = 3.0   # a query term in the tool NAME is a stronger signal than
_DESC_W = 1.0   # the same term buried in a long description.

# Ordered suffix rules for a cheap, dependency-free, DETERMINISTic stemmer.
# Applied to BOTH query and doc tokens so inflected query terms collapse onto
# the tool vocabulary: "cite"/"citing"/"cited"/"citations" -> "cit",
# "reference"/"references" -> "referenc". Longest suffix first; the trailing
# "e" rule is what unifies "cite" with the "citations" family. Not a full
# stemmer (Porter/nltk) - the tool vocabulary is small + technical, so a short
# ruleset suffices and stays reproducible with no dependency.
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "es", "ed", "er", "s", "e")


def _stem(t: str) -> str:
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)]
    return t


def _tokenize(text: str) -> list[str]:
    """Lowercase, stopword-filtered, suffix-stemmed word tokens."""
    return [_stem(t) for t in _WORD_RE.findall(text.lower())
            if t not in _STOPWORDS]


def _doc_tokens(name: str, description: str) -> tuple[set[str], set[str]]:
    """(name-tokens, description-tokens) for a tool. Underscores in the name
    are word boundaries (get_citations -> {get(dropped), citations})."""
    return set(_tokenize(name.replace("_", " "))), set(_tokenize(description))


def _build_idf() -> dict[str, float]:
    """Inverse document frequency over the full tool registry (name +
    description tokens), computed once at import. Rare terms score higher."""
    docs: list[set[str]] = []
    for n, spec in MCP_TOOLS.items():
        nt, dt = _doc_tokens(n, spec.get("description", ""))
        docs.append(nt | dt)
    nd = len(docs) or 1
    df: dict[str, int] = {}
    for toks in docs:
        for t in toks:
            df[t] = df.get(t, 0) + 1
    return {t: math.log((nd + 1) / (1 + c)) + 1.0 for t, c in df.items()}


_IDF: dict[str, float] = _build_idf()
# A term absent from the corpus is maximally rare -> highest weight.
_DEFAULT_IDF = math.log((len(MCP_TOOLS) + 1) / 1) + 1.0


def _score(query_terms: list[str], name: str, description: str) -> float:
    """IDF-weighted relevance. A term in the tool NAME counts more than the
    same term in the description; rare terms count more than common ones."""
    name_toks, desc_toks = _doc_tokens(name, description)
    score = 0.0
    for t in set(query_terms):
        w = _IDF.get(t, _DEFAULT_IDF)
        if t in name_toks:
            score += _NAME_W * w
        elif t in desc_toks:
            score += _DESC_W * w
    return score


async def tool_search(query: str) -> dict:
    """Discover and unlock deferred tools matching ``query``."""
    if not isinstance(query, str) or not query.strip():
        return {"error": "tool_search requires a non-empty 'query' string"}

    # Every tool is discoverable — the per-persona tool_allowlist that once
    # scoped this search was retired in A4b (profiles bias tool use softly,
    # nothing is walled off).
    universe = set(MCP_TOOLS.keys())

    query_terms = _tokenize(query)
    candidates: list[tuple[float, str, dict]] = []
    for name in universe:
        if name in CORE_TOOLS:
            continue  # already in the schema — nothing to unlock
        spec = MCP_TOOLS.get(name)
        if not spec:
            continue
        score = _score(query_terms, name, spec.get("description", ""))
        if score > 0:
            candidates.append((score, name, spec))

    # Highest score first; tie-break on name for deterministic output.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    truncated = len(candidates) > _MAX_RESULTS
    candidates = candidates[:_MAX_RESULTS]

    if not candidates:
        return {
            "query": query,
            "matches": [],
            "note": (
                "No tools matched. Try broader or different terms — or "
                "the core tools already in your schema may cover this."
            ),
        }

    # Unlock the matches so _openai_tools_schema includes them next turn.
    unlocked = current_unlocked_tools.get()
    matched_names = [name for _, name, _ in candidates]
    if unlocked is not None:
        unlocked.update(matched_names)

    matches = [
        {
            "name": name,
            "description": spec.get("description", ""),
            "inputSchema": spec.get("inputSchema", {"type": "object"}),
        }
        for _, name, spec in candidates
    ]
    note = (
        "These tools are now in your schema — call them directly on your "
        "next turn."
    )
    if truncated:
        note += (
            f" More than {_MAX_RESULTS} tools matched; only the top "
            f"{_MAX_RESULTS} are shown — refine your query for the rest."
        )
    logger.info(
        "tool_search %r unlocked %d tool(s): %s",
        query, len(matched_names), matched_names,
    )
    return {"query": query, "matches": matches, "note": note}
