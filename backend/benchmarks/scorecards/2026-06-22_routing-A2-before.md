# Routing scorecard — routing-A2-before

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `6b1b77d`  reps: 8  seed: 42
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-22T13:57:42.709332+00:00

**Mean pass rate: 0.652** (95% CI 0.468–0.836) over 14 items; mean flip-rate 0.275

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 4/8 | 0.71 |  |
| `define_nmr` | no_tool | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 4/8 | 0.71 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 6/8 | 0.57 | required 'paper_search': calls=10 (want 1-4), preds_ok=True |
| `known_doi_read` | direct_ref | 8/8 | 0.00 |  |
| `known_url_fetch` | direct_ref | 4/8 | 0.71 | first tool 'run_python', expected 'web_fetch' |
| `percent_calc` | compute | 7/8 | 0.14 | first tool '<none>', expected 'calculate' |
| `remember_research_area` | memory | 0/8 | 0.00 | required 'remember': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute` | multi_turn | 1/8 | 0.29 | forbidden tools fired: ['paper_search', 'paper_search', 'paper_search' |
| `sota_phip` | deep_research | 1/8 | 0.14 | first tool 'web_search', expected 'deep_research' |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 6/8 | 0.57 | first tool 'web_search', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 8/8 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.00 |
| `export_bibtex` | completed_in_turn:export_citations | 0.00 |
| `known_doi_read` | completed_in_turn:read_paper | 0.00 |
| `remember_research_area` | completed_in_turn:remember | 0.00 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 0.62 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
