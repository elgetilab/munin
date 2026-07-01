"""CLI: LitQA2 retrieval track.

    export NEO4J_PASSWORD=...
    PYTHONPATH=<deps>:. MUNIN_BENCH_SPECTER_DEVICE=cpu \\
      /opt/munin/services/pipeline/venv/bin/python -m munin_bench.pipelines.run_litqa2

Generates (once) the frozen query variants via the production expander (needs
vLLM up), runs AgentRetriever / SPECTER-dense / citation-rerank over the
in-corpus LitQA2 questions against the live papers corpus, writes
results/litqa2/retrieval.{json,md}.
"""

from __future__ import annotations

import argparse
import os

from ..benchmarks.litqa2_runner import run
from ..clients import get_neo4j, get_qdrant, load_specter

RESULTS_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "results"))
# Under data/ (gitignored): the file keys on LitQA2 question text (third-party
# data), so it is NOT committed. A fresh clone regenerates it via the runner.
VARIANTS_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "data", "litqa2",
                 "litqa2_variants.json"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-root", default=RESULTS_ROOT)
    ap.add_argument("--variants", default=VARIANTS_PATH)
    ap.add_argument("--n-resamples", type=int, default=1000)
    args = ap.parse_args()

    device = os.getenv("MUNIN_BENCH_SPECTER_DEVICE", "cpu")
    qc = get_qdrant()
    specter = load_specter(device=device)
    neo4j = get_neo4j()

    payload = run(qc, specter, neo4j, results_root=args.results_root,
                  variants_path=args.variants, n_resamples=args.n_resamples,
                  device=device)
    agent = payload["metrics"]
    print("\n=== AgentRetriever (production) ===")
    for m in ("recall@1", "recall@5", "recall@10", "mrr"):
        s = agent[m]["agent"]
        print(f"  {m:10} {s['mean']:.4f} [{s['ci_low']:.3f}, {s['ci_high']:.3f}]")
    neo4j.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
