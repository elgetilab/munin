"""
Tests for the source ledger (source_ledger.py, SOURCE-LEDGER-PLAN.md).

Hermetic: a temporary chats.db, no network (enrich is exercised with a fake
registry lookup).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_source_ledger.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Must be set before chat_store is imported.
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-ledger-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")

import chat_store  # noqa: E402
import citecheck  # noqa: E402
import source_ledger  # noqa: E402
from source_ledger import Ledger  # noqa: E402

SEARCH = {
    "query": "protein folding",
    "results": [
        {"source_type": "corpus", "title": "Folding of a small protein", "doi": "10.1000/JMB.1",
         "snippet": "...", "year": 1991, "authors": ["Andrej Sali", "Mark Bycroft"]},
        {"source_type": "web", "title": "A lab page", "url": "https://example.org/page#top",
         "snippet": "...", "authors": None},
        {"source_type": "oa", "title": "Same paper via OA", "doi": "https://doi.org/10.1000/jmb.1"},
        {"note": "no source here"},
    ],
    "evidence": [{"paper_id": "p42", "title": "Corpus-only paper", "text": "a passage"}],
}


def _check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' --- ' + detail) if detail and not ok else ''}")
    return ok


def test_annotate_assigns_stable_ids() -> bool:
    led = Ledger()
    view = led.annotate(SEARCH, turn=1)
    r = view["results"]
    ok = (r[0]["sid"] == "S1" and r[1]["sid"] == "S2" and r[2]["sid"] == "S1"
          and "sid" not in r[3] and view["evidence"][0]["sid"] == "S3"
          and "sid" not in SEARCH["results"][0])
    again = led.annotate({"results": [{"title": "x", "doi": "10.1000/jmb.1"}]}, turn=2)
    return _check("ids per source, deduped by DOI, original untouched, stable across calls",
                  ok and again["results"][0]["sid"] == "S1" and len(led.entries) == 3, str(view))


def test_render_numbers_links_and_references() -> bool:
    led = Ledger()
    led.annotate(SEARCH)
    text = "Folding is fast [S1]. A lab shows it [S2, S1]. Unknown [S99]."
    out, notes = led.render(text)
    ok = ("Folding is fast [[1]](https://doi.org/10.1000/jmb.1)." in out
          and "A lab shows it [[2]](https://example.org/page#top), [[1]](https://doi.org/10.1000/jmb.1)." in out
          and "Unknown." in out and "**References**" in out
          and "1. Andrej Sali, Mark Bycroft. (1991). Folding of a small protein. https://doi.org/10.1000/jmb.1" in out
          and notes == {"cited": 2, "unknown_ids": 1, "artifacts": 0})
    return _check("render: numbered links in order of use, References, unknown id removed", ok, out)


def test_render_leaves_code_and_plain_text() -> bool:
    led = Ledger()
    led.annotate(SEARCH)
    code = "```python\nx = data[S1]\n```"
    a, _ = led.render(code)
    b, n = led.render("No citations here.")
    return _check("ids inside code and answers without ids are untouched",
                  a == code and b == "No citations here." and n["cited"] == 0, a)


def test_artifacts() -> bool:
    led = Ledger()
    aid = led.artifact_id({"id": "art_1", "filename": "fit.png",
                           "external_url": "/api/artifacts/c1/sb9"})
    out, notes = led.render(f"See the plot [{aid}].")
    return _check("an artifact id renders as its real link",
                  aid == "A1" and out == "See the plot [fit.png](/api/artifacts/c1/sb9)."
                  and notes["artifacts"] == 1, out)


def test_artifacts_in_tool_results_and_block() -> bool:
    import artifact_store
    led = Ledger()
    view = led.annotate({"artifacts": [{"registered_artifact_id": "art_7", "filename": "fit.png",
                                        "external_url": "/api/artifacts/c/s7"}]})
    blk = artifact_store.build_artifact_summary_block(
        [{"id": "art_7", "filename": "fit.png", "source": "sandbox_generated"}],
        cite={"art_7": led.artifact_id({"id": "art_7"})})
    out, _ = led.render("Plot: [A1]")
    return _check("a sandbox artifact in a tool result gets an id, the block shows it, it renders",
                  view["artifacts"][0]["cite_as"] == "[A1]" and "cite as [A1]" in blk
                  and out == "Plot: [fit.png](/api/artifacts/c/s7)", f"{view} {out}")


def test_elided_result_keeps_source_ids() -> bool:
    import json as _json
    import chat_context as cc
    led = Ledger()
    content = _json.dumps(led.annotate(SEARCH))
    stub = _json.loads(cc._elided_stub(content))
    plain = cc._elided_stub('{"x": 1}')
    return _check("an elided tool result keeps its source ids; without ids the stub is unchanged",
                  stub["sources"] == ["S1", "S2", "S3"] and cc._is_elided(cc._elided_stub(content))
                  and plain == cc._ELIDED_TOOL_RESULT, str(stub))


def test_same_paper_twice_is_one_reference() -> bool:
    led = Ledger()
    t = "Site-directed spin labeling measurements of nanometer distances in nucleic acids"
    led.add("paper", "doi:10.1093/nar/gkl546", {"doi": "10.1093/nar/gkl546", "title": t})
    led.add("paper", "doi:10.1093/nar_gkl546", {"doi": "10.1093/nar_gkl546", "title": t, "_doi_bad": "not registered"})
    out, notes = led.render("a [S2] b [S1, S2]")
    return _check("the same work cited under two ids is one reference, with the good DOI",
                  notes["cited"] == 1 and out.count("[[1]](https://doi.org/10.1093/nar/gkl546)") == 2
                  and "nar_gkl546" not in out and out.count("\n1. ") == 1, out)


def test_block_budget() -> bool:
    led = Ledger()
    for i in range(200):
        led.add("paper", f"doi:10.1/{i}", {"title": f"Paper number {i} " + "x" * 60,
                                           "authors": ["A B", "C D"], "year": 2000}, turn=i)
    blk = led.block()
    return _check("sources block stays in budget, newest first, says how many are left",
                  len(blk) <= source_ledger.BLOCK_MAX_CHARS + 200 and [l for l in blk.splitlines() if re.match(r"S\d+:", l)][0].startswith("S200:")
                  and "older sources not shown" in blk, blk[:300])


def test_user_dois() -> bool:
    led = Ledger()
    led.add_user_dois("Please summarise 10.1000/jmb.1 for me.")
    return _check("a DOI the user typed is citable", led.by_key.get("doi:10.1000/jmb.1") == "S1")


def test_enrich_and_miskeyed_doi() -> bool:
    led = Ledger()
    led.add("paper", "doi:10.1000/a", {"doi": "10.1000/a", "title": "Folding of a small protein"})
    led.add("paper", "doi:10.1000/b", {"doi": "10.1000/b", "title": "Transient EPR of three-spin systems"})
    led.add("paper", "doi:10.1000/c", {"doi": "10.1000/c", "title": "Something"})

    async def fake_lookup(doi, client):
        if doi == "10.1000/c":
            return ("missing", None)
        rec = {"10.1000/a": {"title": "Folding of a small protein", "authors": ["Andrej Sali"],
                             "year": 1991, "journal": "J Mol Biol", "volume": "220", "page": "779-788"},
               "10.1000/b": {"title": "Radicals and radical pairs in photosynthesis",
                             "authors": ["A. Angerhofer"], "year": 1996, "journal": "Photochem Photobiol"}}[doi]
        return ("ok", rec)
    citecheck.lookup = fake_lookup
    asyncio.run(led.enrich(["S1", "S2", "S3"]))
    out, _ = led.render("a [S1] b [S2] c [S3]")
    ok = ("*J Mol Biol* 220, 779-788." in out and "[[2]]" not in out and "b [2]" in out
          and out.count("(no verified DOI)") == 2 and "10.1000/b" not in out and "10.1000/c" not in out)
    return _check("enrich fills from the registry; a mis-keyed or missing DOI is not linked", ok, out)


def test_persistence_roundtrip() -> bool:
    async def go():
        await chat_store.init_db()
        conv = await chat_store.create_conversation("u@test.local", "munin", title="t")
        cid = conv["id"]
        led = await Ledger.load(cid)
        led.annotate(SEARCH, turn=0)
        led.artifact_id({"id": "art_9", "filename": "a.png", "external_url": "/api/artifacts/x/y"})
        await led.save()
        led2 = await Ledger.load(cid)
        same = {s: e.key for s, e in led2.entries.items()} == {s: e.key for s, e in led.entries.items()}
        sid_new = led2.annotate({"results": [{"title": "New", "doi": "10.1000/new"}]}, turn=1)["results"][0]["sid"]
        await chat_store.delete_conversation(cid, "u@test.local")
        led3 = await Ledger.load(cid)
        return same and sid_new == "S4" and not led3.entries
    return _check("ledger persists, continues numbering, and goes with its conversation", asyncio.run(go()))


def main() -> int:
    tests = [test_annotate_assigns_stable_ids, test_render_numbers_links_and_references,
             test_render_leaves_code_and_plain_text, test_artifacts, test_artifacts_in_tool_results_and_block, test_elided_result_keeps_source_ids,
             test_same_paper_twice_is_one_reference,
             test_block_budget,
             test_user_dois, test_enrich_and_miskeyed_doi, test_persistence_roundtrip]
    results = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    # An open aiosqlite connection keeps its worker thread, and the process, alive.
    asyncio.run(chat_store.close_db())
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed; {failed} failed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
