"""
Standalone tests for the MCP tool argument-validation gate
(mcp/executor.py, P0 #4 from munin-audit.md).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_tool_validation.py
Or locally:
    python backend/retrieval/tests/test_tool_validation.py

Hermetic: the only tool we actually dispatch through is ``calculate``,
which is a pure sympy/pint wrapper with no I/O. Every other case
exercises the validator before the dispatch table fires.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jsonschema import Draft202012Validator, ValidationError  # noqa: E402

from mcp.executor import (  # noqa: E402
    _VALIDATORS,
    _format_validation_error,
    execute_mcp_tool,
)
from mcp.schemas import MCP_TOOLS  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Module-load invariants
# ---------------------------------------------------------------------------

def test_every_tool_has_a_compiled_validator() -> bool:
    """Every MCP_TOOLS entry with an inputSchema must produce a validator
    at module load. Drift here (a new tool with no schema, or a schema
    that fails check_schema) is what this gate exists to catch."""
    expected = {
        name for name, meta in MCP_TOOLS.items()
        if isinstance(meta.get("inputSchema"), dict)
    }
    return _check(
        "every tool with an inputSchema has a compiled validator",
        set(_VALIDATORS.keys()) == expected,
        f"missing={expected - set(_VALIDATORS.keys())}, "
        f"extra={set(_VALIDATORS.keys()) - expected}",
    )


def test_validators_are_singletons() -> bool:
    """Validators are built once at import and reused — important so we
    don't pay schema compilation per tool call."""
    from mcp import executor
    v1 = executor._VALIDATORS["paper_search"]
    v2 = executor._VALIDATORS["paper_search"]
    return _check(
        "validators are module-level singletons",
        v1 is v2 and isinstance(v1, Draft202012Validator),
    )


# ---------------------------------------------------------------------------
# Error formatter
# ---------------------------------------------------------------------------

def test_format_validation_error_includes_pointer_and_tool() -> bool:
    v = _VALIDATORS["paper_search"]
    errs = list(v.iter_errors({"top_k": "five"}))
    msg = _format_validation_error("paper_search", errs)
    return _check(
        "format_validation_error embeds tool name + JSON pointer",
        "'paper_search'" in msg
        and "top_k" in msg
        and "integer" in msg,
        f"msg={msg!r}",
    )


def test_format_validation_error_trailing_count() -> bool:
    """When multiple validation errors fire, the formatter reports the
    first and trails with a count of suppressed issues so the model
    knows there's more to fix."""
    # Two violations: missing required `expression` AND mode out of enum.
    v = _VALIDATORS["calculate"]
    errs = sorted(v.iter_errors({"mode": "magic"}), key=lambda e: tuple(str(p) for p in e.absolute_path))
    msg = _format_validation_error("calculate", errs)
    return _check(
        "format_validation_error reports count of suppressed issues",
        len(errs) >= 2 and "more issue" in msg,
        f"n_errs={len(errs)}, msg={msg!r}",
    )


# ---------------------------------------------------------------------------
# Gate behaviour through execute_mcp_tool
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_gate_rejects_wrong_type() -> bool:
    """``top_k`` is declared integer; passing a string must fail at the
    gate, not run paper_search and crash deep in Qdrant."""
    res = _run(execute_mcp_tool("paper_search", {"top_k": "five"}))
    err = (res or {}).get("error", "")
    return _check(
        "gate rejects wrong-type top_k for paper_search",
        "invalid arguments" in err and "top_k" in err,
        f"res={res}",
    )


def test_gate_rejects_array_passed_as_string() -> bool:
    res = _run(execute_mcp_tool("compare_papers", {"dois": "10.1234/foo"}))
    err = (res or {}).get("error", "")
    return _check(
        "gate rejects string in array-typed field",
        "invalid arguments" in err and "dois" in err,
        f"res={res}",
    )


