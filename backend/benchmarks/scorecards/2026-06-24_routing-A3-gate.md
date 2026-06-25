# Routing scorecard — routing-A3-gate

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `915a1e3`  reps: 8  seed: 42
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-24T14:57:52.367395+00:00

**Mean pass rate: 0.643** (95% CI 0.451–0.834) over 14 items; mean flip-rate 0.163

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 6/8 | 0.29 |  |
| `define_nmr` | no_tool | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 4/8 | 0.57 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 3/8 | 0.57 | required 'paper_search': calls=0 (want 1-4), preds_ok=False |
| `known_doi_read` | direct_ref | 8/8 | 0.00 |  |
| `known_url_fetch` | direct_ref | 2/8 | 0.43 | first tool 'web_search', expected 'web_fetch' |
| `percent_calc` | compute | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 0/8 | 0.00 | required 'remember': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute` | multi_turn | 0/8 | 0.00 | routed profile 'chat', expected 'code' |
| `sota_phip` | deep_research | 5/8 | 0.29 | first tool 'web_search', expected 'deep_research' |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 4/8 | 0.14 | first tool '<none>', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 8/8 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.62 |
| `export_bibtex` | completed_in_turn:export_citations | 0.00 |
| `known_doi_read` | completed_in_turn:read_paper | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.00 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 1.00 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
