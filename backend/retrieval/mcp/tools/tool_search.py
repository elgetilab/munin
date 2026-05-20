"""
MCP tool: tool_search (P1 #7 from munin-audit.md).

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

from ..context import current_persona, current_unlocked_tools
from ..schemas import MCP_TOOLS, CORE_TOOLS

logger = logging.getLogger(__name__)

# Cap on schemas returned in one call. A query that matches more than
# this gets the top-ranked slice plus a "refine your query" note.
_MAX_RESULTS = 8


def _score(query_terms: list[str], name: str, description: str) -> int:
    """Cheap relevance score: a query term in the tool name is worth more
    than the same term in the (long) description."""
    name_l = name.lower()
    desc_l = description.lower()
    score = 0
    for t in query_terms:
        if t in name_l:
            score += 3
        if t in desc_l:
            score += 1
    return score


async def tool_search(query: str) -> dict:
    """Discover and unlock deferred tools matching ``query``."""
    if not isinstance(query, str) or not query.strip():
        return {"error": "tool_search requires a non-empty 'query' string"}

    # Resolve the calling persona's tool universe. A persona with no
    # explicit allowlist (legacy) searches the full registry.
    import personas as persona_module

    persona = persona_module.get_persona(current_persona.get())
    allow = persona_module.tool_allowlist(persona) if persona else None
    universe = set(allow) if allow is not None else set(MCP_TOOLS.keys())

    query_terms = [t for t in query.lower().split() if t]
    candidates: list[tuple[int, str, dict]] = []
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
