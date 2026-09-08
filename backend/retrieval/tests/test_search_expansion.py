"""Unit tests for search_agent.search()'s SHARED query expansion.

Before this, `search()` passed `query=` to paper_search / semantic_scholar_search
/ web_search, so each tool called `expand_queries` itself: three vLLM expansion
calls per search, and three DIFFERENT variant lists. The tiers were effectively
answering different questions, which is also why their scores could not be
compared. `search()` now expands once and hands the same list to every tier.

No network: the three tier tools and the expander are patched.
"""

import asyncio
import importlib
import sys
from pathlib import Path

# Without these, `python tests/test_search_expansion.py` puts /app/tests on
# sys.path but NOT /app, so `import mcp...` below raises ModuleNotFoundError
# before a single test runs. The file only ever worked under pytest, which
# adds the rootdir itself.
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

importlib.import_module("mcp.tools.search_agent")
SA = sys.modules["mcp.tools.search_agent"]

import mcp.tools.papers as papers_mod  # noqa: E402
import mcp.tools.query_expansion as qe_mod  # noqa: E402
import mcp.tools.web as web_mod  # noqa: E402
import provenance as P  # noqa: E402

VARIANTS = ["base query", "variant one", "variant two"]


class _Recorder:
    """Stand-in for a tier tool that records the kwargs it was called with."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def _install(monkey: dict):
    """Patch the tier tools + expander; returns a restore callable."""
    originals = {
        (papers_mod, "paper_search"): papers_mod.paper_search,
        (papers_mod, "semantic_scholar_search"): papers_mod.semantic_scholar_search,
        (web_mod, "web_search"): web_mod.web_search,
        (qe_mod, "expand_queries"): qe_mod.expand_queries,
        (P, "may_fetch"): P.may_fetch,
    }
    for (mod, name), value in monkey.items():
        setattr(mod, name, value)

    def restore():
        for (mod, name), value in originals.items():
            setattr(mod, name, value)

    return restore


def _run_search(depth="deep"):
    """Run search() with every tier stubbed. Returns (expand_calls, tool_calls)."""
    expand_calls = []

    async def fake_expand(base, n=4, context_hint=None):
        expand_calls.append({"base": base, "n": n})
        return list(VARIANTS)

    corpus = _Recorder({"results": [{"title": "c", "doi": "10.1/c", "score": 0.9}]})
    oa = _Recorder({"results": [{"title": "o", "doi": "10.1/o", "citation_count": 5}]})
    web = _Recorder({"results": [{"title": "w", "url": "https://e.org/w", "matched_by": 2}]})

    restore = _install({
        (papers_mod, "paper_search"): corpus,
        (papers_mod, "semantic_scholar_search"): oa,
        (web_mod, "web_search"): web,
        (qe_mod, "expand_queries"): fake_expand,
        (P, "may_fetch"): lambda *_a, **_k: True,
    })
    try:
        out = asyncio.run(SA.search(query="base query", depth=depth, top_k=10))
    finally:
        restore()
    return expand_calls, {"corpus": corpus, "oa": oa, "web": web}, out


def test_expands_exactly_once():
    expand_calls, _tools, _out = _run_search()
    assert len(expand_calls) == 1, f"expected one expansion, got {expand_calls}"
    assert expand_calls[0]["base"] == "base query"


def test_every_tier_gets_the_same_variant_list():
    _expand, tools, _out = _run_search()
    for name, rec in tools.items():
        assert rec.calls, f"{name} tier was never called"
        kwargs = rec.calls[0]
        assert kwargs.get("queries") == VARIANTS, f"{name} got {kwargs!r}"
        # Passing `query=` would make the tool expand again on its own.
        assert "query" not in kwargs, f"{name} was handed a raw query: {kwargs!r}"


def test_base_query_is_still_executed_verbatim():
    """expand_queries returns [base, ...]; the tiers must receive that head so
    the user's exact wording is still run against every tier."""
    _expand, tools, _out = _run_search()
    assert tools["oa"].calls[0]["queries"][0] == "base query"


def test_web_tier_skipped_when_not_deep():
    _expand, tools, _out = _run_search(depth="normal")
    assert tools["corpus"].calls, "corpus should always run"
    assert tools["oa"].calls, "oa should run at normal depth"
    assert not tools["web"].calls, "web must not fan out below deep"


if __name__ == "__main__":
    # Standalone runner, per the convention in tests/README.md: these files
    # must print PASS/FAIL and exit non-zero under plain `python`, not only
    # under pytest (which is not installed in the retrieval container).
    # Functions taking parameters want a pytest fixture and are reported as
    # skipped rather than called with nothing, which would fail misleadingly.
    import inspect as _inspect
    import sys as _sys
    import traceback as _traceback
    import types as _types

    _passed = _failed = _skipped = 0
    for _name, _fn in sorted(globals().items()):
        if not (_name.startswith("test_") and isinstance(_fn, _types.FunctionType)):
            continue
        if _inspect.signature(_fn).parameters:
            print(f"[SKIP] {_name} (needs a pytest fixture)")
            _skipped += 1
            continue
        try:
            _fn()
            print(f"[PASS] {_name}")
            _passed += 1
        except Exception:
            print(f"[FAIL] {_name}")
            _traceback.print_exc()
            _failed += 1
    print(f"\n{_passed} passed, {_failed} failed, {_skipped} skipped")
    _sys.exit(1 if _failed else 0)
