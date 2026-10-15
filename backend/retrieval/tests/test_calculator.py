"""
Unit tests for the `calculate` MCP tool (mcp/tools/calculator.py).

The bug: numeric mode assumed parse_expr always returns a sympy Expr and
called `.is_Integer` on it. Input like "3, 4" parses to a tuple, "[1, 2]"
and solve(...) to a list, "None" to None, and some inputs to plain Python
numbers, so the tool returned AttributeError "'tuple' object has no
attribute 'is_Integer'" instead of a result or a usable error.

Pure function: no DB, no network. Needs sympy and pint, so run it inside
the retrieval image:
    docker run --rm --network none -v "$PWD/backend/retrieval":/app \
        munin-retrieval:latest python /app/tests/test_calculator.py
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.tools import calculator  # noqa: E402
from mcp.tools.calculator import calculate  # noqa: E402


def _run(coro) -> dict:
    return asyncio.run(coro)


def _check(name: str, ok: bool, detail: str = "") -> None:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    assert ok, f"{name}: {detail}"


# --- scalars ---------------------------------------------------------------
def test_integer_scalar() -> None:
    res = _run(calculate("2 + 3"))
    _check("2 + 3 -> int 5", res.get("result") == 5 and res.get("result_str") == "5",
           repr(res))


def test_big_integer_keeps_precision() -> None:
    res = _run(calculate("2**1024"))
    _check("2**1024 stays an exact int", res.get("result") == 2 ** 1024, repr(res))


def test_float_scalar() -> None:
    res = _run(calculate("1/4"))
    _check("1/4 -> 0.25", res.get("result") == 0.25, repr(res))


def test_percent_of() -> None:
    res = _run(calculate("17% of 450"))
    _check("17% of 450 -> 76.5", res.get("result") == 76.5, repr(res))


def test_symbolic_mode() -> None:
    res = _run(calculate("diff(x**2, x)", mode="symbolic"))
    _check("diff(x**2, x) -> 2*x", res.get("result") == "2*x", repr(res))


# --- units -----------------------------------------------------------------
def test_physical_conversion() -> None:
    res = _run(calculate("1 km to m", mode="physical"))
    _check("1 km to m -> 1000 meter",
           res.get("magnitude") == 1000.0 and res.get("units") == "meter",
           repr(res))


# --- non-scalar parse results (the bug) -------------------------------------
def test_tuple_input() -> None:
    res = _run(calculate("3, 4"))
    _check("'3, 4' -> list result [3, 4]",
           "error" not in res and res.get("result") == [3, 4]
           and res.get("result_str") == "[3, 4]",
           repr(res))


def test_tuple_of_mixed_values() -> None:
    res = _run(calculate("1/4, 2**10, sqrt(2)"))
    ok = ("error" not in res
          and isinstance(res.get("result"), list)
          and res["result"][0] == 0.25
          and res["result"][1] == 1024
          and isinstance(res["result"][2], str))
    _check("mixed tuple -> per-element results", ok, repr(res))


def test_list_input() -> None:
    res = _run(calculate("[1, 2]"))
    _check("'[1, 2]' -> list result", "error" not in res and res.get("result") == [1, 2],
           repr(res))


def test_solve_returns_list() -> None:
    res = _run(calculate("solve(x**2 - 4, x)"))
    _check("solve(...) in numeric mode -> [-2, 2]",
           "error" not in res and res.get("result") == [-2, 2], repr(res))


def test_bare_python_int() -> None:
    # Some parse paths hand back a plain Python number rather than a sympy
    # Number; exercise the shaping helper directly with one.
    out = calculator._shape_numeric(7)
    _check("bare Python int 7 -> 7", out == (7, "7"), repr(out))


def test_none_is_clear_error() -> None:
    res = _run(calculate("None"))
    err = res.get("error") or ""
    _check("'None' -> clear error naming the expected input",
           "AttributeError" not in err and "expression" in err.lower(),
           repr(res))


def test_empty_expression_error() -> None:
    res = _run(calculate("   "))
    _check("blank expression -> error", "non-empty" in (res.get("error") or ""),
           repr(res))


def main() -> int:
    tests = [
        test_integer_scalar,
        test_big_integer_keeps_precision,
        test_float_scalar,
        test_percent_of,
        test_symbolic_mode,
        test_physical_conversion,
        test_tuple_input,
        test_tuple_of_mixed_values,
        test_list_input,
        test_solve_returns_list,
        test_bare_python_int,
        test_none_is_clear_error,
        test_empty_expression_error,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError:
            failed += 1
        except Exception:
            traceback.print_exc()
            print(f"[FAIL] {t.__name__} raised")
            failed += 1
    print(f"\n{len(tests) - failed}/{len(tests)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
