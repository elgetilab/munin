"""Regression tests: `search` called with a `queries` list.

The model keeps sending `queries: [...]` to `search` (paper_search and
web_search both take one, and the research persona used to say "ALWAYS pass a
`queries` array"). 33 of 307 calls failed on it: "'query' is a required
property" from the schema validator when ONLY `queries` was sent, and
"search does not take a 'queries' argument" when both were.

Decided behaviour: accept it. A missing `query` is filled from `queries[0]`
(a stringified list too). If more than one query was given, only the first
runs and the result carries a `note` telling the model the rest were not run
and to call search again for each.

Runs the full executor path (alias + coercion + schema validation + dispatch)
with the tier tools and the expander stubbed: no network, no Qdrant.

    python /app/tests/test_search_queries_arg.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mcp.executor as executor  # noqa: E402
import mcp.tools.papers as papers_mod  # noqa: E402
import mcp.tools.query_expansion as qe_mod  # noqa: E402
import mcp.tools.web as web_mod  # noqa: E402
import provenance as P  # noqa: E402


class _Recorder:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.payload


def _run(arguments: dict):
    """Dispatch `search` through the executor with every tier stubbed.
    Returns (result, list of base queries handed to the expander)."""
    expanded = []

    async def fake_expand(base, n=4, context_hint=None):
        expanded.append(base)
        return [base]

    corpus = _Recorder({"results": [{"title": "Synthetic corpus paper",
                                     "doi": "10.0000/synthetic.1",
                                     "excerpt": "synthetic excerpt",
                                     "score": 0.9}]})
    empty = _Recorder({"results": []})
    originals = {
        (papers_mod, "paper_search"): papers_mod.paper_search,
        (papers_mod, "semantic_scholar_search"): papers_mod.semantic_scholar_search,
        (web_mod, "web_search"): web_mod.web_search,
        (qe_mod, "expand_queries"): qe_mod.expand_queries,
        (P, "may_fetch"): P.may_fetch,
    }
    papers_mod.paper_search = corpus
    papers_mod.semantic_scholar_search = empty
    web_mod.web_search = empty
    qe_mod.expand_queries = fake_expand
    P.may_fetch = lambda *_a, **_k: False
    try:
        out = asyncio.run(executor._dispatch_mcp_tool("search", arguments))
    finally:
        for (mod, name), value in originals.items():
            setattr(mod, name, value)
    return out, expanded


def test_queries_only_list_runs_first_and_notes_rest():
    out, expanded = _run({"queries": ["alpha topic", "beta topic"]})
    assert not out.get("error"), f"rejected: {out!r}"
    assert expanded == ["alpha topic"], f"ran {expanded!r}"
    note = out.get("note") or ""
    assert "beta topic" in note and "search" in note, f"note {note!r}"


def test_queries_only_stringified_list():
    out, expanded = _run({"queries": '["alpha topic", "beta topic"]'})
    assert not out.get("error"), f"rejected: {out!r}"
    assert expanded == ["alpha topic"], f"ran {expanded!r}"
    assert "beta topic" in (out.get("note") or ""), f"got {out!r}"


def test_single_queries_entry_has_no_note():
    out, expanded = _run({"queries": ["alpha topic"]})
    assert not out.get("error"), f"rejected: {out!r}"
    assert expanded == ["alpha topic"], f"ran {expanded!r}"
    assert not out.get("note"), f"unexpected note {out.get('note')!r}"


def test_query_and_queries_runs_query_and_notes_rest():
    out, expanded = _run({"query": "alpha topic",
                          "queries": ["beta topic", "gamma topic"]})
    assert not out.get("error"), f"rejected: {out!r}"
    assert expanded == ["alpha topic"], f"ran {expanded!r}"
    note = out.get("note") or ""
    assert "beta topic" in note and "gamma topic" in note, f"note {note!r}"


def test_query_repeated_in_queries_is_not_listed_as_unrun():
    out, _ = _run({"query": "alpha topic",
                   "queries": ["alpha topic", "beta topic"]})
    note = out.get("note") or ""
    assert "beta topic" in note, f"note {note!r}"
    assert "alpha topic" not in note.split("NOT run")[-1], f"note {note!r}"


def test_plain_query_unchanged():
    out, expanded = _run({"query": "alpha topic"})
    assert not out.get("error"), f"rejected: {out!r}"
    assert expanded == ["alpha topic"]
    assert "note" not in out, f"unexpected note {out!r}"


def test_no_query_and_empty_queries_still_errors():
    out, expanded = _run({"queries": []})
    assert out.get("error"), f"expected an error, got {out!r}"
    assert expanded == []


if __name__ == "__main__":
    import traceback as _traceback
    import types as _types

    _passed = _failed = 0
    for _name, _fn in sorted(globals().items()):
        if not (_name.startswith("test_") and isinstance(_fn, _types.FunctionType)):
            continue
        try:
            _fn()
            print(f"[PASS] {_name}")
            _passed += 1
        except Exception:
            print(f"[FAIL] {_name}")
            _traceback.print_exc()
            _failed += 1
    print(f"\n{_passed} passed, {_failed} failed")
    sys.exit(1 if _failed else 0)
