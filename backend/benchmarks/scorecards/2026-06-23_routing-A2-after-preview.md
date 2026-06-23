# Routing scorecard — routing-A2-after-preview

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `bc00638`  reps: 8  seed: 42
- persona policy: _eval_full (test-config override; e.g. _eval_full = full tool universe for the A2 post-allowlist gate preview)
- timestamp: 2026-06-23T09:07:48.964234+00:00

**Mean pass rate: 0.656** (95% CI 0.328–0.985) over 4 items; mean flip-rate 0.250

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 5/8 | 0.43 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `known_doi_read` | direct_ref | 7/8 | 0.29 | forbidden tools fired: ['semantic_scholar_search'] |
| `remember_research_area` | memory | 1/8 | 0.29 | required 'remember': calls=0 (want 1-99), preds_ok=False |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.25 |
| `export_bibtex` | completed_in_turn:export_citations | 0.38 |
| `known_doi_read` | completed_in_turn:read_paper | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.12 |
