"""
Tests for the Pydantic schemas guarding agents.yml + persona JSONs (P2 #20).

Pins the two properties the audit was about:
  - extra=forbid catches typos like `max_iteratons: 8` and
    `params.temprature: 1.0` at load time
  - failure policy is warn-and-skip per entry, not fail-startup

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_config_schemas.py
Or locally:
    python backend/retrieval/tests/test_config_schemas.py
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from agents.registry import AgentEntry, _normalize  # noqa: E402
from personas import _Persona  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# AgentEntry schema
# ---------------------------------------------------------------------------

def test_agent_happy_path() -> bool:
    """A complete, valid entry parses and exposes the normalised fields."""
    raw = {
        "name": "writing_agent",
        "description": "  helps write ",
        "system": " You are a writing agent. ",
        "tools": ["web_search"],
        "max_iterations": 6,
        "max_tool_calls": 15,
        "timeout_seconds": 180,
    }
    validated = AgentEntry.model_validate(raw)
    return _check(
        "agent: happy path validates and strips description/system",
        validated.name == "writing_agent"
        and validated.description == "helps write"
        and validated.system == "You are a writing agent."
        and validated.tools == ["web_search"]
        and validated.max_iterations == 6,
    )


def test_agent_defaults_applied() -> bool:
    """Numeric defaults kick in when fields are omitted."""
    raw = {"name": "x", "system": "y"}
    v = AgentEntry.model_validate(raw)
    return _check(
        "agent: defaults applied for max_iterations/max_tool_calls/timeout",
        v.max_iterations == 8 and v.max_tool_calls == 20 and v.timeout_seconds == 300,
    )


def test_agent_typo_rejected() -> bool:
    """A typo like `max_iteratons: 8` (audit's example) raises ValidationError
    because extra=forbid; _normalize wraps and skips, returning None."""
    raw = {
        "name": "writing_agent",
        "system": "prompt",
        "max_iteratons": 8,  # typo, not a real field
    }
    try:
        AgentEntry.model_validate(raw)
        direct = False  # should have raised
    except ValidationError as e:
        direct = "max_iteratons" in str(e) and "extra" in str(e).lower()
    skipped = _normalize(raw) is None
    return _check(
        "agent: extra field rejected by schema and skipped by _normalize",
        direct and skipped,
    )


def test_agent_missing_required_skipped() -> bool:
    """`system: ""` (or omitted) fails min_length=1; _normalize returns None."""
    raw = {"name": "needs_system", "tools": []}
    result = _normalize(raw)
    raw_empty = {"name": "needs_system", "system": "", "tools": []}
    result_empty = _normalize(raw_empty)
    return _check(
        "agent: missing/empty system prompt is skipped",
        result is None and result_empty is None,
    )


def test_agent_out_of_bounds_rejected() -> bool:
    """max_iterations: 0 fails ge=1; _normalize logs and returns None."""
    raw = {"name": "tight", "system": "p", "max_iterations": 0}
    return _check(
        "agent: max_iterations=0 rejected (out of bounds)",
        _normalize(raw) is None,
    )


def test_agent_unknown_tool_stripped_not_rejected() -> bool:
    """Tools list contains a name not in MCP_TOOLS: schema accepts it,
    _normalize logs a warning and strips it from the allowlist."""
    raw = {
        "name": "x",
        "system": "p",
        "tools": ["web_search", "bogus_tool_does_not_exist", "invoke_agent"],
    }
    out = _normalize(raw)
    if out is None:
        return _check("agent: unknown tools stripped, entry kept", False)
    return _check(
        "agent: unknown tools and invoke_agent stripped from allowlist",
        out is not None
        and "bogus_tool_does_not_exist" not in out["tools"]
        and "invoke_agent" not in out["tools"]
        and "web_search" in out["tools"],
    )


# ---------------------------------------------------------------------------
# Persona schema
# ---------------------------------------------------------------------------

def _shipped_persona(name: str) -> dict:
    """Load a real persona JSON shipped in shared/personas/ for round-trip
    regression. Path resolves whether the tests run inside the container
    (/app/personas) or locally (../../shared/personas)."""
    here = Path(__file__).resolve()
    candidates = [
        Path("/app/personas") / f"{name}.json",
        here.parent.parent.parent.parent / "shared" / "personas" / f"{name}.json",
    ]
    for path in candidates:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    raise FileNotFoundError(f"Could not locate persona JSON for {name!r}")


def test_persona_happy_path_real_file() -> bool:
    """The shipped chat.json validates end-to-end. Regression gate: any
    field rename / new field in a shipped persona must update the schema."""
    raw = _shipped_persona("chat")
    try:
        _Persona.model_validate(raw)
        ok = True
        detail = ""
    except ValidationError as e:
        ok = False
        detail = str(e)[:200]
    return _check("persona: shipped chat.json validates", ok, detail)


def test_persona_typo_in_params_rejected() -> bool:
    """`params.temprature` is the audit's textbook failure mode. extra=forbid
    on Params must surface it."""
    raw = {
        "id": "test",
        "name": "test",
        "description": "",
        "params": {
            "system": "p",
            "temprature": 1.0,  # typo
        },
    }
    try:
        _Persona.model_validate(raw)
        return _check("persona: typo in params rejected", False, "no error raised")
    except ValidationError as e:
        return _check(
            "persona: typo in params rejected",
            "temprature" in str(e) and "extra" in str(e).lower(),
        )


def test_persona_malformed_prompt_suggestion_rejected() -> bool:
    """Missing `title` on a prompt suggestion is a schema violation."""
    raw = {
        "id": "test",
        "prompt_suggestions": [
            {"subtitle": "x", "content": "y"},  # no title
        ],
    }
    try:
        _Persona.model_validate(raw)
        return _check(
            "persona: malformed prompt_suggestion rejected", False, "no error",
        )
    except ValidationError as e:
        return _check(
            "persona: malformed prompt_suggestion rejected",
            "title" in str(e),
        )


def test_persona_out_of_bounds_temperature_rejected() -> bool:
    """temperature: 5.0 is out of the [0, 2] range. Reject, don't clamp."""
    raw = {
        "id": "test",
        "params": {"temperature": 5.0},
    }
    try:
        _Persona.model_validate(raw)
        return _check(
            "persona: temperature=5.0 rejected (out of bounds)",
            False, "no error",
        )
    except ValidationError:
        return _check(
            "persona: temperature=5.0 rejected (out of bounds)", True,
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_agent_happy_path,
    test_agent_defaults_applied,
    test_agent_typo_rejected,
    test_agent_missing_required_skipped,
    test_agent_out_of_bounds_rejected,
    test_agent_unknown_tool_stripped_not_rejected,
    test_persona_happy_path_real_file,
    test_persona_typo_in_params_rejected,
    test_persona_malformed_prompt_suggestion_rejected,
    test_persona_out_of_bounds_temperature_rejected,
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
