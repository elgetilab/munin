"""
Standalone tests for the deferred-tool schema + tool_search
(P1 #7 from munin-audit.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_tool_search.py
Or locally:
    python backend/retrieval/tests/test_tool_search.py

tool_search resolves the calling persona via personas.get_persona; the
tests monkeypatch that to return a synthetic persona so they don't
depend on the persona JSON files being on disk. The real
personas.tool_allowlist runs on the synthetic persona.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import personas as persona_module  # noqa: E402
from mcp.context import current_persona, current_unlocked_tools  # noqa: E402
from mcp.schemas import CORE_TOOLS, MCP_TOOLS  # noqa: E402
from mcp.tools.tool_search import tool_search, _MAX_RESULTS  # noqa: E402
from chat_service import _openai_tools_schema  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------

_real_get_persona = persona_module.get_persona


def _install_persona(allowlist):
    """Monkeypatch personas.get_persona to return a synthetic persona with
    the given tool_allowlist (or no allowlist when allowlist is None)."""
    params = {} if allowlist is None else {"tool_allowlist": list(allowlist)}
    synthetic = {"id": "test", "params": params}
    persona_module.get_persona = lambda _id: synthetic
    return synthetic


def _uninstall_persona():
    persona_module.get_persona = _real_get_persona


# A broad allowlist covering most of the registry for discovery tests.
_BROAD = [
    "web_search", "web_fetch", "paper_search", "semantic_scholar_search",
    "paper_lookup", "read_paper", "compare_papers", "get_citations",
    "get_references", "s2_get_citations", "s2_get_references",
    "get_author_papers", "get_paper_pdf", "check_papers_availability",
    "export_citations", "compile_latex", "run_python", "sandbox_reset",
    "calculate", "ask_clarification", "create_artifact", "read_artifact",
    "update_artifact", "list_artifacts", "save_artifact_to_documents",
    "search_user_docs", "search_past_conversations", "list_projects",
    "get_current_project", "remember", "forget", "recall", "invoke_agent",
    "deep_research", "view_attachment", "transcribe_equation", "faq",
]


# ---------------------------------------------------------------------------
# tool_search
# ---------------------------------------------------------------------------

def test_tool_search_keyword_match() -> bool:
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        res = asyncio.run(tool_search("papers that cite a given DOI"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    names = {m["name"] for m in res.get("matches", [])}
    return _check(
        "tool_search keyword-matches citation tools",
        "get_citations" in names or "s2_get_citations" in names,
        f"names={names}",
    )


def test_tool_search_unlocks_into_contextvar() -> bool:
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    unlocked: set = set()
    tok_u = current_unlocked_tools.set(unlocked)
    try:
        res = asyncio.run(tool_search("compile latex to pdf"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    matched = {m["name"] for m in res.get("matches", [])}
    return _check(
        "tool_search adds matches to current_unlocked_tools",
        matched and matched.issubset(unlocked),
        f"matched={matched}, unlocked={unlocked}",
    )


def test_tool_search_excludes_core_tools() -> bool:
    """paper_search is core — already in the schema — so tool_search must
    not bother returning it even though 'search papers' matches it."""
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        res = asyncio.run(tool_search("search papers"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    names = {m["name"] for m in res.get("matches", [])}
    return _check(
        "tool_search never returns core tools",
        names.isdisjoint(CORE_TOOLS),
        f"names ∩ CORE = {names & CORE_TOOLS}",
    )


def test_tool_search_respects_allowlist() -> bool:
    """A persona whose allowlist excludes compile_latex must never have
    compile_latex surfaced by tool_search."""
    no_latex = [t for t in _BROAD if t != "compile_latex"]
    _install_persona(no_latex)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        res = asyncio.run(tool_search("compile latex document to pdf"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    names = {m["name"] for m in res.get("matches", [])}
    return _check(
        "tool_search respects the persona allowlist",
        "compile_latex" not in names,
        f"names={names}",
    )


def test_tool_search_caps_at_max() -> bool:
    """A deliberately broad query that matches many tools returns at most
    _MAX_RESULTS results with a truncation note."""
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        # "paper" appears in a great many tool descriptions.
        res = asyncio.run(tool_search("paper"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    matches = res.get("matches", [])
    return _check(
        f"tool_search caps results at {_MAX_RESULTS}",
        len(matches) <= _MAX_RESULTS,
        f"len(matches)={len(matches)}",
    )


def _surfaces(query: str, target: str) -> bool:
    """Helper: does tool_search(query) put `target` in its matches?"""
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        res = asyncio.run(tool_search(query))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    return target in {m["name"] for m in res.get("matches", [])}


def test_tool_search_surfaces_gate_tools_natural_phrasing() -> bool:
    """A2 regression: the IDF-weighted matcher must surface the genuinely-
    relevant deferred tool for NATURAL queries, where the old substring
    scorer crowded it out with generic-term matches. These are the routing-
    eval gate tools (export_citations, get_citations) + remember."""
    cases = [
        ("give me BibTeX for these DOIs", "export_citations"),
        ("find papers that cite a given DOI", "get_citations"),
        ("remember that I work on dissolution-DNP going forward", "remember"),
    ]
    missing = [(q, t) for q, t in cases if not _surfaces(q, t)]
    return _check(
        "tool_search surfaces gate tools for natural phrasing",
        not missing,
        f"missing={missing}",
    )


def test_tool_search_no_match() -> bool:
    _install_persona(_BROAD)
    tok_p = current_persona.set("test")
    tok_u = current_unlocked_tools.set(set())
    try:
        res = asyncio.run(tool_search("xyzzy plugh nonsense qwerty"))
    finally:
        current_persona.reset(tok_p)
        current_unlocked_tools.reset(tok_u)
        _uninstall_persona()
    return _check(
        "tool_search returns empty matches on a no-match query",
        res.get("matches") == [] and "note" in res,
        f"res={res}",
    )


def test_tool_search_empty_query() -> bool:
    res = asyncio.run(tool_search("   "))
    return _check(
        "tool_search rejects an empty query",
        "error" in res,
        f"res={res}",
    )


# ---------------------------------------------------------------------------
# _openai_tools_schema deferral
# ---------------------------------------------------------------------------

def _schema_names(persona: dict) -> set:
    return {t["function"]["name"] for t in _openai_tools_schema(persona)}


def test_schema_core_only_when_nothing_unlocked() -> bool:
    persona = {"id": "test", "params": {"tool_allowlist": list(_BROAD)}}
    tok = current_unlocked_tools.set(set())
    try:
        names = _schema_names(persona)
    finally:
        current_unlocked_tools.reset(tok)
    # Schema should be exactly CORE intersected with the (broad) universe.
    # _BROAD covers all of CORE except tool_search/delegate_to_persona,
    # which tool_allowlist auto-injects, so the full CORE is present.
    return _check(
        "schema is core-only when nothing is unlocked",
        names == set(CORE_TOOLS),
        f"names={sorted(names)}",
    )


def test_schema_includes_unlocked_after_search() -> bool:
    persona = {"id": "test", "params": {"tool_allowlist": list(_BROAD)}}
    tok = current_unlocked_tools.set({"get_citations", "compile_latex"})
    try:
        names = _schema_names(persona)
    finally:
        current_unlocked_tools.reset(tok)
    return _check(
        "schema includes unlocked tools after a search",
        "get_citations" in names and "compile_latex" in names
        and set(CORE_TOOLS).issubset(names),
        f"names={sorted(names)}",
    )


def test_schema_core_clamped_to_allowlist() -> bool:
    """A code-style persona without paper_search/read_paper must not get
    those core tools — core is intersected with the allowlist."""
    code_like = [
        "web_search", "run_python", "calculate", "create_artifact",
        "ask_clarification", "compile_latex",
    ]
    persona = {"id": "test", "params": {"tool_allowlist": code_like}}
    tok = current_unlocked_tools.set(set())
    try:
        names = _schema_names(persona)
    finally:
        current_unlocked_tools.reset(tok)
    return _check(
        "core is clamped to the persona allowlist",
        "paper_search" not in names and "read_paper" not in names
        and "run_python" in names and "tool_search" in names,
        f"names={sorted(names)}",
    )


def test_schema_materially_smaller_than_universe() -> bool:
    """The whole point: the schema is far smaller than the persona's
    full tool universe."""
    persona = {"id": "test", "params": {"tool_allowlist": list(_BROAD)}}
    tok = current_unlocked_tools.set(set())
    try:
        schema_size = len(_schema_names(persona))
    finally:
        current_unlocked_tools.reset(tok)
    universe_size = len(persona_module.tool_allowlist(persona))
    # Core-only schema == CORE_TOOLS clamped to the allowlist. Track the
    # actual CORE size (it grew to 11 when plan-mode tools were added) rather
    # than a stale hardcoded bound; the real assertion is "materially smaller
    # than the universe".
    return _check(
        "deferred schema is materially smaller than the tool universe",
        schema_size <= len(CORE_TOOLS) and schema_size < universe_size // 2,
        f"schema={schema_size}, universe={universe_size}, core={len(CORE_TOOLS)}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_tool_search_keyword_match,
    test_tool_search_unlocks_into_contextvar,
    test_tool_search_excludes_core_tools,
    test_tool_search_respects_allowlist,
    test_tool_search_caps_at_max,
    test_tool_search_surfaces_gate_tools_natural_phrasing,
    test_tool_search_no_match,
    test_tool_search_empty_query,
    test_schema_core_only_when_nothing_unlocked,
    test_schema_includes_unlocked_after_search,
    test_schema_core_clamped_to_allowlist,
    test_schema_materially_smaller_than_universe,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
