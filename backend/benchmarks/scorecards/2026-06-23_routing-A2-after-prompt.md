# Routing scorecard — routing-A2-after-prompt

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `e3629f5`  reps: 8  seed: 42
- persona policy: _eval_full (test-config override; e.g. _eval_full = full tool universe for the A2 post-allowlist gate preview)
- timestamp: 2026-06-23T09:38:57.304694+00:00

**Mean pass rate: 0.719** (95% CI 0.309–1.000) over 4 items; mean flip-rate 0.071

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 7/8 | 0.29 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `known_doi_read` | direct_ref | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 0/8 | 0.00 | required 'remember': calls=0 (want 1-99), preds_ok=False |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.75 |
| `export_bibtex` | completed_in_turn:export_citations | 0.75 |
| `known_doi_read` | completed_in_turn:read_paper | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.00 |
