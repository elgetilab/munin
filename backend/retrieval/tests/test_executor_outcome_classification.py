"""
Regression tests for ``execute_mcp_tool``'s outcome classification
(P1 #12 metric labels).

Pins the bug found via end-to-end testing on hugin 2026-05-26:
``run_python`` and similar tools return a result envelope with a
literal ``"error": null`` field on success (the null is meaningful
— "no exception was raised"). The original classifier used
``"error" in result`` (key presence), which misclassified every
successful run_python / compile_latex / sandbox call as
``outcome="error"``. The fix is a truthy check on
``result.get("error")``; these tests pin it.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_executor_outcome_classification.py
Or locally:
    python backend/retrieval/tests/test_executor_outcome_classification.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import executor  # noqa: E402, F401  (populates the registry)
from mcp._dispatch import register_tool, _REGISTRY  # noqa: E402
from mcp.executor import execute_mcp_tool  # noqa: E402
from metrics import mcp_tool_total  # noqa: E402


PROBE_NAME = "__probe_outcome_classifier__"
PROBE_NAME_VERR = "__probe_validation_error_path__"


def _register_probe(name: str, payload):
    """Idempotent register. The real registry rejects duplicates at
    decoration time; we work around that by removing first."""
    _REGISTRY.pop(name, None)

    @register_tool(name)
    async def _fn(arguments: dict) -> dict:
        return payload

    return _fn


def _counter_value(name: str, outcome: str) -> float:
    """Read the current value of mcp_tool_total{name, outcome}."""
    return mcp_tool_total.labels(name=name, outcome=outcome)._value.get()


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# 1. {"error": null} -> outcome="success" (the regression)
# ---------------------------------------------------------------------------

def test_null_error_field_is_success() -> bool:
    """Mirror of run_python's success envelope: stdout populated,
    error explicitly null. Must NOT bucket as outcome=error."""
    _register_probe(
        PROBE_NAME,
        {"stdout": "1267650600228229401496703205376", "error": None, "timed_out": False},
    )
    before_success = _counter_value(PROBE_NAME, "success")
    before_error = _counter_value(PROBE_NAME, "error")
    asyncio.run(execute_mcp_tool(PROBE_NAME, {}))
    after_success = _counter_value(PROBE_NAME, "success")
    after_error = _counter_value(PROBE_NAME, "error")
    return _check(
        "{'error': null} envelope buckets as outcome=success (P1 #12 fix)",
        after_success == before_success + 1 and after_error == before_error,
    )


# ---------------------------------------------------------------------------
# 2. No "error" key at all -> outcome="success"
# ---------------------------------------------------------------------------

def test_no_error_key_is_success() -> bool:
    _register_probe(PROBE_NAME, {"data": "fine"})
    before = _counter_value(PROBE_NAME, "success")
    asyncio.run(execute_mcp_tool(PROBE_NAME, {}))
    after = _counter_value(PROBE_NAME, "success")
    return _check(
        "envelope without an 'error' key buckets as outcome=success",
        after == before + 1,
    )


# ---------------------------------------------------------------------------
# 3. Truthy "error" string -> outcome="error" (generic)
# ---------------------------------------------------------------------------

def test_truthy_error_string_is_error() -> bool:
    _register_probe(PROBE_NAME, {"error": "vLLM stream dropped"})
    before = _counter_value(PROBE_NAME, "error")
    asyncio.run(execute_mcp_tool(PROBE_NAME, {}))
    after = _counter_value(PROBE_NAME, "error")
    return _check(
        "{'error': 'message'} buckets as outcome=error",
        after == before + 1,
    )


# ---------------------------------------------------------------------------
# 4. "invalid arguments..." -> outcome="validation_error"
# ---------------------------------------------------------------------------

def test_invalid_arguments_is_validation_error() -> bool:
    """The jsonschema validator in _dispatch_mcp_tool produces this
    exact prefix; the classifier must recognise it as validation."""
    _register_probe(
        PROBE_NAME_VERR,
        {"error": "invalid arguments for 'foo' at /query: required key not found"},
    )
    before = _counter_value(PROBE_NAME_VERR, "validation_error")
    asyncio.run(execute_mcp_tool(PROBE_NAME_VERR, {}))
    after = _counter_value(PROBE_NAME_VERR, "validation_error")
    return _check(
        "error starting with 'invalid arguments' buckets as validation_error",
        after == before + 1,
    )


# ---------------------------------------------------------------------------
# 5. Unknown tool -> outcome="unknown_tool"
# ---------------------------------------------------------------------------

def test_unknown_tool_buckets_correctly() -> bool:
    """No dispatcher registered → _dispatch_mcp_tool returns
    {'error': 'Unknown tool: ...'}; classifier must bucket it."""
    unknown = "__definitely_not_registered__"
    _REGISTRY.pop(unknown, None)  # ensure absent
    before = _counter_value(unknown, "unknown_tool")
    asyncio.run(execute_mcp_tool(unknown, {}))
    after = _counter_value(unknown, "unknown_tool")
    return _check(
        "missing dispatcher buckets as outcome=unknown_tool",
        after == before + 1,
    )


# ---------------------------------------------------------------------------
# 6. Exception in dispatcher -> outcome="error"
# ---------------------------------------------------------------------------

def test_dispatcher_exception_is_error() -> bool:
    name = "__probe_raising_dispatcher__"
    _REGISTRY.pop(name, None)

    @register_tool(name)
    async def _boom(arguments: dict) -> dict:
        raise RuntimeError("simulated tool crash")

    before = _counter_value(name, "error")
    asyncio.run(execute_mcp_tool(name, {}))
    after = _counter_value(name, "error")
    return _check(
        "dispatcher raising buckets as outcome=error",
        after == before + 1,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_null_error_field_is_success,
    test_no_error_key_is_success,
    test_truthy_error_string_is_error,
    test_invalid_arguments_is_validation_error,
    test_unknown_tool_buckets_correctly,
    test_dispatcher_exception_is_error,
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
    # Clean up probes so a re-run doesn't bloat the registry on dev
    # machines (verify_dispatch_registry would complain about
    # __probe_* not being in MCP_TOOLS).
    for k in list(_REGISTRY.keys()):
        if k.startswith("__probe_"):
            del _REGISTRY[k]
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
