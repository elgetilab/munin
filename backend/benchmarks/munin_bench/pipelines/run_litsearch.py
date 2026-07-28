"""CLI: run the LitSearch retrieval benchmark (BENCHMARK-TODO T8).

    PYTHONPATH=<deps>:. /opt/munin/services/pipeline/venv/bin/python \\
        -m munin_bench.pipelines.run_litsearch --date 2026-07-28

Builds the isolated ``eval_litsearch`` Qdrant collection with the PRODUCTION
BGE-large encoder (reused if present and the right size; --rebuild to force),
runs BM25 / BGE-dense / citation-rerank / RRF, and writes a scorecard.

Unlike BEIR, LitSearch ships a real citation graph, so citation-rerank is
genuinely exercised here rather than degenerating to dense-only.

Embedding ~64k docs is the dominant cost; set
MUNIN_BENCH_ENCODER_DEVICE=cuda:0 when a card is free.
"""

from __future__ import annotations

import argparse
import os

from .. import config
from ..benchmarks.litsearch_runner import run
from ..clients import get_qdrant, load_encoder

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results")
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="drop + rebuild the eval_litsearch collection")
    ap.add_argument("--n-resamples", type=int, default=1000)
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    ap.add_argument("--limit", type=int, default=0,
                    help="cap queries (smoke runs); 0 = all")
    ap.add_argument("--date", default=None,
                    help="write scorecards/<date>_litsearch.json")
    args = ap.parse_args()

    device = os.getenv("MUNIN_BENCH_ENCODER_DEVICE",
                       os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu"))
    print(f"[litsearch] encoder=BGE-large device={device}")
    encoder = load_encoder(config.BGE_LARGE_PATH, device=device,
                           hf_fallback=config.BGE_LARGE_HF_ID)
    qdrant = get_qdrant()

    run(qdrant, encoder,
        results_root=os.path.join(args.results_root, "litsearch"),
        rebuild=args.rebuild, n_resamples=args.n_resamples,
        device=device, limit=args.limit, date=args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
