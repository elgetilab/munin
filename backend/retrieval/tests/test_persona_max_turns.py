"""
Standalone tests for personas.max_turns (P1 #16).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_persona_max_turns.py
Or locally:
    python backend/retrieval/tests/test_persona_max_turns.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from personas import max_turns  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _persona(value) -> dict:
    """Synthetic persona with the given params.max_turns (omitted when
    value is the sentinel _OMIT)."""
    params: dict = {}
    if value is not _OMIT:
        params["max_turns"] = value
    return {"id": "test", "params": params}


_OMIT = object()


def test_default_when_absent() -> bool:
    return _check(
        "absent max_turns defaults to 10",
        max_turns(_persona(_OMIT)) == 10,
    )


def test_explicit_value_honoured() -> bool:
    return _check(
        "explicit in-range max_turns is honoured",
        max_turns(_persona(16)) == 16,
    )


def test_clamped_high() -> bool:
    return _check(
        "max_turns above 30 clamps to 30",
        max_turns(_persona(1000)) == 30,
    )


def test_clamped_low() -> bool:
    return _check(
        "max_turns of 0 / negative clamps to 1",
        max_turns(_persona(0)) == 1 and max_turns(_persona(-5)) == 1,
    )


def test_non_int_falls_back() -> bool:
    ok = (
        max_turns(_persona("20")) == 10
        and max_turns(_persona(12.5)) == 10
        and max_turns(_persona(None)) == 10
        and max_turns(_persona(True)) == 10  # bool is not a real int here
    )
    return _check("non-int max_turns falls back to the default", ok)


def test_non_dict_persona() -> bool:
    return _check(
        "non-dict / None persona returns the default",
        max_turns(None) == 10 and max_turns("nonsense") == 10,
    )


def test_research_persona_json() -> bool:
    """The shipped research.json sets max_turns: 24 (raised from 16 to give
    deep multi-call exploration more room before the auto-continue / Continue
    fallback kicks in — rpt_20260702)."""
    import json

    path = Path(__file__).resolve().parents[3] / "shared/personas/research.json"
    if not path.exists():
        print("[SKIP] research.json max_turns - file not found at "
              f"{path}")
        return True
    persona = json.load(open(path))
    return _check(
        "research.json ships max_turns=24",
        max_turns(persona) == 24,
        f"got {max_turns(persona)}",
    )


TESTS = [
    test_default_when_absent,
    test_explicit_value_honoured,
    test_clamped_high,
    test_clamped_low,
    test_non_int_falls_back,
    test_non_dict_persona,
    test_research_persona_json,
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
