"""Drift guard: the three Munin routing-profile files share ONE identical frame.

After consolidating Meitner/Turing/Curie into a single Munin identity
(docs/paper-track/done/PERSONA-CONSOLIDATION-PLAN.md), chat/code/research.json carry the SAME
frame (prefix = identity + CORE RULES + OUTPUT STYLE; suffix = TASK PLANNING +
DISCOVERING TOOLS) and differ ONLY in the per-profile fragment, sampling, and
resident_tools. We keep three hand-maintained files (not one), so this test is
the safeguard: edit the frame in one and forget the others -> CI fails.

Run standalone or under pytest:
    python backend/retrieval/tests/test_munin_frame_shared.py
    pytest backend/retrieval/tests/test_munin_frame_shared.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# `parents[3]` raised IndexError from /app/tests, where parents is exactly
# ['/app/tests', '/app', '/'], and it did so BEFORE the is_dir() guard below
# could take effect. Same defect as KNOWN-BUGS 7; see tests/README.md.
# The deployed container mounts the personas at /app/personas, so prefer the
# repo copy (a developer editing JSON sees it immediately) and fall back to
# the deployed one rather than skipping.
_parents = Path(__file__).resolve().parents
_CANDIDATES = []
if len(_parents) > 3:
    _CANDIDATES.append(_parents[3] / "shared" / "personas")
_CANDIDATES.append(Path(os.getenv("PERSONAS_DIR") or "/app/personas"))

_SHARED = next((d for d in _CANDIDATES if d.is_dir()), None)
if _SHARED is None:
    print("[SKIP] test_munin_frame_shared - no persona dir at any of: "
          + ", ".join(str(d) for d in _CANDIDATES))
    raise SystemExit(0)
os.environ["PERSONAS_DIR"] = str(_SHARED)

import personas  # noqa: E402

_PROFILES = ("chat", "code", "research")


def _load(pid: str) -> dict:
    return json.loads((_SHARED / f"{pid}.json").read_text())


def test_all_three_are_named_munin():
    for pid in _PROFILES:
        assert _load(pid)["name"] == "Munin", f"{pid}.json name must be 'Munin'"


def test_internal_ids_unchanged():
    # the router, router_examples.json, and the slash parser key off these.
    for pid in _PROFILES:
        assert _load(pid)["id"] == pid


def test_frames_byte_identical():
    frames = {}
    for pid in _PROFILES:
        pre, _frag, suf = personas.split_system_prompt(_load(pid))
        assert pre and suf, f"{pid}.json: empty frame (split markers missing?)"
        frames[pid] = (pre, suf)
    assert frames["chat"] == frames["code"] == frames["research"], (
        "Munin frame has DRIFTED across the three profile files. Edit the frame "
        "(prefix/suffix) identically in chat.json, code.json, AND research.json."
    )


def test_fragments_differ():
    # the whole point: same frame, DIFFERENT task fragment per profile.
    frags = {pid: personas.split_system_prompt(_load(pid))[1] for pid in _PROFILES}
    assert len({frags["chat"], frags["code"], frags["research"]}) == 3


def test_public_personas_is_single_munin():
    personas.load_personas()
    pp = personas.public_personas()
    assert pp["default_persona"] == personas.AUTO_PERSONA_ID == "munin"
    assert [p["id"] for p in pp["personas"]] == ["munin"]
    assert pp["personas"][0]["name"] == "Munin"
    assert pp["personas"][0]["prompt_suggestions"]  # merged, non-empty
    # internal profiles still resolve by id; the user-facing id does NOT load
    # (the chat path maps it to no-pin / auto-route).
    assert personas.get_persona("research") is not None
    assert personas.get_persona("munin") is None


def test_no_legacy_persona_names_remain():
    blob = " ".join((_SHARED / f"{pid}.json").read_text() for pid in _PROFILES)
    for legacy in ("Meitner", "Turing", "Curie"):
        assert legacy not in blob, f"legacy persona name {legacy!r} still present"


if __name__ == "__main__":
    import types
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and isinstance(fn, types.FunctionType):
            fn()
            print(f"  ok  {name}")
            passed += 1
    print(f"{passed} tests passed")
