"""Track E — run selected benchmark tracks and write one committed scorecard.

    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu NEO4J_PASSWORD=... \\
      python -m munin_bench.pipelines.run_all --tag baseline-specter \\
        --tracks beir-scifact,litqa2-retrieval [--date 2026-07-03]

Tracks (opt-in via --tracks; answer is slow + hits the shared chat service):
  beir-scifact       BEIR SciFact (in eval_* collection)
  litqa2-retrieval   AgentRetriever/dense/citation-rerank over LitQA2 (live papers)
  litqa2-answer      end-to-end MCQ (research profile) — slow, concurrency 1

Writes scorecards/<date>_<tag>.{json,md} (committed). Compare two with
`python -m munin_bench.pipelines.compare A.json B.json`.
"""

from __future__ import annotations

import argparse
import os

from ..benchmarks import beir_runner, litqa2_runner
from ..clients import get_neo4j, get_qdrant, load_specter
from ..scorecard import make_run_header, task_from_per_query, write_scorecard

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results"))
VARIANTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "litqa2",
                 "litqa2_variants.json"))
ALL_TRACKS = ["beir-scifact", "litqa2-retrieval", "litqa2-answer"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tracks", default="beir-scifact,litqa2-retrieval")
    ap.add_argument("--encoder", default="specter-v1",
                    help="label recorded in the scorecard header (provenance only)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD for the filename")
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    args = ap.parse_args()

    tracks = [t.strip() for t in args.tracks.split(",") if t.strip()]
    for t in tracks:
        if t not in ALL_TRACKS:
            raise SystemExit(f"unknown track {t!r}; known: {ALL_TRACKS}")
    # No Date.now in scripts is a workflow constraint; run_all is a plain CLI, but
    # we still let --date be passed for reproducible filenames.
    if args.date:
        date_str = args.date
    else:
        from datetime import datetime, timezone
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    qc = get_qdrant()
    specter = load_specter(device=device)
    neo4j = get_neo4j()

    tasks = {}
    if "beir-scifact" in tracks:
        print("=== track: beir-scifact ===")
        p = beir_runner.run_subset(qc, specter, "scifact",
                                   results_root=args.results_root, device=device)
        tasks["beir_scifact"] = task_from_per_query(
            p["per_query"], p["qids"], p["metrics"])
    if "litqa2-retrieval" in tracks:
        print("=== track: litqa2-retrieval ===")
        p = litqa2_runner.run(qc, specter, neo4j, results_root=args.results_root,
                              variants_path=VARIANTS, device=device)
        tasks["litqa2_retrieval"] = task_from_per_query(
            p["per_query"], p["qids"], p["metrics"])
    if "litqa2-answer" in tracks:
        print("=== track: litqa2-answer ===")
        p = litqa2_runner.run_answer(qc, base_url="http://127.0.0.1:8080",
                                     email="litqa2-eval@localhost",
                                     results_root=args.results_root, concurrency=1)
        tasks["litqa2_answer"] = task_from_per_query(
            p["per_query"], p["qids"], p["sc_summary"])

    meta = make_run_header(qc, neo4j, encoder=args.encoder, tag=args.tag)
    path = write_scorecard(meta, tasks, args.results_root, date_str)
    neo4j.close()
    print(f"\nscorecard -> {path}  (+ .md)")
    print("commit it, then: python -m munin_bench.pipelines.compare <old>.json <new>.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
