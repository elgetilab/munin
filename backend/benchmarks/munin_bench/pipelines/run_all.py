"""Track E — run selected benchmark tracks and write one committed scorecard.

    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu NEO4J_PASSWORD=... \\
      python -m munin_bench.pipelines.run_all --tag baseline-specter \\
        --tracks beir-scifact,litqa2-retrieval [--date 2026-07-03]

Tracks (opt-in via --tracks; the live-chat + GPU tracks are slow):
  beir-scifact       BEIR SciFact (in eval_* collection)
  litqa2-retrieval   AgentRetriever/dense/citation-rerank over LitQA2 (live papers)
  litqa2-answer      end-to-end MCQ (research profile) — slow, concurrency 1
  faithfulness       Track B: MiniCheck grounding of the live agentic arm (GPU)
  abstention         Track C1: refuse/confabulate on fabricated papers
  ablation           Track D: bare vs RAG vs agentic accuracy (slow: agentic ~hrs)
  --with-reliability folds the behavioral QA registry (pong, ...) PASS/FLAKY/FAIL

Writes scorecards/<date>_<tag>.{json,md} (committed). Compare two with
`python -m munin_bench.pipelines.compare A.json B.json`. Use --limit to smoke-test
the wiring without full re-runs.
"""

from __future__ import annotations

import argparse
import os

from .. import config
from ..benchmarks import beir_runner, litqa2_runner
from ..clients import get_neo4j, get_qdrant, load_encoder
from ..scorecard import make_run_header, task_from_per_query, write_scorecard

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results"))
VARIANTS = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "litqa2",
                 "litqa2_variants.json"))
ALL_TRACKS = ["beir-scifact", "litqa2-retrieval", "litqa2-answer",
              "faithfulness", "abstention", "ablation"]


