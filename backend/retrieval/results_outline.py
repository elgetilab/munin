"""A one-line outline of a JSON file a run_python call wrote (LABKIT-013-PLAN.md).

In Phase 4 run 3 eight runs had the right numbers but saved them without
the labels the request asked for: R1 as a bare list of 40 rates with no
residue numbers, Gen5 rates as a list instead of by well, a NanoDrop table
nested one level too deep. The model never looked at what it had written.
`figure_summary` shows it what a figure actually contains; this does the
same for JSON: what each top-level key holds. It describes the file and
says nothing about what the file should contain.
"""
from __future__ import annotations

import json
from typing import Any

MAX_BYTES = 256 * 1024
MAX_KEYS = 25


def _kind(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true/false"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "text"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _short(v: Any) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 40 else s[:37] + "..."


def _describe(v: Any, depth: int = 0) -> str:
    k = _kind(v)
    if k in ("number", "text", "true/false", "null"):
        return f"{k} {_short(v)}" if k != "null" else "null"
    if k == "list":
        if not v:
            return "empty list"
        kinds = sorted({_kind(x) for x in v})
        if len(kinds) == 1:
            inner = kinds[0]
            plural = {"number": "numbers", "text": "texts", "list": "lists", "object": "objects",
                      "null": "nulls", "true/false": "true/false values"}.get(inner, inner)
            what = f"list of {len(v)} {plural}"
            if inner == "object" and depth < 1:
                what += f" ({_describe(v[0], depth + 1)})"
        else:
            what = f"list of {len(v)} ({', '.join(_kind(x) for x in v[:4])}{', ...' if len(v) > 4 else ''})"
        if len(v) <= 4:
            what += f" {_short(v)}"
        # A list of messages is fine as it is; numbers in a list have lost
        # whatever they belonged to (a residue, a well, a sample).
        return what if kinds == ["text"] else what + " (no labels)"
    keys = list(v)
    if not keys:
        return "empty object"
    shown = ", ".join(repr(x) for x in keys[:3]) + (", ..." if len(keys) > 3 else "")
    vk = sorted({_kind(x) for x in v.values()})
    if vk == ["object"] and depth < 1:
        sub = sorted({kk for x in v.values() for kk in x})
        tail = f"objects with keys {', '.join(sub[:5])}{', ...' if len(sub) > 5 else ''}"
    elif len(vk) == 1:
        tail = {"number": "numbers", "text": "texts", "list": "lists", "object": "objects",
                "null": "nulls"}.get(vk[0], vk[0])
    else:
        tail = " / ".join(vk)
    return f"object with {len(keys)} keys ({shown}) -> {tail}"


def outline(filename: str, data: bytes) -> str:
    """'results.json: key: what it holds; ...', or '' if not a JSON object/list."""
    if not data or len(data) > MAX_BYTES:
        return ""
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return f"{filename}: not valid JSON"
    if isinstance(obj, dict):
        parts = [f"{k}: {_describe(v)}" for k, v in list(obj.items())[:MAX_KEYS]]
        more = f"; and {len(obj) - MAX_KEYS} more keys" if len(obj) > MAX_KEYS else ""
        return f"{filename}: " + "; ".join(parts) + more
    return f"{filename}: top level is a {_describe(obj)}"
