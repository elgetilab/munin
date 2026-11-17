"""Tests for the JSON outline in run_python results (LABKIT-013-PLAN.md).

Why: in Phase 4 run 3 eight lab runs had the right numbers but saved them
without the labels the request asked for (R1 as a bare list of 40 rates,
Gen5 rates as a list instead of by well, a NanoDrop table nested one level
too deep). run_python now outlines each JSON file a run wrote, key by key,
the way figure_summary describes a figure. The outline describes; it never
says what the file should contain.

Run standalone or under pytest:
    python backend/retrieval/tests/test_results_outline.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("CHATS_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="munin-test-ro-"), "chats.db"))

from results_outline import outline  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _b(obj) -> bytes:
    return json.dumps(obj).encode()


def test_lists_are_flagged_maps_are_described():
    # The shapes from run 3: relaxation, standard curve, Gen5, NanoDrop.
    relax = outline("results.json", _b({"R1": [2.03] * 40, "mean_R2_R1_rigid": 9.06,
                                         "warnings": ["H20: negative intensity"]}))
    std = outline("results.json", _b({"U1": [11.464, [9.09, 13.839]], "U5": None}))
    nano = outline("results.json", _b({"concentration": {"pl01": {"concentration": 78.7, "ratio_260_280": 1.91}}}))
    good = outline("results.json", _b({"R1": {"K12": 1.2, "G13": 1.3}, "Kd_uM": 2.23}))
    ok = ("R1: list of 40 numbers (no labels)" in relax and "mean_R2_R1_rigid: number 9.06" in relax
          and "warnings: list of 1 texts" in relax and "texts [\"H20" in relax
          and "no labels)" not in relax.split("warnings:")[1]
          and "U1: list of 2 (number, list) [11.464, [9.09, 13.839]] (no labels)" in std
          and "U5: null" in std
          and "objects with keys concentration, ratio_260_280" in nano
          and "R1: object with 2 keys ('K12', 'G13') -> numbers" in good and "Kd_uM: number 2.23" in good)
    return _check("bare number lists say (no labels); keyed maps and nesting are described",
                  ok, "\n".join([relax, std, nano, good]))


def test_bad_or_huge_json():
    bad = outline("results.json", b"{not json")
    huge = outline("results.json", b"[" + b"1," * 200000 + b"1]")
    top = outline("x.json", _b([1, 2, 3]))
    return _check("invalid JSON is said; files over the cap are skipped; a top-level list is described",
                  bad == "results.json: not valid JSON" and huge == ""
                  and top.startswith("x.json: top level is a list of 3 numbers"), f"{bad!r} {huge!r} {top!r}")


def test_run_python_outlines_json_artifacts():
    """End to end through run_python with the sandbox HTTP calls stubbed."""
    import mcp.context as ctx
    from mcp.tools import sandbox

    body = _b({"initial_rate": [0.056, 0.033, 0.018, 0.008]})

    class _Resp:
        def __init__(self, payload=None, content=b"", status=200):
            self._p, self.content, self.status_code, self.text = payload, content, status, ""

        def json(self):
            return self._p

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp({"stdout": "ok", "artifacts": [
                {"id": "a1", "filename": "results.json", "size_bytes": len(body), "content_type": "application/json"},
                {"id": "a2", "filename": "plot.png", "size_bytes": 10, "content_type": "image/png"}]})

        async def get(self, url, *a, **k):
            return _Resp(content=body) if url.endswith("/a1") else _Resp(status=404)

    async def _no_register(**kw):
        return {"id": "art_x", "source": "sandbox", "external_url": "/x"}

    import artifact_store
    orig = sandbox.httpx.AsyncClient, artifact_store.register_sandbox_artifact
    sandbox.httpx.AsyncClient = _Client
    artifact_store.register_sandbox_artifact = _no_register
    t1 = ctx.current_user_email.set("u@example.org")
    t2 = ctx.current_conversation_id.set("conv-ro")
    try:
        r = asyncio.run(sandbox.run_python("import json\njson.dump({}, open('results.json','w'))"))
    finally:
        sandbox.httpx.AsyncClient, artifact_store.register_sandbox_artifact = orig
        ctx.current_user_email.reset(t1)
        ctx.current_conversation_id.reset(t2)
        sandbox.forget_source("conv-ro")
    lines = r.get("json_outline") or []
    return _check("run_python adds json_outline for the JSON file it wrote, not for the image",
                  len(lines) == 1 and "initial_rate: list of 4 numbers" in lines[0]
                  and "json_outline_hint" in r, repr(r.get("json_outline")))


TESTS = [
    test_lists_are_flagged_maps_are_described,
    test_bad_or_huge_json,
    test_run_python_outlines_json_artifacts,
]


def main() -> int:
    passed = 0
    for t in TESTS:
        try:
            passed += bool(t())
        except Exception:
            traceback.print_exc()
            print(f"[FAIL] {t.__name__} raised")
    print(f"\n{passed}/{len(TESTS)} passed")
    return 0 if passed == len(TESTS) else 1


if __name__ == "__main__":
    sys.exit(main())
