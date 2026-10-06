# Routing scorecard — a5-anchor-baseline

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `unknown`  git: `a207539`  reps: 5  seed: 42
- tier: `anchor`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-28T12:02:38.964749+00:00

**Mean pass rate: 0.800** (95% CI 0.670–0.930) over 16 items; mean flip-rate 0.203

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 1 | 1.000 | 1.000 | 0.000 |
| artifact | 1 | 0.800 | 0.800 | 0.000 |
| citation_export | 1 | 0.600 | 0.600 | 0.000 |
| citation_graph | 1 | 0.800 | 0.800 | 0.000 |
| clarify | 1 | 0.400 | 0.400 | 0.000 |
| compute | 2 | 1.000 | 1.000 | 0.000 |
| corpus_qa | 1 | 1.000 | 1.000 | 0.000 |
| deep_research | 1 | 1.000 | 1.000 | 0.000 |
| direct_ref | 2 | 0.500 | 0.400 | 0.100 |
| memory | 1 | 0.200 | 0.200 | 0.000 |
| multi_turn | 1 | 1.000 | 1.000 | 0.000 |
| no_tool | 1 | 1.000 | 1.000 | 0.000 |
| robustness | 1 | 1.000 | 1.000 | 0.000 |
| simple_lookup | 1 | 1.000 | 1.000 | 0.000 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers` | citation_graph | 4/5 | 0.25 | required 'get_citations': calls=0 (want 1-99), preds_ok=False |
| `corpus_absent_abstain` | abstain | 5/5 | 0.00 |  |
| `define_nmr` | no_tool | 5/5 | 0.00 |  |
| `export_bibtex` | citation_export | 3/5 | 0.75 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `group_corpus_qa` | corpus_qa | 5/5 | 0.00 |  |
| `html_poster_artifact` | artifact | 4/5 | 0.25 | required 'create_artifact': calls=0 (want 1-99), preds_ok=False |
| `known_doi_read` | direct_ref | 3/5 | 0.25 | forbidden tools fired: ['semantic_scholar_search'] |
| `known_url_fetch` | direct_ref | 2/5 | 0.75 | first tool 'read_paper', expected 'web_fetch' |
| `percent_calc` | compute | 5/5 | 0.00 |  |
| `remember_research_area` | memory | 1/5 | 0.25 | required 'remember': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute` | multi_turn | 5/5 | 0.00 |  |
| `sota_phip` | deep_research | 5/5 | 0.00 |  |
| `unit_convert_physical` | compute | 5/5 | 0.00 |  |
| `weather_no_location` | clarify | 2/5 | 0.75 | first tool '<none>', expected 'ask_clarification' |
| `weather_paraphrase` | robustness | 5/5 | 0.00 |  |
| `weather_with_location` | simple_lookup | 5/5 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers` | completed_in_turn:get_citations | 0.80 |
| `citing_papers` | profile_match | 1.00 |
| `corpus_absent_abstain` | profile_match | 1.00 |
| `define_nmr` | profile_match | 0.00 |
| `export_bibtex` | completed_in_turn:export_citations | 0.60 |
| `group_corpus_qa` | calls_within_6 | 0.20 |
| `group_corpus_qa` | profile_match | 1.00 |
| `html_poster_artifact` | profile_match | 0.00 |
| `known_doi_read` | completed_in_turn:read_paper | 0.80 |
| `known_doi_read` | profile_match | 1.00 |
| `remember_research_area` | completed_in_turn:remember | 0.20 |
| `reroute_research_to_compute` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute` | profile_match | 0.00 |
| `sota_phip` | calls_within_8 | 0.00 |
| `sota_phip` | profile_match | 1.00 |
| `weather_no_location` | profile_match | 1.00 |
| `weather_with_location` | profile_match | 1.00 |

### Skipped at A0

- `web_search_degraded` — needs inject_tool_result (deferred to A2)