def test_gate_rejects_missing_required() -> bool:
    """``calculate`` requires `expression`. Empty args must fail at the gate."""
    res = _run(execute_mcp_tool("calculate", {}))
    err = (res or {}).get("error", "")
    return _check(
        "gate rejects missing required field",
        "invalid arguments" in err and "expression" in err,
        f"res={res}",
    )


def test_gate_rejects_enum_violation() -> bool:
    res = _run(execute_mcp_tool("calculate", {"expression": "1+1", "mode": "magic"}))
    err = (res or {}).get("error", "")
    return _check(
        "gate rejects out-of-enum value",
        "invalid arguments" in err and "mode" in err,
        f"res={res}",
    )


def test_gate_accepts_valid_args_and_dispatches() -> bool:
    """The point of the whole gate: valid args fall through to the tool.
    Use calculate — pure function, no I/O."""
    res = _run(execute_mcp_tool("calculate", {"expression": "2+2"}))
    if not isinstance(res, dict):
        return _check("gate accepts valid args + tool runs", False, f"res={res}")
    if "error" in res:
        return _check(
            "gate accepts valid args + tool runs",
            False,
            f"unexpected error: {res['error']}",
        )
    # calculator returns {"result": "4", ...} or similar; just verify
    # it produced *something* non-error.
    return _check(
        "gate accepts valid args + tool runs",
        any(k for k in res.keys() if k != "error"),
        f"res={res}",
    )


def test_gate_allows_extra_unknown_keys() -> bool:
    """Models occasionally invent harmless extra keys. The dispatcher's
    arguments.get(...) ignores them; the gate must too."""
    res = _run(execute_mcp_tool(
        "calculate",
        {"expression": "1+1", "weird_extra_key": 99},
    ))
    return _check(
        "gate accepts unknown extra keys (permissive on additionalProperties)",
        isinstance(res, dict) and "error" not in res,
        f"res={res}",
    )


def test_gate_allows_missing_optional_keys() -> bool:
    """``mode`` has a default and isn't required. Calling without it works."""
    res = _run(execute_mcp_tool("calculate", {"expression": "2*3"}))
    return _check(
        "gate accepts missing optional keys (uses dispatcher defaults)",
        isinstance(res, dict) and "error" not in res,
        f"res={res}",
    )


def test_unknown_tool_skips_validation() -> bool:
    """A tool name not in MCP_TOOLS has no validator — should reach the
    existing 'Unknown tool' branch in the dispatcher, not the gate."""
    res = _run(execute_mcp_tool("definitely_not_a_real_tool", {}))
    err = (res or {}).get("error", "")
    return _check(
        "unknown tool falls through to dispatcher's Unknown error",
        "Unknown tool" in err,
        f"res={res}",
    )


def test_gate_handles_none_arguments() -> bool:
    """Defensive: if a caller passes None instead of {} the gate must
    coerce to {} rather than crashing."""
    res = _run(execute_mcp_tool("calculate", None))  # type: ignore[arg-type]
    err = (res or {}).get("error", "")
    # Should still complain about missing required `expression`.
    return _check(
        "gate coerces None arguments to {}",
        "invalid arguments" in err and "expression" in err,
        f"res={res}",
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_every_tool_has_a_compiled_validator,
    test_validators_are_singletons,
    test_format_validation_error_includes_pointer_and_tool,
    test_format_validation_error_trailing_count,
    test_gate_rejects_wrong_type,
    test_gate_rejects_array_passed_as_string,
    test_gate_rejects_missing_required,
    test_gate_rejects_enum_violation,
    test_gate_accepts_valid_args_and_dispatches,
    test_gate_allows_extra_unknown_keys,
    test_gate_allows_missing_optional_keys,
    test_unknown_tool_skips_validation,
    test_gate_handles_none_arguments,
]


def main() -> int:
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            ok = test()
        except Exception:
            ok = False
            print(f"[FAIL] {test.__name__} - exception:")
            traceback.print_exc()
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
