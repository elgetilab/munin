"""Regression tests: a self-correcting turn shows ONE result and ONE answer.

Why (docs/agent-track/TURN-RESULTS-PLAN.md): chat 13550817 (2026-11-13). The
model guessed PDB 6JFQ for AT1R, showed it, found it was a bacterial enzyme,
looked up 4YAY and showed that. The chat displayed both viewers and one text
holding the wrong claim, the retraction and the answer. Figure redraws
(2026-11-04) showed every version of the plot.

These tests pin the fix at the backend: the replaced result is marked
superseded (and an `artifact_superseded` event is sent), the text written
before tool calls is stored as step_text (work log), and the saved message
holds only the answer. Frontend tests pin the rendering
(frontend/webui/src/lib/artifactDisplay.test.ts, components/TaskLog.test.tsx,
e2e/turn-results.spec.ts).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_turn_results.py
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import turn_results as T  # noqa: E402

RESULTS: list[bool] = []


def check(name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else ' - ' + str(detail)}")
    return ok


def _protein(cid, pdb, aid, replaces=None):
    args = {"pdb_id": pdb}
    if replaces:
        args["replaces"] = replaces
    return {"id": cid, "name": "show_protein", "arguments": args,
            "result": {"id": aid, "title": f"{pdb}: x", "content_type": "application/vnd.munin.widget+json"}}


def _fig(cid, code, aid, filename=None, name="run_python"):
    fn = filename or (aid.replace("art_", "").ljust(32, "0")[:32] + ".png")
    args = {"code": code} if name == "run_python" else {"edits": []}
    res = {"artifacts": [{"registered_artifact_id": aid, "filename": fn, "content_type": "image/png"}]}
    if name == "edit_python":
        res["resulting_code"] = code
    return {"id": cid, "name": name, "arguments": args, "result": res}


PLOT = """import numpy as np, matplotlib.pyplot as plt
t = np.linspace(0, 60, 200)
p = 0.2 * np.exp(-t / 20)
fig, ax = plt.subplots(figsize=(4, 3))
ax.plot(t, p)
ax.set_yscale('log')
ax.set_xlabel('time (s)'); ax.set_ylabel('polarisation')
plt.show()
"""
PREAMBLE = """import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
df = pd.read_csv('inputs/data.csv')
df['ratio'] = df['a'] / df['b']
summary = df.groupby('group').agg(['mean', 'std'])
"""


# --- supersede rules -------------------------------------------------------------

def test_at1r_with_replaces_collapses():
    e = [_protein("c1", "6JFQ", "art_wrong"),
         {"id": "c2", "name": "search", "arguments": {"query": "AT1R PDB"}, "result": {"ranked": []}},
         _protein("c3", "4YAY", "art_right", replaces=["art_wrong"])]
    ev = []
    for k in range(len(e)):
        ev += T.supersede(e, k)
    check("AT1R: the corrected viewer (replaces=) supersedes the wrong one",
          e[0].get("superseded_by") == {"art_wrong": "art_right"} and len(ev) == 1
          and ev[0]["tool_call_id"] == "c1" and "superseded_by" not in e[2], (e, ev))


def test_two_proteins_without_replaces_both_stay():
    e = [_protein("c1", "1CRN", "art_a"), _protein("c2", "4HHB", "art_b")]
    ev = T.supersede(e, 0) + T.supersede(e, 1)
    check("two structures without replaces are both results (can be what the user asked)", ev == [], ev)


def test_same_title_create_artifact_across_steps():
    def ca(cid, aid, title):
        return {"id": cid, "name": "create_artifact", "arguments": {"title": title, "content": "x"},
                "result": {"id": aid, "title": title, "content_type": "text/x-python"}}
    e = [ca("c1", "art_1", "unpacker.py"), ca("c2", "art_2", "Unpacker.py "), ca("c3", "art_3", "other.py")]
    for k in range(3):
        T.supersede(e, k)
    check("create_artifact with an earlier title (case/space-insensitive) supersedes it; other titles stay",
          e[0].get("superseded_by") == {"art_1": "art_2"} and "superseded_by" not in e[1]
          and "superseded_by" not in e[2], e)


def test_redraw_of_auto_figure_supersedes():
    e = [_fig("c1", PLOT, "art_f1"), _fig("c2", PLOT.replace("ax.plot(t, p)", "ax.plot(t, p)\nax.axhline(8e-6, ls='--')"), "art_f2"),
         _fig("c3", PLOT.replace("figsize=(4, 3)", "figsize=(5, 3)"), "art_f3", name="edit_python")]
    for k in range(3):
        T.supersede(e, k)
    check("a redrawn auto-captured figure (run_python and edit_python) supersedes the previous drawing",
          e[0].get("superseded_by") == {"art_f1": "art_f2"} and e[1].get("superseded_by") == {"art_f2": "art_f3"}
          and "superseded_by" not in e[2], e)


def test_different_figure_sharing_preamble_stays():
    a = PREAMBLE + "fig, ax = plt.subplots()\nax.scatter(df['a'], df['b'])\nax.set_title('a vs b')\nplt.show()\n"
    b = PREAMBLE + "fig, ax = plt.subplots()\nax.hist(df['ratio'], bins=30)\nax.set_title('Ratio')\nplt.show()\n"
    e = [_fig("c1", a, "art_s"), _fig("c2", b, "art_h")]
    T.supersede(e, 0)
    T.supersede(e, 1)
    check("a different figure sharing the data loading (similar code, other plot type) is not a redraw",
          "superseded_by" not in e[0], e)


def test_named_files_never_auto_superseded():
    e = [_fig("c1", PLOT, "art_n1", filename="fig_a.png"), _fig("c2", PLOT, "art_n2", filename="fig_b.png")]
    T.supersede(e, 0)
    T.supersede(e, 1)
    check("figures saved under different names are different results", "superseded_by" not in e[0], e)


def test_failed_call_supersedes_nothing():
    e = [_protein("c1", "6JFQ", "art_w"),
         {"id": "c2", "name": "show_protein", "arguments": {"pdb_id": "XXXX", "replaces": ["art_w"]},
          "result": {"error": "no PDB entry XXXX"}}]
    T.supersede(e, 1)
    check("a correction that failed does not hide the earlier result", "superseded_by" not in e[0], e)


def test_answer_split_rule():
    split, items = T.answer_and_narration([("c1", "I'll show 6JFQ."), ("c2", "")], "The correct id is 4YAY " * 3)
    thin, _ = T.answer_and_narration([("c1", "Plotting it now.")], "Done.")
    none, _ = T.answer_and_narration([], "An answer long enough to stand on its own here.")
    check("narration splits off only when the answer stands alone (>= 40 chars) and there is narration",
          split and items == [("c1", "I'll show 6JFQ.")] and not thin and not none)


# --- the chat loop, end to end -------------------------------------------------------

STEP1 = ("I'll show you the AT1R structure. The first crystal structure was solved in 2017 "
         "(PDB ID: **6JFQ**).")
STEP2 = "That PDB ID was wrong: it is a different protein. Let me find the correct one."
ANSWER = ("The correct PDB entry is **4YAY**, the first crystal structure of the human AT1R "
          "(Cell 2015), shown in the viewer.")


def _scripted(steps):
    """A fake _stream_vllm_once that plays `steps` (text, [tool calls]) in order;
    the wrap-up (enable_tools=False) is never expected."""
    from chat_service import _StreamAccumulator
    state = {"i": 0}

    async def fake(*args, **kwargs):
        if kwargs.get("enable_tools") is False:
            raise AssertionError("no wrap-up expected")
        text, calls = steps[state["i"]]
        state["i"] += 1
        acc = _StreamAccumulator()
        if text:
            acc.content_parts.append(text)
            yield ("token", {"content": text}, acc)
        for k, (cid, name, args) in enumerate(calls):
            acc.tool_calls[k] = {"id": cid, "name": name, "arguments_raw": json.dumps(args)}
        acc.finish_reason = "tool_calls" if calls else "stop"
        for cid, name, args in calls:
            yield ("tool_call", {"id": cid, "name": name, "arguments": args}, acc)
    return fake


async def _fake_tools(tool_calls, **kw):
    out = []
    for tc in tool_calls:
        if tc["name"] == "show_protein":
            pdb = tc["arguments"]["pdb_id"]
            res = {"id": f"art_{pdb.lower()}", "title": f"{pdb}: structure",
                   "content_type": "application/vnd.munin.widget+json", "version": 1}
        else:
            res = {"ranked": [{"title": "Structure of the Angiotensin Receptor", "doi": "10.1016/j.cell.2015.04.011"}]}
        out.append({"id": tc["id"], "name": tc["name"], "result": res, "duration_ms": 1})
    return out


def _run(steps):
    import test_stream_error_persistence as harness
    capture: dict = {}
    events = harness._drive_stream_chat(_scripted(steps), _fake_tools, capture)
    saved = [c for c in capture["calls"] if c.get("role") == "assistant"][-1]
    return events, saved


def _sse_events(events, name):
    """Payloads of the SSE events called `name` (chat_service._sse yields
    {"event": ..., "data": json})."""
    return [json.loads(ev["data"]) for ev in events if isinstance(ev, dict) and ev.get("event") == name]


def test_loop_at1r_turn():
    steps = [(STEP1, [("c1", "show_protein", {"pdb_id": "6JFQ"})]),
             (STEP2, [("c2", "search", {"query": "AT1R crystal structure PDB"})]),
             ("", [("c3", "show_protein", {"pdb_id": "4YAY", "replaces": ["art_6jfq"]})]),
             (ANSWER, [])]
    events, saved = _run(steps)
    tcs = saved["tool_calls"]
    sup = _sse_events(events, "artifact_superseded")
    done = _sse_events(events, "done")[-1]
    ok = (saved["content"].strip() == ANSWER and "6JFQ" not in saved["content"]
          and tcs[0].get("step_text", "").strip() == STEP1 and tcs[1].get("step_text", "").strip() == STEP2
          and "step_text" not in tcs[2]
          and tcs[0].get("superseded_by") == {"art_6jfq": "art_4yay"}
          and sup == [{"artifact_id": "art_6jfq", "superseded_by": "art_4yay", "tool_call_id": "c1"}]
          and done.get("narration_in_log") is True)
    check("chat loop, AT1R replay: message = answer only, narration in step_text, wrong viewer superseded "
          "(saved and streamed), done says narration_in_log", ok,
          {"content": saved["content"], "tool_calls": tcs, "sup": sup, "done": done})


def test_loop_thin_answer_keeps_everything():
    steps = [("Here is the full explanation of AT1R and why 4YAY is the right entry, in detail.",
              [("c1", "show_protein", {"pdb_id": "4YAY"})]),
             ("Done.", [])]
    events, saved = _run(steps)
    done = _sse_events(events, "done")[-1]
    ok = ("4YAY is the right entry" in saved["content"] and "Done." in saved["content"]
          and all("step_text" not in t for t in saved["tool_calls"]) and done.get("narration_in_log") is False)
    check("chat loop: a thin final answer keeps all text in the message (no split)", ok,
          {"content": saved["content"], "done": done})


def test_loop_no_tools_unchanged():
    events, saved = _run([(ANSWER, [])])
    done = _sse_events(events, "done")[-1]
    check("chat loop: a turn without tools is saved exactly as before",
          saved["content"].strip() == ANSWER and done.get("narration_in_log") is False, saved)


# --- show_protein(query=...): look a protein up instead of recalling an id ------

def test_show_protein_query_returns_candidates_without_a_viewer():
    import asyncio
    import httpx
    from mcp.tools import structures

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "rcsbsearch" in str(request.url):
            body = json.loads(request.content)
            assert body["query"]["parameters"]["value"] == "angiotensin II receptor type 1"
            return httpx.Response(200, json={"result_set": [{"identifier": "4YAY"}, {"identifier": "4ZUD"}]})
        return httpx.Response(200, json={"data": {"entries": [
            {"rcsb_id": "4YAY", "struct": {"title": "XFEL structure of human Angiotensin Receptor"},
             "rcsb_entry_info": {"experimental_method": "X-ray", "resolution_combined": [2.9]},
             "polymer_entities": [{"rcsb_entity_source_organism": [{"scientific_name": "Homo sapiens"}]}]},
            {"rcsb_id": "4ZUD", "struct": {"title": "Angiotensin II type 1 receptor with olmesartan"},
             "rcsb_entry_info": {"experimental_method": "X-ray", "resolution_combined": [2.8]},
             "polymer_entities": []}]}})

    real = structures.httpx.AsyncClient

    def fake_client(*a, **k):
        k["transport"] = httpx.MockTransport(handler)
        return real(*a, **k)
    structures.httpx.AsyncClient = fake_client
    try:
        out = asyncio.run(structures.show_protein(query="angiotensin II receptor type 1"))
    finally:
        structures.httpx.AsyncClient = real
    c = out.get("candidates") or []
    ok = ([x["pdb_id"] for x in c] == ["4YAY", "4ZUD"] and c[0]["organisms"] == ["Homo sapiens"]
          and c[0]["resolution_A"] == 2.9 and "id" not in out and "Nothing is shown" in out.get("note", "")
          and len(seen) == 2)
    check("show_protein(query=) returns RCSB candidates and creates no viewer", ok, out)


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception:
            traceback.print_exc()
            check(t.__name__, False, "raised")
    print(f"\n{sum(RESULTS)}/{len(RESULTS)} passed")
    sys.exit(0 if all(RESULTS) else 1)
