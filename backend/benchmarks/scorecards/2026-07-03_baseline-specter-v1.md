# Scorecard — baseline-specter-v1

- model `qwen3.6-35b-a3b` | encoder `specter-v1` | git `77b6a21`
- corpus papers 68121 | graph CITES 1342364 | seed 42
- 2026-07-03T08:58:11.946690+00:00

## litqa2_retrieval

| system | recall@1 | recall@5 | recall@10 | mrr |
|---|---|---|---|---|
| specter_dense | 0.2337 | 0.3794 | 0.4472 | 0.3078 |
| citation_rerank_0.7_0.3 | 0.0000 | 0.0000 | 0.0000 | 0.0013 |
| agent | 0.1910 | 0.3693 | 0.4372 | 0.2764 |

