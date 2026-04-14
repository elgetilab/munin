"""
Capability introspection block (§4 passive half).

Builds a compact ``=== CAPABILITIES ===`` block that chat_service
prepends to the system prompt on persistent chats. The block
summarises what the model can actually do: MCP tools, registered
agents, available personas, static user-facing features, and the
list of FAQ topics (so the model knows the ``faq`` tool exists and
what topic ids to pass).

Why this exists: users ask "what can you do?" in plain English and
expect a real answer. Without this block the model hallucinates a
generic list or says it doesn't know. Budgeted at ~500-600 tokens
per request, bounded, worth it.

This is the *passive* half of §4. The *active* half is the ``faq``
MCP tool (``retrieval/mcp/tools/faq.py``) which the model calls on
demand when a user asks a specific how-to question with a topic
that matches the YAML.
"""

from __future__ import annotations

from typing import Optional


# Static user-facing feature list. Kept in code rather than the FAQ
# YAML because these are structural capabilities, not tunable
# "explain this" answers - they change with the codebase, not with
# admin curation.
_STATIC_FEATURES = (
    "Upload documents (PDF, TXT, MD, DOCX, PNG, JPG, WEBP) via the + button",
    "Switch personas in the sidebar (Meitner chat / Turing code / Curie research)",
    "Organize chats into Projects with per-project instructions and docs",
    "Versioned document artifacts in the side panel (drafts, code, plots, spreadsheets)",
    "Toggle Ephemeral mode for chats that are never persisted",
    "Check system status at /api/status",
)


def _one_line(text: str) -> str:
    """Take the first sentence of a multi-sentence tool description so
    the capabilities block stays compact. Falls back to the first 120
    chars if the description has no sentence terminator."""
    if not text:
        return ""
    # Cheap sentence split: stop at the first period/exclamation/question
    # that is followed by a space or end-of-string. Avoids the heavier
    # nltk dependency.
    for terminator in (". ", "! ", "? "):
        idx = text.find(terminator)
        if 20 < idx < 200:
            return text[: idx + 1].strip()
    return text[:160].strip()


def _tool_one_liners() -> list[str]:
    """Pull name + first-sentence description for every MCP tool."""
    from mcp.schemas import MCP_TOOLS  # lazy to avoid circular init

    lines: list[str] = []
    for name, spec in sorted(MCP_TOOLS.items()):
        summary = _one_line(spec.get("description") or "")
        lines.append(f"  - {name}: {summary}" if summary else f"  - {name}")
    return lines


def _agent_one_liners() -> list[str]:
    try:
        import agents as agents_pkg
    except Exception:
        return []
    try:
        listed = agents_pkg.list_agents()
    except Exception:
        return []
    out: list[str] = []
    for a in listed:
        name = a.get("name") or "?"
        desc = _one_line(a.get("description") or "")
        out.append(f"  - {name}: {desc}" if desc else f"  - {name}")
    return out


def _persona_one_liners() -> list[str]:
    try:
        import personas as persona_module
    except Exception:
        return []
    try:
        public = persona_module.public_personas()
    except Exception:
        return []
    out: list[str] = []
    for p in public.get("personas") or []:
        pid = p.get("id") or "?"
        name = p.get("name") or pid
        desc = _one_line(p.get("description") or "")
        line = f"  - {pid} ({name})"
        if desc:
            line += f": {desc}"
        out.append(line)
    return out


def _faq_topic_ids() -> list[str]:
    """Return the list of FAQ topic ids so the model can pass them to
    the faq tool. We import the loader lazily so capabilities.py
    doesn't pull in PyYAML when the tool isn't being used."""
    try:
        from mcp.tools.faq import list_faq_topics
    except Exception:
        return []
    try:
        return list_faq_topics()
    except Exception:
        return []


def build_capabilities_block() -> Optional[str]:
    """
    Render the ``=== CAPABILITIES ===`` block. Returns None if
    everything is empty (unusual - only happens if imports fail
    catastrophically).
    """
    sections: list[str] = ["=== CAPABILITIES ==="]

    tools = _tool_one_liners()
    if tools:
        sections.append("")
        sections.append("Available tools:")
        sections.extend(tools)

    agents = _agent_one_liners()
    if agents:
        sections.append("")
        sections.append("Available agents (call via invoke_agent):")
        sections.extend(agents)

    personas = _persona_one_liners()
    if personas:
        sections.append("")
        sections.append("Personas the user can pick from:")
        sections.extend(personas)

    if _STATIC_FEATURES:
        sections.append("")
        sections.append("User-facing features (describe these when asked how-to):")
        for feat in _STATIC_FEATURES:
            sections.append(f"  - {feat}")

    faq_topics = _faq_topic_ids()
    if faq_topics:
        sections.append("")
        sections.append(
            "FAQ topics (call faq(topic=...) or faq(search=...) for "
            "admin-curated answers on these):"
        )
        sections.append("  " + ", ".join(sorted(faq_topics)))

    sections.append("=== END CAPABILITIES ===")
    body = "\n".join(sections)
    # Sanity check: if we somehow rendered only the headers and no
    # content, return None so the caller can skip injection.
    if len(sections) <= 2:
        return None
    return body