def _metric(x) -> dict:
    """Coerce a bootstrap dict or scalar to the scorecard metric shape."""
    if isinstance(x, dict) and "mean" in x:
        return {"mean": x["mean"], "ci_low": x.get("ci_low"), "ci_high": x.get("ci_high")}
    return {"mean": float(x) if x is not None else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--tracks", default="beir-scifact,litqa2-retrieval")
    ap.add_argument("--encoder", default="specter-v1",
                    choices=list(config.ENCODER_PRESETS),
                    help="encoder preset: model + papers collection + query prefix")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD for the filename")
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    ap.add_argument("--base-url", default="http://127.0.0.1:8080",
                    help="live chat for faithfulness/abstention/ablation")
    ap.add_argument("--email", default="litqa2-eval@localhost")
    ap.add_argument("--limit", type=int, default=0,
                    help="cap questions for the live tracks (0 = full; use to smoke-test wiring)")
    ap.add_argument("--with-reliability", action="store_true",
                    help="run the backend/eval behavioral QA registry (pong, ...)")
    ap.add_argument("--certify", action="store_true",
                    help="check the written scorecard against certification_thresholds.json (re-cert gate)")
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
    preset = config.ENCODER_PRESETS[args.encoder]
    qc = get_qdrant()
    # Retrieval tracks need the encoder + neo4j; the live-chat/GPU tracks
    # (faithfulness/abstention/ablation) do not, so don't force NEO4J_PASSWORD
    # or an encoder load for a B/C/D-only run.
    retrieval_tracks = {"beir-scifact", "litqa2-retrieval", "litqa2-answer"}
    if retrieval_tracks & set(tracks):
        specter = load_encoder(preset["model"], device=device, hf_fallback=preset["hf"])
        neo4j = get_neo4j()
    else:
        specter = neo4j = None

    if "beir-scifact" in tracks and args.encoder != "specter-v1":
        raise SystemExit("beir-scifact builds a 768-d eval collection; only the "
                         "specter-v1 preset is wired for it. Run BEIR separately.")

    tasks = {}
    if "beir-scifact" in tracks:
        print("=== track: beir-scifact ===")
        p = beir_runner.run_subset(qc, specter, "scifact",
                                   results_root=args.results_root, device=device)
        tasks["beir_scifact"] = task_from_per_query(
            p["per_query"], p["qids"], p["metrics"])
    if "litqa2-retrieval" in tracks:
        print(f"=== track: litqa2-retrieval (encoder={args.encoder}, "
              f"collection={preset['collection']}) ===")
        p = litqa2_runner.run(qc, specter, neo4j, results_root=args.results_root,
                              variants_path=VARIANTS, device=device,
                              collection=preset["collection"],
                              query_prefix=preset["query_prefix"])
        tasks["litqa2_retrieval"] = task_from_per_query(
            p["per_query"], p["qids"], p["metrics"])
    if "litqa2-answer" in tracks:
        print("=== track: litqa2-answer ===")
        p = litqa2_runner.run_answer(qc, base_url="http://127.0.0.1:8080",
                                     email="litqa2-eval@localhost",
                                     results_root=args.results_root, concurrency=1)
        tasks["litqa2_answer"] = task_from_per_query(
            p["per_query"], p["qids"], p["sc_summary"])

    if "faithfulness" in tracks:
        print("=== track: faithfulness (Track B, live capture + MiniCheck) ===")
        from ..faithfulness.faithfulness_runner import capture_pool, score_and_metrics
        work = os.path.join(args.results_root, "..", "faithfulness_runs")
        os.makedirs(work, exist_ok=True)
        cap = os.path.join(work, "run_all.capture.jsonl")
        caps = capture_pool(args.base_url, args.email, cap, limit=args.limit or 0)
        sc = score_and_metrics(caps, arm="agentic-live", base_url=args.base_url)
        tasks["faithfulness"] = {"agentic-live": {
            "metrics": {"frac_claims_supported": _metric(sc["frac_claims_supported"]),
                        "mean_faithfulness": _metric(sc["mean_faithfulness"])},
            "per_query": {r["qid"]: {"frac_supported": r["frac_supported"]}
                          for r in sc["per_q"] if r["frac_supported"] is not None}}}

    if "abstention" in tracks:
        print("=== track: abstention (Track C1, fabricated papers) ===")
        from ..abstention.run_c1 import run as run_c1
        sc = run_c1(args.base_url, args.email, limit=args.limit or 0)
        tasks["abstention_c1"] = {"agentic": {
            "metrics": {"abstain_rate": _metric(sc["abstain_rate"]),
                        "confabulation_rate": _metric(sc["confabulation_rate"])},
            "per_query": {p["id"]: {"abstained": 1.0 if p["abstained"] else 0.0}
                          for p in sc["per_item"]}}}

    if "ablation" in tracks:
        print("=== track: ablation (Track D, bare/rag/agentic) ===")
        from ..ablation.run_arm import run as run_arm_fn
        from ..ablation.compare import compare as compare_arms
        n = args.limit or 100
        for arm in ("bare", "rag", "agentic"):
            run_arm_fn(arm, n, base_url=args.base_url, email=args.email)
        sc = compare_arms(date=None)
        tasks["ablation"] = {arm: {
            "metrics": {"accuracy": _metric(pa["accuracy"]),
                        "precision": _metric(pa.get("precision_of_attempted"))},
            "per_query": {}} for arm, pa in sc["per_arm"].items()}

    meta = make_run_header(qc, neo4j, encoder=args.encoder, tag=args.tag)
    if args.with_reliability:
        print("=== reliability: behavioral QA registry ===")
        try:
            from .reliability import run_registry
            meta["reliability"] = run_registry(base_url=args.base_url)
        except Exception as e:
            meta["reliability"] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  reliability registry failed: {e}")
    path = write_scorecard(meta, tasks, args.results_root, date_str)
    if neo4j is not None:
        neo4j.close()
    print(f"\nscorecard -> {path}  (+ .md)")

    gate_fail = False
    if args.certify:
        import json as _json
        from .certify import certify as _certify
        thr = os.path.join(os.path.dirname(__file__), "..", "..", "certification_thresholds.json")
        res = _certify({"meta": meta, "tasks": tasks}, _json.load(open(thr)))
        print("\n=== certification gate ===")
        for c in res["checks"]:
            print(f"  [{c['status']:4s}] {c['name']}")
        print(f"OVERALL: {res['overall']} ({res['n_fail']} fail, {res['n_skip']} skipped)")
        gate_fail = res["overall"] == "FAIL"

    print("commit it, then: python -m munin_bench.pipelines.compare <old>.json <new>.json")
    return 1 if gate_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
