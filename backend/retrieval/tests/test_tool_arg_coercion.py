"""
Unit tests for schema-driven argument coercion in mcp/executor.py.

WHY THIS EXISTS. T11 telemetry over the same 199 LitQA2 questions put the
`search` tool at a 0.000 error rate on Qwen3.6-35B-A3B and 0.122 on
Qwen3.8-27B, with NO code change between the two runs. Reproducing the failures
showed all of them were argument TYPES, not logic:

    top_k="5"            -> validator rejects, tool call wasted
    filters="year:2023"  -> validator rejects, tool call wasted
    top_k=5.0            -> validator ACCEPTS (jsonschema treats an integral
                            float as "integer"), then the call dies deeper in
                            with "slice indices must be integers", which
                            reaches the model as an opaque
                            "Tool execution failed"

The third is the dangerous one and is the specific regression these tests pin.
Model-emitted argument types are a moving target, so coercion is generic and
schema-driven rather than per tool.

The other half of the contract is what must NOT be coerced: anything lossy or
ambiguous stays untouched so the validator can reject it with its clear
message, and anything already well-typed is returned unchanged (identity, not
a rebuilt copy).

Runs in-process, no network:

    docker exec munin-retrieval python /app/tests/test_tool_arg_coercion.py
"""

from __future__ import annotations

import sys
import traceback

sys.path.insert(0, "/app")
sys.path.insert(0, ".")

from mcp.executor import coerce_argument_types as coerce  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(("[PASS] " if ok else "[FAIL] ") + name + (f"  {detail}" if detail and not ok else ""))
    return ok


# --- the three measured failures -------------------------------------------

def test_int_as_string_is_coerced() -> bool:
    out = coerce("search", {"query": "x", "top_k": "5"})
    return _check("top_k='5' -> 5", out["top_k"] == 5 and isinstance(out["top_k"], int),
                  f"got {out['top_k']!r}")


def test_integral_float_is_coerced() -> bool:
    """The one that passed validation and then crashed on a list slice."""
    out = coerce("search", {"query": "x", "top_k": 5.0})
    return _check("top_k=5.0 -> 5 (int)",
                  out["top_k"] == 5 and isinstance(out["top_k"], int),
                  f"got {out['top_k']!r} ({type(out['top_k']).__name__})")


def test_json_object_string_is_coerced() -> bool:
    out = coerce("search", {"query": "x", "filters": '{"year":"2023"}'})
    return _check("filters='{\"year\":\"2023\"}' -> dict",
                  out["filters"] == {"year": "2023"}, f"got {out['filters']!r}")


# --- what must NOT be coerced ----------------------------------------------

def test_non_json_string_left_for_validator() -> bool:
    """'year:2023' is not JSON. Leave it so the validator says so clearly,
    rather than inventing a parse the model did not intend."""
    args = {"query": "x", "filters": "year:2023"}
    out = coerce("search", args)
    return _check("filters='year:2023' left untouched", out["filters"] == "year:2023")


def test_lossy_float_left_alone() -> bool:
    """5.5 is not an integer. Coercing it would silently change the request."""
    out = coerce("search", {"query": "x", "top_k": 5.5})
    return _check("top_k=5.5 left untouched", out["top_k"] == 5.5)


def test_bool_is_never_widened_to_int() -> bool:
    """bool is an int subclass in Python; top_k=True must not become 1."""
    out = coerce("search", {"query": "x", "top_k": True})
    return _check("top_k=True left untouched", out["top_k"] is True)


def test_unparseable_string_left_alone() -> bool:
    out = coerce("search", {"query": "x", "top_k": "abc"})
    return _check("top_k='abc' left untouched", out["top_k"] == "abc")


# --- neutrality -------------------------------------------------------------

def test_wellformed_args_returned_identically() -> bool:
    """No coercion needed -> the SAME object back, so the hot path allocates
    nothing and behaviour is provably unchanged for correct calls."""
    args = {"query": "x", "top_k": 5, "filters": {"year": "2023"}}
    out = coerce("search", args)
    return _check("well-formed args returned unchanged (identity)", out is args)


def test_unknown_tool_is_passthrough() -> bool:
    args = {"anything": "goes"}
    out = coerce("no_such_tool_xyz", args)
    return _check("unknown tool -> passthrough", out is args)


def test_unknown_property_is_passthrough() -> bool:
    """A key the schema does not declare is left for the validator."""
    out = coerce("search", {"query": "x", "not_a_real_param": "7"})
    return _check("undeclared property untouched", out["not_a_real_param"] == "7")


def test_empty_and_none_are_safe() -> bool:
    ok = coerce("search", {}) == {} and coerce("search", None) is None
    return _check("empty/None arguments are safe", ok)


def test_none_value_not_coerced() -> bool:
    """An explicit null must stay null, not become 0 or ''. """
    out = coerce("search", {"query": "x", "top_k": None})
    return _check("top_k=None left as None", out["top_k"] is None)


TESTS = [
    test_int_as_string_is_coerced,
    test_integral_float_is_coerced,
    test_json_object_string_is_coerced,
    test_non_json_string_left_for_validator,
    test_lossy_float_left_alone,
    test_bool_is_never_widened_to_int,
    test_unparseable_string_left_alone,
    test_wellformed_args_returned_identically,
    test_unknown_tool_is_passthrough,
    test_unknown_property_is_passthrough,
    test_empty_and_none_are_safe,
    test_none_value_not_coerced,
]


def main() -> int:
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
