"""
MCP tool: calculate.

Three modes for any kind of math the user (or model) might want to do
without burning a sandbox kernel:

    numeric  — arithmetic, trig, logs, powers, percentages, arbitrary precision
    symbolic — sympy: derivatives, integrals, equation solving, limits, series
    physical — pint: unit-aware arithmetic with physical constants, conversions

Why this exists: LLMs make silent math errors. A dedicated tool with safe
evaluation gives the model a deterministic answer in ~10 ms instead of
fabricating one. The tool also unlocks "convert 8.6 MJ to kcal" and
"derivative of sin(x)**2" without needing the heavier sandbox to be online.

Safety model:
    * No `eval` of raw user strings. We use sympy.parse_expr with an
      explicit local_dict of allowed names — no Python builtins reachable.
    * Hard ban on dunder access (`__import__`, `__class__`, etc.) at the
      string level before parsing.
    * Hard ban on common dangerous tokens (`import`, `exec`, `open`, etc.).
    * 1-second wall-clock timeout via asyncio.wait_for + asyncio.to_thread.
      Pathological inputs (e.g. `integrate(exp(exp(exp(x))), x)`) cannot
      hang the calling tool loop.
    * Pint's parser is strict by design and can't execute arbitrary code.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import sympy
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
)


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

_TIMEOUT_S = 1.0

# Sympy names we intentionally expose to user expressions. Anything not in
# this dict will be auto-coerced to a free Symbol by sympy (which is what we
# want for "x" in "diff(x**2, x)") OR rejected as a NameError by the parser.
_SYMPY_NAMESPACE: dict[str, Any] = {
    # Constants
    "pi": sympy.pi,
    "E": sympy.E,
    "I": sympy.I,
    "oo": sympy.oo,
    "infinity": sympy.oo,
    "nan": sympy.nan,
    # Basic functions
    "sqrt": sympy.sqrt,
    "abs": sympy.Abs,
    "Abs": sympy.Abs,
    "log": sympy.log,
    "ln": sympy.log,
    "exp": sympy.exp,
    "floor": sympy.floor,
    "ceiling": sympy.ceiling,
    "ceil": sympy.ceiling,
    "factorial": sympy.factorial,
    "sign": sympy.sign,
    "Min": sympy.Min,
    "Max": sympy.Max,
    "min": sympy.Min,
    "max": sympy.Max,
    # Trig
    "sin": sympy.sin,
    "cos": sympy.cos,
    "tan": sympy.tan,
    "asin": sympy.asin,
    "acos": sympy.acos,
    "atan": sympy.atan,
    "atan2": sympy.atan2,
    "sinh": sympy.sinh,
    "cosh": sympy.cosh,
    "tanh": sympy.tanh,
    # Symbolic operations
    "diff": sympy.diff,
    "integrate": sympy.integrate,
    "solve": sympy.solve,
    "limit": sympy.limit,
    "series": sympy.series,
    "simplify": sympy.simplify,
    "expand": sympy.expand,
    "factor": sympy.factor,
    "trigsimp": sympy.trigsimp,
    "nsimplify": sympy.nsimplify,
    "Symbol": sympy.Symbol,
    "Eq": sympy.Eq,
    "Sum": sympy.Sum,
    "Product": sympy.Product,
    "Matrix": sympy.Matrix,
}

_TRANSFORMATIONS = standard_transformations + (implicit_multiplication_application,)

# Forbidden substrings — checked case-insensitively before parsing.
_FORBIDDEN_TOKENS = (
    "__",          # dunder access
    "import",
    "exec(",
    "eval(",
    "compile(",
    "open(",
    "globals(",
    "locals(",
    "getattr(",
    "setattr(",
    "delattr(",
    "vars(",
    "dir(",
    "lambdify",    # sympy.lambdify can compile arbitrary Python
    "subprocess",
    "os.",
    "sys.",
    "pathlib",
)


# -----------------------------------------------------------------------------
# Pint registry (lazy)
# -----------------------------------------------------------------------------

_pint_registry = None


def _get_pint_registry():
    global _pint_registry
    if _pint_registry is None:
        import pint
        _pint_registry = pint.UnitRegistry(autoconvert_offset_to_baseunit=True)
    return _pint_registry


# -----------------------------------------------------------------------------
# Safety check
# -----------------------------------------------------------------------------

def _validate(expression: str) -> None:
    """Raise ValueError for any obviously-unsafe expression."""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("expression must be a non-empty string")
    if len(expression) > 2000:
        raise ValueError("expression too long (max 2000 chars)")
    lowered = expression.lower()
    for tok in _FORBIDDEN_TOKENS:
        if tok in lowered:
            raise ValueError(f"forbidden token: {tok!r}")


# -----------------------------------------------------------------------------
# Numeric mode (sympy parse + numeric eval)
# -----------------------------------------------------------------------------

# Match "<num>% of <expr>" — common natural language for percentages.
_PERCENT_OF = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(.+)",
    re.IGNORECASE,
)


def _normalize_numeric(expression: str) -> str:
    """
    Light preprocessing for natural-language patterns the model often uses:
    "17% of 450" → "(17/100)*(450)". Other normalizations could go here.
    """
    expression = expression.strip()
    m = _PERCENT_OF.fullmatch(expression)
    if m:
        return f"({m.group(1)}/100)*({m.group(2).strip()})"
    return expression


def _calc_numeric(expression: str) -> dict:
    expression = _normalize_numeric(expression)
    expr = parse_expr(
        expression,
        local_dict=dict(_SYMPY_NAMESPACE),
        transformations=_TRANSFORMATIONS,
        evaluate=True,
    )

    out: dict = {
        "mode": "numeric",
        "expression": expression,
    }

    # Handle arbitrary-precision integers FIRST — checking is_Integer on the
    # original expression preserves the full digit count. Calling evalf()
    # first would convert Integer(2**1024) to a Float which then overflows
    # float64 and serializes as JSON-incompatible inf.
    if expr.is_Integer:
        as_int = int(expr)
        out["result"] = as_int
        out["result_str"] = str(as_int)
        return out

    # Otherwise it's some other Number — try to coerce to a Python float,
    # falling back to a string for inf / NaN / overflow / non-numeric.
    if expr.is_Number:
        try:
            numeric = expr.evalf()
            as_float = float(numeric)
            if as_float != as_float or as_float in (float("inf"), float("-inf")):
                # NaN or infinity → string-only
                out["result"] = str(numeric)
                out["result_str"] = str(numeric)
            else:
                out["result"] = as_float
                out["result_str"] = str(numeric)
        except (OverflowError, ValueError, TypeError):
            out["result"] = str(expr)
            out["result_str"] = str(expr)
        return out

    # Symbolic / non-numeric — return the simplified form as a string.
    out["result"] = str(expr)
    out["result_str"] = str(expr)
    return out


# -----------------------------------------------------------------------------
# Symbolic mode (sympy)
# -----------------------------------------------------------------------------

def _calc_symbolic(expression: str) -> dict:
    expr = parse_expr(
        expression,
        local_dict=dict(_SYMPY_NAMESPACE),
        transformations=_TRANSFORMATIONS,
        evaluate=True,
    )
    out: dict = {
        "mode": "symbolic",
        "expression": expression,
        "result": str(expr),
    }
    # If the result is also numerically evaluable, include a decimal form
    try:
        numeric = expr.evalf()
        if numeric != expr and numeric.is_Number:
            out["result_numeric"] = str(numeric)
    except Exception:
        pass
    return out


# -----------------------------------------------------------------------------
# Physical mode (pint)
# -----------------------------------------------------------------------------

def _calc_physical(expression: str) -> dict:
    ureg = _get_pint_registry()
    expression = expression.strip()

    # "X to Y" syntax for unit conversions
    if " to " in expression.lower():
        # Case-insensitive split on " to "
        idx = expression.lower().index(" to ")
        lhs = expression[:idx].strip()
        rhs = expression[idx + 4:].strip()
        quantity = ureg.parse_expression(lhs)
        result = quantity.to(rhs)
    else:
        result = ureg.parse_expression(expression)

    # If pint returned a Quantity, format it; if it returned a scalar
    # (constant lookup with no units), wrap it as a string.
    try:
        magnitude = float(result.magnitude)
        units = str(result.units)
    except (AttributeError, TypeError):
        magnitude = None
        units = None

    return {
        "mode": "physical",
        "expression": expression,
        "result": str(result),
        "magnitude": magnitude,
        "units": units,
    }


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

_DISPATCH = {
    "numeric": _calc_numeric,
    "symbolic": _calc_symbolic,
    "physical": _calc_physical,
}


def _do_calculate(expression: str, mode: str) -> dict:
    _validate(expression)
    fn = _DISPATCH.get(mode)
    if fn is None:
        raise ValueError(
            f"unknown mode {mode!r}. choose from: numeric, symbolic, physical"
        )
    return fn(expression)


async def calculate(expression: str, mode: str = "numeric") -> dict:
    """
    Evaluate a mathematical expression in one of three modes.

    Args:
        expression: The expression to evaluate. See per-mode notes.
        mode: "numeric" | "symbolic" | "physical". Default: numeric.

    Returns:
        Dict with mode, expression, result, and mode-specific extras.
        On error: {"error": "...", "mode": ..., "expression": ...}
    """
    try:
        # Run the (CPU-bound, sync) calculation in a worker thread with a
        # wall-clock timeout. The thread will keep running after the timeout
        # but the caller gets an error and the loop is unblocked. For our
        # use case that's acceptable — the worst case is a stranded sympy
        # background thread that eventually errors or finishes.
        return await asyncio.wait_for(
            asyncio.to_thread(_do_calculate, expression, mode),
            timeout=_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        return {
            "error": (
                f"calculation exceeded {_TIMEOUT_S}s timeout — try a "
                "simpler expression or pre-simplify before calling"
            ),
            "mode": mode,
            "expression": expression,
        }
    except ValueError as e:
        return {
            "error": str(e),
            "mode": mode,
            "expression": expression,
        }
    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {e}",
            "mode": mode,
            "expression": expression,
        }
