"""Extract the worked example for panel (b) of fig_architecture into
data/architecture_trace.json.

Runs only where the raw logs exist (the cluster): the benchmark capture is
gitignored and the agent trace log lives under /opt/munin. The JSON it writes
is committed, so fig_architecture.py never needs either source. Re-run this
only to change the example.

The capture records which tools ran and how long each took, but not their
arguments. The agent trace log records the arguments, but eval turns carry
no question id (conversation_id is "ephemeral-<hex>"). The two are joined on
the question text and on tool durations, which the two logs record
independently and which must agree to the millisecond, so a wrong join
fails loudly instead of drawing someone else's turn.

    python3 docs/paper-kit/figures/extract_architecture_trace.py
"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

DEFAULT_QID = "ca4c9d21-b842-4875-9a6a-bcb9f6c55073"
DEFAULT_RUN = "qwen38-27b-recapture"
DEFAULT_TRACE_LOG = "/opt/munin/data/agent_traces/2026-09-16.jsonl"
LITQA2_GLOB = (
    "~/.cache/huggingface/hub/datasets--futurehouse--lab-bench/"
    "snapshots/*/LitQA2/*.parquet"
)


def _load_question(qid: str) -> dict:
    import pandas as pd

    paths = glob.glob(str(Path(LITQA2_GLOB).expanduser()))
    if not paths:
        raise SystemExit(f"LitQA2 parquet not found under {LITQA2_GLOB}")
    df = pd.read_parquet(paths[0])
    row = df[df.id == qid].iloc[0]
    return {
        "question": row.question,
        "ideal": row.ideal,
        "distractors": list(row.distractors),
        "source": list(row.sources),
    }


def _load_capture(run: str, qid: str) -> tuple[dict, dict]:
    run_dir = REPO / "backend/benchmarks/ablation_runs" / run
    meta = json.loads((run_dir / "agentic.meta.json").read_text())
    for line in (run_dir / "agentic.capture.jsonl").open():
        row = json.loads(line)
        if row["qid"] == qid:
            return row, meta
    raise SystemExit(f"{qid} not in {run_dir}/agentic.capture.jsonl")


def _join_agent_traces(log: str, capture: dict, keywords: list[str]) -> list[dict]:
    """The agent traces of the one conversation whose durations match."""
    wanted = [e["duration_ms"] for e in capture["tool_events"]]
    convs: dict[str, list[dict]] = {}
    for line in open(log):
        r = json.loads(line)
        convs.setdefault(r.get("conversation_id") or "", []).append(r)
    for cid, rows in convs.items():
        text = json.dumps([r.get("query") or r.get("question") or "" for r in rows])
        if not all(k in text for k in keywords):
            continue
        got = [round(r["elapsed_s"] * 1000) for r in rows]
        if got == wanted:
            return rows
    raise SystemExit(f"no conversation in {log} matches durations {wanted} ms")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--qid", default=DEFAULT_QID)
    ap.add_argument("--run", default=DEFAULT_RUN)
    ap.add_argument("--trace-log", default=DEFAULT_TRACE_LOG)
    ap.add_argument("--keywords", nargs="+", default=["LungMAP"])
    ap.add_argument("--out", default=str(HERE / "data/architecture_trace.json"))
    args = ap.parse_args()

    q = _load_question(args.qid)
    capture, meta = _load_capture(args.run, args.qid)
    traces = _join_agent_traces(args.trace_log, capture, args.keywords)

    # The quote the answer rests on, as the source agent returned it: the
    # capture keeps each tool's grounding context, and source(qa) contexts
    # start with "Answer:".
    qa_context = next(c for c in capture["contexts"] if c.startswith("Answer:"))
    search_hits = [c.split(":", 1)[0] for c in capture["contexts"]
                   if not c.startswith("Answer:")][:10]

    steps = []
    for t in traces:
        step = {
            "tool": t["agent"],
            "elapsed_s": t["elapsed_s"],
            "outcome": t["outcome"],
            "n_llm_calls": t["n_llm_calls"],
        }
        if t["agent"] == "search":
            d = {x["msg"]: x for x in t["decisions"]}
            step.update(
                query=t["query"],
                depth=t["depth"],
                tiers_enabled={k: d["tier fan-out"][k] for k in ("corpus", "oa", "web")},
                n_query_variants=d["query expansion"]["n_variants"],
                corpus_hits=d["stage 1 corpus"]["n"],
                corpus_sufficient=d["stage 1 corpus"]["sufficient"],
                top_hits=search_hits[:3],
            )
        elif t["agent"] == "source":
            src = t["resolved_sources"][0]
            step.update(
                mode=t["mode"],
                question=t["question"],
                resolved={"doi": src["doi"], "title": src["title"],
                          "first_author": src["authors"][0]},
                returned=qa_context,
            )
        steps.append(step)

    # The model's final line is "Answer: <letter>"; the verdict is the
    # harness's scoring of it against the ideal.
    out = {
        "provenance": {
            "benchmark": "LitQA2 (FutureHouse lab-bench)",
            "qid": args.qid,
            "run": f"backend/benchmarks/ablation_runs/{args.run}",
            "model": meta["model"],
            "git_sha": meta["git_sha"],
            "egress": meta["egress"],
            "persona_pinned": "research",
            "captured": meta["finished_at"][:10],
            "joined_on": "question text + per-tool duration (ms), both logs",
        },
        "question": q["question"],
        "ideal": q["ideal"],
        "distractors": q["distractors"],
        "steps": steps,
        "final": {
            "letter": capture["letter"],
            "verdict": capture["verdict"],
            "elapsed_s": capture["elapsed_s"],
            "tool_calls": capture["tool_calls"],
            "cited_doi": steps[-1]["resolved"]["doi"],
            "answer_tail": capture["answer"].strip()[-700:],
        },
    }
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
