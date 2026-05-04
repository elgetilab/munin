"""
Standalone unit tests for the per-persona tool allowlist
(`personas.tool_allowlist` + `chat_service._openai_tools_schema`)
introduced 2026-04-28.

Covers:
- The allowlist helper: missing field returns None, valid list
  returns a normalised list, malformed entries are dropped.
- Schema filtering: persona with allowlist returns a strict subset,
  persona without allowlist returns the full schema (with a one-shot
  warning), unknown tool names in the allowlist are silently skipped.
- The three real personas (chat / code / research) each end up with
  the expected tool count after filtering.

Pure-function tests — no I/O, no model call, no DB. Behavioural
coverage of the broader chat path lives in the flakiness suite.

Runs in-process inside the retrieval container:

    docker exec munin-retrieval python /app/tests/test_tool_allowlist.py

Exit 0 = pass, non-zero = fail.
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")

import personas as persona_module  # noqa: E402
from chat_service import _openai_tools_schema  # noqa: E402
from mcp.schemas import MCP_TOOLS  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' — ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# personas.tool_allowlist helper
# ---------------------------------------------------------------------------

def test_allowlist_none_for_missing_field() -> bool:
    p = {"id": "x", "params": {"system": "stub"}}
    out = persona_module.tool_allowlist(p)
    return _check(
        "missing tool_allowlist → None (back-compat)",
        out is None,
        f"got {out!r}",
    )


def test_allowlist_none_for_non_dict() -> bool:
    return _check(
        "non-dict persona → None (defensive)",
        persona_module.tool_allowlist(None) is None  # type: ignore[arg-type]
        and persona_module.tool_allowlist("foo") is None  # type: ignore[arg-type]
        and persona_module.tool_allowlist(42) is None,  # type: ignore[arg-type]
    )


def test_allowlist_returns_string_list() -> bool:
    """Valid allowlist passes through; ``delegate_to_persona`` is
    auto-injected so all-to-all routing works without each persona
    spelling it out."""
    p = {
        "id": "x",
        "params": {"tool_allowlist": ["web_search", "calculate", "compile_latex"]},
    }
    out = persona_module.tool_allowlist(p)
    return _check(
        "valid allowlist → list of strings + delegate auto-inject",
        out == ["web_search", "calculate", "compile_latex", "delegate_to_persona"],
        f"got {out!r}",
    )


def test_allowlist_drops_non_strings() -> bool:
    """Defensive: malformed entries (numbers, dicts, empty strings)
    are silently dropped rather than raising. delegate_to_persona is
    still auto-injected at the end."""
    p = {
        "id": "x",
        "params": {
            "tool_allowlist": ["web_search", 42, "", None, {"x": 1}, "calculate"],
        },
    }
    out = persona_module.tool_allowlist(p)
    return _check(
        "allowlist drops non-string entries (delegate auto-injected)",
        out == ["web_search", "calculate", "delegate_to_persona"],
        f"got {out!r}",
    )


def test_allowlist_explicit_delegate_not_duplicated() -> bool:
    """If the persona JSON explicitly lists delegate_to_persona, the
    auto-injection must dedupe rather than emit it twice."""
    p = {
        "id": "x",
        "params": {
            "tool_allowlist": ["web_search", "delegate_to_persona", "calculate"],
        },
    }
    out = persona_module.tool_allowlist(p)
    return _check(
        "explicit delegate not duplicated",
        out == ["web_search", "delegate_to_persona", "calculate"],
        f"got {out!r}",
    )


def test_allowlist_non_list_returns_none() -> bool:
    p = {"id": "x", "params": {"tool_allowlist": "calculate"}}
    out = persona_module.tool_allowlist(p)
    return _check(
        "non-list allowlist field → None (treated as missing)",
        out is None,
        f"got {out!r}",
    )


# ---------------------------------------------------------------------------
# _openai_tools_schema filtering
# ---------------------------------------------------------------------------

def test_schema_no_persona_returns_all_tools() -> bool:
    out = _openai_tools_schema(None)
    names = [t["function"]["name"] for t in out]
    return _check(
        "no persona → full schema returned",
        sorted(names) == sorted(MCP_TOOLS.keys()),
        f"got {len(names)} tools, expected {len(MCP_TOOLS)}",
    )


def test_schema_persona_without_allowlist_returns_all_tools() -> bool:
    """Back-compat: a persona JSON written before allowlist support
    must still work. Falls back to full schema with a logged warning."""
    p = {"id": "legacy-test", "params": {"system": "stub"}}
    out = _openai_tools_schema(p)
    names = [t["function"]["name"] for t in out]
    return _check(
        "persona without allowlist → full schema (back-compat)",
        sorted(names) == sorted(MCP_TOOLS.keys()),
        f"got {len(names)} tools, expected {len(MCP_TOOLS)}",
    )


def test_schema_filters_to_allowlist() -> bool:
    """Allowlist is honoured; delegate_to_persona is auto-injected
    so the model can always escape to another persona."""
    p = {
        "id": "filter-test",
        "params": {"tool_allowlist": ["web_search", "calculate"]},
    }
    out = _openai_tools_schema(p)
    names = sorted(t["function"]["name"] for t in out)
    return _check(
        "allowlist → only listed tools (+ delegate auto-inject)",
        names == ["calculate", "delegate_to_persona", "web_search"],
        f"got {names!r}",
    )


def test_schema_unknown_tool_in_allowlist_is_dropped() -> bool:
    """An allowlist entry that doesn't match a known MCP tool is
    silently ignored — that way personas survive a tool rename
    without throwing on every request. delegate_to_persona is still
    auto-injected."""
    p = {
        "id": "filter-test",
        "params": {"tool_allowlist": ["web_search", "totally_made_up_tool"]},
    }
    out = _openai_tools_schema(p)
    names = sorted(t["function"]["name"] for t in out)
    return _check(
        "unknown allowlist entry dropped (delegate auto-injected)",
        names == ["delegate_to_persona", "web_search"],
        f"got {names!r}",
    )


def test_schema_empty_allowlist_returns_only_delegate() -> bool:
    """An explicit empty list still gets delegate_to_persona injected
    — every persona must be able to escape to another. Personas that
    truly want zero tools can either omit the allowlist (back-compat
    full schema) or accept the single delegate entry."""
    p = {"id": "empty-test", "params": {"tool_allowlist": []}}
    out = _openai_tools_schema(p)
    names = [t["function"]["name"] for t in out]
    return _check(
        "empty allowlist → only delegate_to_persona",
        names == ["delegate_to_persona"],
        f"got {names!r}",
    )


# ---------------------------------------------------------------------------
# Real personas — sanity check the shipped allowlists
# ---------------------------------------------------------------------------

def test_chat_persona_has_essentials() -> bool:
    """The chat persona must include the tools its system prompt
    references by name. A drift between the prompt and the allowlist
    would silently make the model unable to do what the prompt
    promises."""
    chat = persona_module.get_persona("chat")
    if chat is None:
        return _check("chat persona loaded", False, "persona not found")
    allow = persona_module.tool_allowlist(chat) or []
    required = {
        "compile_latex",
        "run_python",
        "calculate",
        "ask_clarification",
        "deep_research",
        "web_search",
        "paper_search",
    }
    missing = required - set(allow)
    return _check(
        "chat allowlist includes essentials",
        not missing,
        f"missing: {sorted(missing)}",
    )


def test_code_persona_excludes_research_tools() -> bool:
    code = persona_module.get_persona("code")
    if code is None:
        return _check("code persona loaded", False, "persona not found")
    allow = set(persona_module.tool_allowlist(code) or [])
    must_exclude = {
        "deep_research",
        "paper_search",
        "semantic_scholar_search",
        "read_paper",
        "compare_papers",
    }
    leaked = must_exclude & allow
    return _check(
        "code allowlist excludes research-only tools",
        not leaked,
        f"leaked: {sorted(leaked)}",
    )


def test_research_persona_includes_paper_tools() -> bool:
    research = persona_module.get_persona("research")
    if research is None:
        return _check("research persona loaded", False, "persona not found")
    allow = set(persona_module.tool_allowlist(research) or [])
    required = {
        "deep_research",
        "paper_search",
        "semantic_scholar_search",
        "read_paper",
        "compare_papers",
        "export_citations",
    }
    missing = required - allow
    return _check(
        "research allowlist includes paper tools",
        not missing,
        f"missing: {sorted(missing)}",
    )


def test_chat_schema_smaller_than_full() -> bool:
    """The whole point: chat persona's schema must be substantially
    smaller than the unfiltered one. Loose target: ≤80% of full."""
    chat = persona_module.get_persona("chat")
    if chat is None:
        return _check("chat schema smaller than full", False, "no chat persona")
    full = _openai_tools_schema(None)
    chat_schema = _openai_tools_schema(chat)
    ratio = len(chat_schema) / max(1, len(full))
    return _check(
        "chat schema is a strict subset (≤80% of full)",
        len(chat_schema) < len(full) and ratio <= 0.8,
        f"chat={len(chat_schema)} full={len(full)} ratio={ratio:.2f}",
    )


# ---------------------------------------------------------------------------
# delegate_to_persona auto-injection + enum narrowing
# ---------------------------------------------------------------------------

def test_delegate_in_every_persona_schema() -> bool:
    """Every persona's effective schema must include
    ``delegate_to_persona`` so all-to-all routing works without each
    persona JSON spelling it out."""
    missing = []
    for pid in ("chat", "code", "research"):
        p = persona_module.get_persona(pid)
        if p is None:
            return _check(
                "delegate_to_persona auto-injected",
                False,
                f"{pid} persona missing",
            )
        names = [t["function"]["name"] for t in _openai_tools_schema(p)]
        if "delegate_to_persona" not in names:
            missing.append(pid)
    return _check(
        "delegate_to_persona present in chat/code/research schemas",
        not missing,
        f"missing in: {missing}",
    )


def test_delegate_enum_excludes_self() -> bool:
    """When a persona compiles its own schema, the
    ``delegate_to_persona.persona_id`` enum must NOT include its own
    id — otherwise the model could call delegate(persona_id=self),
    which is a no-op that wastes a turn."""
    failures = []
    for pid in ("chat", "code", "research"):
        p = persona_module.get_persona(pid)
        if p is None:
            failures.append(f"{pid}: persona not loaded")
            continue
        schema = _openai_tools_schema(p)
        entry = next(
            (t for t in schema if t["function"]["name"] == "delegate_to_persona"),
            None,
        )
        if entry is None:
            failures.append(f"{pid}: no delegate_to_persona in schema")
            continue
        params = entry["function"]["parameters"]
        enum = (params.get("properties") or {}).get("persona_id", {}).get("enum")
        if not enum:
            failures.append(f"{pid}: no enum on persona_id")
            continue
        if pid in enum:
            failures.append(f"{pid}: enum still contains self ({enum!r})")
    return _check(
        "delegate persona_id enum excludes self per-persona",
        not failures,
        "; ".join(failures),
    )


def test_delegate_enum_includes_other_personas() -> bool:
    """Sanity: chat's enum should at least include code + research
    so the model has somewhere to delegate to."""
    chat = persona_module.get_persona("chat")
    if chat is None:
        return _check("chat enum includes others", False, "no chat persona")
    schema = _openai_tools_schema(chat)
    entry = next(
        (t for t in schema if t["function"]["name"] == "delegate_to_persona"),
        None,
    )
    if entry is None:
        return _check(
            "chat enum includes others", False, "delegate_to_persona missing"
        )
    enum = set(
        (entry["function"]["parameters"].get("properties") or {})
        .get("persona_id", {})
        .get("enum", [])
    )
    return _check(
        "chat's delegate enum includes code + research",
        {"code", "research"}.issubset(enum),
        f"got {sorted(enum)}",
    )


def test_delegate_in_mcp_tools_registry() -> bool:
    """The MCP tool registry must carry delegate_to_persona — without
    that the schema generator has nothing to emit."""
    return _check(
        "delegate_to_persona registered in MCP_TOOLS",
        "delegate_to_persona" in MCP_TOOLS,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_allowlist_none_for_missing_field,
    test_allowlist_none_for_non_dict,
    test_allowlist_returns_string_list,
    test_allowlist_drops_non_strings,
    test_allowlist_explicit_delegate_not_duplicated,
    test_allowlist_non_list_returns_none,
    test_schema_no_persona_returns_all_tools,
    test_schema_persona_without_allowlist_returns_all_tools,
    test_schema_filters_to_allowlist,
    test_schema_unknown_tool_in_allowlist_is_dropped,
    test_schema_empty_allowlist_returns_only_delegate,
    test_chat_persona_has_essentials,
    test_code_persona_excludes_research_tools,
    test_research_persona_includes_paper_tools,
    test_chat_schema_smaller_than_full,
    test_delegate_in_every_persona_schema,
    test_delegate_enum_excludes_self,
    test_delegate_enum_includes_other_personas,
    test_delegate_in_mcp_tools_registry,
]


def main() -> int:
    # The real-persona tests need personas loaded.
    persona_module.PERSONAS_DIR = "/backend/personas"
    persona_module.load_personas()

    failed = 0
    for fn in TESTS:
        try:
            if not fn():
                failed += 1
        except Exception:
            failed += 1
            print(f"[FAIL] {fn.__name__} raised:")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
