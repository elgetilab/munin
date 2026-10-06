# Routing scorecard — routing-A3-gate-v3

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `qwen3.6-35b-a3b`  git: `a060b9b`  reps: 8  seed: 42
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-25T11:58:54.560907+00:00

**Mean pass rate: 0.696** (95% CI 0.524–0.869) over 14 items; mean flip-rate 0.235

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 6/8 | 0.43 |  |
| `define_nmr` | no_tool | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 5/8 | 0.57 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 6/8 | 0.43 |  |
| `known_doi_read` | direct_ref | 8/8 | 0.00 |  |
| `known_url_fetch` | direct_ref | 0/8 | 0.00 | first tool 'web_search', expected 'web_fetch' |
| `percent_calc` | compute | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 1/8 | 0.29 | required 'remember': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute` | multi_turn | 2/8 | 0.43 | forbidden tools fired: ['paper_search', 'paper_search', 'paper_search' |
| `sota_phip` | deep_research | 6/8 | 0.29 | error SSE: ['vLLM returned 400: {"error":{"message":"This model\'s max |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 5/8 | 0.57 | first tool 'web_search', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 7/8 | 0.29 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.62 |
| `citing_papers` | profile_match | 1.00 |
| `define_nmr` | profile_match | 0.00 |
| `export_bibtex` | completed_in_turn:export_citations | 0.00 |
| `group_corpus_qa` | profile_match | 1.00 |
| `known_doi_read` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read` | profile_match | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.12 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute` | profile_match | 0.00 |
| `sota_phip` | profile_match | 1.00 |
| `weather_no_location` | profile_match | 1.00 |
| `weather_with_location` | profile_match | 1.00 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
