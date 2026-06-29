"""CLI: run a BEIR subset end-to-end.

    PYTHONPATH=<deps>:. /opt/munin/services/pipeline/venv/bin/python \\
        -m munin_bench.pipelines.run_beir --subset scifact

Builds the eval_<subset> Qdrant collection (reused if present; --rebuild to
force), runs BM25 / SPECTER / citation-rerank / RRF, writes
results/beir/<subset>/summary.{json,md}. SPECTER embeds on CPU by default
(GPUs busy with vLLM); set MUNIN_BENCH_SPECTER_DEVICE=cuda when the cards
are free for large subsets.
"""

from __future__ import annotations

import argparse
import os

from ..benchmarks.beir_runner import SUBSETS, run_subset
from ..clients import get_qdrant, load_specter

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results")
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subset", required=True, choices=list(SUBSETS))
    ap.add_argument("--rebuild", action="store_true",
                    help="drop + rebuild the eval_<subset> collection")
    ap.add_argument("--n-resamples", type=int, default=1000)
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    args = ap.parse_args()

    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    qdrant = get_qdrant()
    specter = load_specter(device=device)

    payload = run_subset(
        qdrant, specter, args.subset,
        results_root=args.results_root, rebuild=args.rebuild,
        n_resamples=args.n_resamples, device=device,
    )

    # Gate echo: SPECTER dense nDCG@10 (>0.5 expected on scifact).
    nd = payload["metrics"]["ndcg@10"]["specter_dense"]["mean"]
    print(f"\nSPECTER dense nDCG@10 = {nd:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
