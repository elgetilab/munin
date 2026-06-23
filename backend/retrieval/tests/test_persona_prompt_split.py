"""Tests for the A3 layered-prompt split (personas.split/compose_system_prompt).

The router composes `prefix(pin) + fragment(routed) + suffix(pin)`. The
safety guarantee that makes the refactor low-risk: when pin == routed the
composition reconstructs the ORIGINAL persona prompt exactly, so ordinary
(non-re-routed) turns are provably unchanged.

Run standalone or under pytest:
    python backend/retrieval/tests/test_persona_prompt_split.py
    pytest backend/retrieval/tests/test_persona_prompt_split.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Point at the repo's shared personas (the real chat/code/research prompts).
_SHARED = Path(__file__).resolve().parents[3] / "shared" / "personas"
if _SHARED.is_dir():
    os.environ["PERSONAS_DIR"] = str(_SHARED)

import personas  # noqa: E402

_REAL = ["chat", "research", "code"]


def _load():
    personas.load_personas()


def test_split_is_lossless():
    """prefix + fragment + suffix == the original system prompt."""
    _load()
    for pid in _REAL:
        p = personas.get_persona(pid)
        assert p is not None, f"{pid} not loaded"
        prefix, fragment, suffix = personas.split_system_prompt(p)
        assert prefix + fragment + suffix == personas.build_system_prompt(p), pid
        # markers actually split something out (fragment non-trivial)
        assert len(fragment) > 100, f"{pid} fragment suspiciously small"
        assert prefix and suffix, f"{pid} missing prefix/suffix (markers?)"


def test_identity_compose_pin_equals_routed():
    """compose(p, p) == original[p] — the behavior-preserving guarantee."""
    _load()
    for pid in _REAL:
        p = personas.get_persona(pid)
        composed = personas.compose_system_prompt(p, p)
        assert composed == personas.build_system_prompt(p), (
            f"{pid}: compose(p,p) != original"
        )


def test_cross_composition_swaps_only_the_fragment():
    """compose(research_pin, code_routed) = research prefix + code fragment +
    research suffix. Voice/rules from the pin, task guidance from the routed
    profile, and the pin's own fragment absent (no search-vs-plot conflict)."""
    _load()
    research = personas.get_persona("research")
    code = personas.get_persona("code")
    r_prefix, r_fragment, r_suffix = personas.split_system_prompt(research)
    _c_prefix, c_fragment, _c_suffix = personas.split_system_prompt(code)

    composed = personas.compose_system_prompt(research, code)
    assert composed == r_prefix + c_fragment + r_suffix
    # the code task fragment is present; research's own methodology fragment is not
    assert c_fragment in composed
    assert r_fragment not in composed
    # research voice/frame survives (prefix + suffix retained)
    assert r_prefix in composed and r_suffix in composed


def test_compose_fallback_when_pin_unsplittable():
    """If the pin lacks the markers, compose returns the routed persona's full
    prompt (graceful degradation, never a bare fragment)."""
    _load()
    code = personas.get_persona("code")
    unsplittable = {"id": "x", "params": {"system": "no markers here at all."}}
    composed = personas.compose_system_prompt(unsplittable, code)
    assert composed == personas.build_system_prompt(code)


def _main() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
