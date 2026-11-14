# Routing scorecard — routing-sage-a1.5-2026-11-13

- **Routing regression scorecard (per-turn router).**
- model: `sage-a1.5`  git: `685bae2`  reps: 8  seed: 42
- tier: `anchor`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-11-14T13:01:58.481692+00:00

**Mean pass rate: 0.787** (95% CI 0.640–0.933) over 17 items; mean flip-rate 0.168

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 1 | 1.000 | 1.000 | 0.000 |
| artifact | 1 | 0.875 | 0.875 | 0.000 |
| citation_export | 1 | 0.625 | 0.625 | 0.000 |
| citation_graph | 1 | 1.000 | 1.000 | 0.000 |
| clarify | 1 | 0.125 | 0.125 | 0.000 |
| compute | 2 | 1.000 | 1.000 | 0.000 |
| corpus_qa | 1 | 0.000 | 0.000 | 0.000 |
| deep_research | 1 | 1.000 | 1.000 | 0.000 |
| direct_ref | 3 | 0.750 | 0.500 | 0.204 |
| memory | 1 | 0.625 | 0.625 | 0.000 |
| multi_turn | 1 | 1.000 | 1.000 | 0.000 |
| no_tool | 1 | 0.875 | 0.875 | 0.000 |
| robustness | 1 | 1.000 | 1.000 | 0.000 |
| simple_lookup | 1 | 1.000 | 1.000 | 0.000 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 8/8 | 0.00 |  |
| `compare_known_dois` | direct_ref | 4/8 | 0.71 | required 'source': calls=0 (want 1-99), preds_ok=False |
| `corpus_absent_abstain` | abstain | 8/8 | 0.00 |  |
| `define_nmr` | no_tool | 7/8 | 0.29 | expected zero tool calls, got ['search'] |
| `export_bibtex` | citation_export | 5/8 | 0.43 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 0/8 | 0.00 | first tool 'search', expected 'paper_search' |
| `html_poster_artifact` | artifact | 7/8 | 0.29 | forbidden tools fired: ['web_search'] |
| `known_doi_read` | direct_ref | 6/8 | 0.29 | required 'source': calls=0 (want 1-99), preds_ok=False |
| `known_url_fetch` | direct_ref | 8/8 | 0.00 |  |
| `percent_calc` | compute | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 5/8 | 0.57 | forbidden tools fired: ['search'] |
| `reroute_research_to_compute` | multi_turn | 8/8 | 0.00 |  |
| `sota_phip` | deep_research | 8/8 | 0.00 |  |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 1/8 | 0.29 | first tool 'recall', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 8/8 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 1.00 |
| `citing_papers` | profile_match | 1.00 |
| `compare_known_dois` | profile_match | 1.00 |
| `corpus_absent_abstain` | profile_match | 1.00 |
| `define_nmr` | profile_match | 0.00 |
| `export_bibtex` | completed_in_turn:export_citations | 0.62 |
| `group_corpus_qa` | calls_within_6 | 0.12 |
| `group_corpus_qa` | profile_match | 1.00 |
| `html_poster_artifact` | profile_match | 0.00 |
| `known_doi_read` | profile_match | 1.00 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute` | profile_match | 0.00 |
| `sota_phip` | calls_within_8 | 0.00 |
| `sota_phip` | profile_match | 1.00 |
| `weather_no_location` | profile_match | 1.00 |
| `weather_with_location` | profile_match | 1.00 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
