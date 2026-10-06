# Scorecard — gpt-oss-20b-smoke

- model `gpt-oss-20b` | encoder `specter-v1` | git `e37892e`
- corpus papers 68913 | graph CITES n/a | seed 42
- 2026-09-15T22:40:01.799158+00:00

## ablation

| system | accuracy | precision | abstain_rate | mean_tool_calls | mean_elapsed_s | unparseable | tool_markup_in_content |
|---|---|---|---|---|---|---|---|
| bare | 0.3015 | 0.4255 | 0.0603 | 0.0000 | 1.9000 | 46.0000 | 0.0000 |
| rag | 0.0603 | 0.6316 | 0.0754 | 0.0000 | 0.9000 | 165.0000 | 0.0000 |
| agentic | 0.4673 | 0.8857 | 0.3166 | 8.1000 | 25.3000 | 31.0000 | 1.0000 |

