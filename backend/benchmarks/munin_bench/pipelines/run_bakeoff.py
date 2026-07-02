"""CLI: encoder bake-off on a BEIR subset (in-memory, non-invasive).

    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu \\
      /opt/munin/services/pipeline/venv/bin/python -m munin_bench.pipelines.run_bakeoff --subset scifact
"""

from __future__ import annotations

import argparse
import os

from ..benchmarks.encoder_bakeoff import SUBSETS, run, run_litqa2_pool

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="beir", choices=["beir", "litqa2-pool"])
    ap.add_argument("--subset", default="scifact", choices=list(SUBSETS))
    ap.add_argument("--n-distractors", type=int, default=5000)
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    ap.add_argument("--n-resamples", type=int, default=1000)
    args = ap.parse_args()
    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    if args.task == "litqa2-pool":
        from ..clients import get_qdrant
        run_litqa2_pool(get_qdrant(), results_root=args.results_root,
                        device=device, n_distractors=args.n_distractors,
                        n_resamples=args.n_resamples)
    else:
        run(args.subset, results_root=args.results_root, device=device,
            n_resamples=args.n_resamples)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
