# Routing scorecard — routing-A2-after-prompt-n16

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `bc00638`  reps: 16  seed: 42
- persona policy: _eval_full (test-config override; e.g. _eval_full = full tool universe for the A2 post-allowlist gate preview)
- timestamp: 2026-06-23T11:56:44.731867+00:00

**Mean pass rate: 0.719** (95% CI 0.405–1.000) over 4 items; mean flip-rate 0.200

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 15/16 | 0.13 |  |
| `export_bibtex` | citation_export | 12/16 | 0.40 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `known_doi_read` | direct_ref | 16/16 | 0.00 |  |
| `remember_research_area` | memory | 3/16 | 0.27 | required 'remember': calls=0 (want 1-99), preds_ok=False |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.31 |
| `export_bibtex` | completed_in_turn:export_citations | 0.62 |
| `known_doi_read` | completed_in_turn:read_paper | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.19 |
