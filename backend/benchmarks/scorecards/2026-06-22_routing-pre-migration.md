# Routing scorecard — routing-pre-migration

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `d4e30a2`  reps: 8  seed: 42
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-22T11:09:35.169050+00:00

**Mean pass rate: 0.714** (95% CI 0.547–0.881) over 14 items; mean flip-rate 0.235

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 8/8 | 0.00 |  |
| `define_nmr` | no_tool | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 6/8 | 0.29 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 4/8 | 0.43 | required 'paper_search': calls=6 (want 1-4), preds_ok=True |
| `known_doi_read` | direct_ref | 7/8 | 0.29 | required 'read_paper': calls=0 (want 1-99), preds_ok=False |
| `known_url_fetch` | direct_ref | 5/8 | 0.57 | first tool 'web_search', expected 'web_fetch' |
| `percent_calc` | compute | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 3/8 | 0.57 | required 'remember': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute` | multi_turn | 0/8 | 0.00 | required 'run_python': calls=0 (want 1-99), preds_ok=False |
| `sota_phip` | deep_research | 5/8 | 0.57 | first tool 'web_search', expected 'deep_research' |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 2/8 | 0.57 | first tool 'web_search', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 8/8 | 0.00 |  |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
