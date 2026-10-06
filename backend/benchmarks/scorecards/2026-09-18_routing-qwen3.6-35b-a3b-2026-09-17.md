# Routing scorecard — routing-qwen3.6-35b-a3b-2026-09-17

- **Routing regression scorecard, run after the router went live (2026-07-08). Note corrected 2026-10: the runner stamped this run as a pre-migration baseline and not a paper result, which was wrong for it.**
- model: `qwen3.6-35b-a3b`  git: `6983e86`  reps: 8  seed: 42
- tier: `anchor`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-09-18T17:11:22.892551+00:00

**Mean pass rate: 0.816** (95% CI 0.650–0.982) over 17 items; mean flip-rate 0.076

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 1 | 1.000 | 1.000 | 0.000 |
| artifact | 1 | 1.000 | 1.000 | 0.000 |
| citation_export | 1 | 1.000 | 1.000 | 0.000 |
| citation_graph | 1 | 0.875 | 0.875 | 0.000 |
| clarify | 1 | 1.000 | 1.000 | 0.000 |
| compute | 2 | 1.000 | 1.000 | 0.000 |
| corpus_qa | 1 | 0.000 | 0.000 | 0.000 |
| deep_research | 1 | 0.000 | 0.000 | 0.000 |
| direct_ref | 3 | 0.667 | 0.250 | 0.312 |
| memory | 1 | 1.000 | 1.000 | 0.000 |
| multi_turn | 1 | 1.000 | 1.000 | 0.000 |
| no_tool | 1 | 1.000 | 1.000 | 0.000 |
| robustness | 1 | 1.000 | 1.000 | 0.000 |
| simple_lookup | 1 | 1.000 | 1.000 | 0.000 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 7/8 | 0.29 | required 'get_citations': calls=0 (want 1-99), preds_ok=False |
| `compare_known_dois` | direct_ref | 2/8 | 0.43 | required 'source': calls=3 (want 1-99), preds_ok=False |
| `corpus_absent_abstain` | abstain | 8/8 | 0.00 |  |
| `define_nmr` | no_tool | 8/8 | 0.00 |  |
| `export_bibtex` | citation_export | 8/8 | 0.00 |  |
| `group_corpus_qa` | corpus_qa | 0/8 | 0.00 | first tool 'search', expected 'paper_search' |
| `html_poster_artifact` | artifact | 8/8 | 0.00 |  |
| `known_doi_read` | direct_ref | 6/8 | 0.57 | required 'source': calls=0 (want 1-99), preds_ok=False |
| `known_url_fetch` | direct_ref | 8/8 | 0.00 |  |
| `percent_calc` | compute | 8/8 | 0.00 |  |
| `remember_research_area` | memory | 8/8 | 0.00 |  |
| `reroute_research_to_compute` | multi_turn | 8/8 | 0.00 |  |
| `sota_phip` | deep_research | 0/8 | 0.00 | first tool 'search', expected 'deep_research' |
| `unit_convert_physical` | compute | 8/8 | 0.00 |  |
| `weather_no_location` | clarify | 8/8 | 0.00 |  |
| `weather_paraphrase` | robustness | 8/8 | 0.00 |  |
| `weather_with_location` | simple_lookup | 8/8 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.88 |
| `citing_papers` | profile_match | 1.00 |
| `compare_known_dois` | profile_match | 1.00 |
| `corpus_absent_abstain` | profile_match | 1.00 |
| `define_nmr` | profile_match | 0.00 |
| `export_bibtex` | completed_in_turn:export_citations | 1.00 |
| `group_corpus_qa` | calls_within_6 | 0.00 |
| `group_corpus_qa` | profile_match | 1.00 |
| `html_poster_artifact` | profile_match | 0.00 |
| `known_doi_read` | profile_match | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 1.00 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute` | profile_match | 0.00 |
| `sota_phip` | calls_within_8 | 0.00 |
| `sota_phip` | profile_match | 1.00 |
| `weather_no_location` | profile_match | 1.00 |
| `weather_with_location` | profile_match | 1.00 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
