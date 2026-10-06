# Routing scorecard — a5-paraphrase-full

- **Paraphrase tier (224), MERGED: 161 clean from the first run + 63 re-run after a 02:00 vLLM-outage window. Not a paper result.**
- model: `unknown`  git: `c4b7cbb`  reps: 1  seed: 42
- tier: `paraphrase`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-29T00:08:11.780313+00:00

**Mean pass rate: 0.911** (95% CI 0.873–0.948) over 224 items; mean flip-rate 0.000

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 16 | 0.938 | 0.000 | 0.242 |
| artifact | 16 | 0.938 | 0.000 | 0.242 |
| citation_export | 16 | 0.875 | 0.000 | 0.331 |
| citation_graph | 16 | 1.000 | 1.000 | 0.000 |
| clarify | 16 | 0.688 | 0.000 | 0.464 |
| compute | 16 | 1.000 | 1.000 | 0.000 |
| corpus_qa | 16 | 0.875 | 0.000 | 0.331 |
| deep_research | 16 | 0.688 | 0.000 | 0.464 |
| direct_ref | 16 | 0.938 | 0.000 | 0.242 |
| memory | 16 | 1.000 | 1.000 | 0.000 |
| multi_turn | 16 | 0.812 | 0.000 | 0.390 |
| no_tool | 16 | 1.000 | 1.000 | 0.000 |
| robustness | 16 | 1.000 | 1.000 | 0.000 |
| simple_lookup | 16 | 1.000 | 1.000 | 0.000 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `citing_papers__p01` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p02` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p03` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p04` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p05` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p06` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p07` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p08` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p09` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p10` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p11` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p12` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p13` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p14` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p15` | citation_graph | 1/1 | 0.00 |  |
| `citing_papers__p16` | citation_graph | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p01` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p02` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p03` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p04` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p05` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p06` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p07` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p08` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p09` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p10` | abstain | 0/1 | 0.00 | first tool 'tool_search', expected 'paper_search' |
| `corpus_absent_abstain__p11` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p12` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p13` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p14` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p15` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p16` | abstain | 1/1 | 0.00 |  |
| `define_nmr__p01` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p02` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p03` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p04` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p05` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p06` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p07` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p08` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p09` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p10` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p11` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p12` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p13` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p14` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p15` | no_tool | 1/1 | 0.00 |  |
| `define_nmr__p16` | no_tool | 1/1 | 0.00 |  |
| `export_bibtex__p01` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p02` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p03` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p04` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p05` | citation_export | 0/1 | 0.00 |  |
| `export_bibtex__p06` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p07` | citation_export | 0/1 | 0.00 | required 'export_citations': calls=0 (want 1-99), preds_ok=False |
| `export_bibtex__p08` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p09` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p10` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p11` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p12` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p13` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p14` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p15` | citation_export | 1/1 | 0.00 |  |
| `export_bibtex__p16` | citation_export | 1/1 | 0.00 |  |
| `group_corpus_qa__p01` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p02` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p03` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p04` | corpus_qa | 0/1 | 0.00 | first tool 'ask_clarification', expected 'paper_search' |
| `group_corpus_qa__p05` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p06` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p07` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p08` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p09` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p10` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p11` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p12` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p13` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p14` | corpus_qa | 0/1 | 0.00 | first tool 'set_plan', expected 'paper_search' |
| `group_corpus_qa__p15` | corpus_qa | 1/1 | 0.00 |  |
| `group_corpus_qa__p16` | corpus_qa | 1/1 | 0.00 |  |
| `html_poster_artifact__p01` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p02` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p03` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p04` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p05` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p06` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p07` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p08` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p09` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p10` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p11` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p12` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p13` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p14` | artifact | 1/1 | 0.00 |  |
| `html_poster_artifact__p15` | artifact | 0/1 | 0.00 | required 'create_artifact': calls=0 (want 1-99), preds_ok=False |
| `html_poster_artifact__p16` | artifact | 1/1 | 0.00 |  |
| `known_doi_read__p01` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p02` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p03` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p04` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p05` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p06` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p07` | direct_ref | 1/1 | 0.00 |  |
| `known_doi_read__p08` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p01` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p02` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p03` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p04` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p05` | direct_ref | 0/1 | 0.00 | first tool 'read_paper', expected 'web_fetch' |
| `known_url_fetch__p06` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p07` | direct_ref | 1/1 | 0.00 |  |
| `known_url_fetch__p08` | direct_ref | 1/1 | 0.00 |  |
| `percent_calc__p01` | compute | 1/1 | 0.00 |  |
| `percent_calc__p02` | compute | 1/1 | 0.00 |  |
| `percent_calc__p03` | compute | 1/1 | 0.00 |  |
| `percent_calc__p04` | compute | 1/1 | 0.00 |  |
| `percent_calc__p05` | compute | 1/1 | 0.00 |  |
| `percent_calc__p06` | compute | 1/1 | 0.00 |  |
| `percent_calc__p07` | compute | 1/1 | 0.00 |  |
| `percent_calc__p08` | compute | 1/1 | 0.00 |  |
| `remember_research_area__p01` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p02` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p03` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p04` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p05` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p06` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p07` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p08` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p09` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p10` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p11` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p12` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p13` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p14` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p15` | memory | 1/1 | 0.00 |  |
| `remember_research_area__p16` | memory | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p01` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p02` | multi_turn | 0/1 | 0.00 | required 'run_python': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute__p03` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p04` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p05` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p06` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p07` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p08` | multi_turn | 0/1 | 0.00 | required 'run_python': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute__p09` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p10` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p11` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p12` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p13` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p14` | multi_turn | 1/1 | 0.00 |  |
| `reroute_research_to_compute__p15` | multi_turn | 0/1 | 0.00 | required 'run_python': calls=0 (want 1-99), preds_ok=False |
| `reroute_research_to_compute__p16` | multi_turn | 1/1 | 0.00 |  |
| `sota_phip__p01` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p02` | deep_research | 0/1 | 0.00 | first tool 'set_plan', expected 'deep_research' |
| `sota_phip__p03` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p04` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p05` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p06` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p07` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p08` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p09` | deep_research | 0/1 | 0.00 | error SSE: ['vLLM returned 400: {"error":{"message":"This model\'s max |
| `sota_phip__p10` | deep_research | 0/1 | 0.00 | first tool 'set_plan', expected 'deep_research' |
| `sota_phip__p11` | deep_research | 0/1 | 0.00 | error SSE: ['vLLM returned 400: {"error":{"message":"This model\'s max |
| `sota_phip__p12` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p13` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p14` | deep_research | 0/1 | 0.00 | error SSE: ['vLLM returned 400: {"error":{"message":"This model\'s max |
| `sota_phip__p15` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p16` | deep_research | 1/1 | 0.00 |  |
| `unit_convert_physical__p01` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p02` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p03` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p04` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p05` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p06` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p07` | compute | 1/1 | 0.00 |  |
| `unit_convert_physical__p08` | compute | 1/1 | 0.00 |  |
| `weather_no_location__p01` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p02` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p03` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p04` | clarify | 0/1 | 0.00 | first tool '<none>', expected 'ask_clarification' |
| `weather_no_location__p05` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p06` | clarify | 0/1 | 0.00 | first tool '<none>', expected 'ask_clarification' |
| `weather_no_location__p07` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p08` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p09` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p10` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p11` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p12` | clarify | 0/1 | 0.00 | first tool 'recall', expected 'ask_clarification' |
| `weather_no_location__p13` | clarify | 1/1 | 0.00 |  |
| `weather_no_location__p14` | clarify | 0/1 | 0.00 | first tool '<none>', expected 'ask_clarification' |
| `weather_no_location__p15` | clarify | 0/1 | 0.00 | first tool 'web_search', expected 'ask_clarification' |
| `weather_no_location__p16` | clarify | 1/1 | 0.00 |  |
| `weather_paraphrase__p01` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p02` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p03` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p04` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p05` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p06` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p07` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p08` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p09` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p10` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p11` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p12` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p13` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p14` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p15` | robustness | 1/1 | 0.00 |  |
| `weather_paraphrase__p16` | robustness | 1/1 | 0.00 |  |
| `weather_with_location__p01` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p02` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p03` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p04` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p05` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p06` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p07` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p08` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p09` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p10` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p11` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p12` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p13` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p14` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p15` | simple_lookup | 1/1 | 0.00 |  |
| `weather_with_location__p16` | simple_lookup | 1/1 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
| `citing_papers__p01` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p01` | profile_match | 1.00 |
| `citing_papers__p02` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p02` | profile_match | 1.00 |
| `citing_papers__p03` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p03` | profile_match | 1.00 |
| `citing_papers__p04` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p04` | profile_match | 1.00 |
| `citing_papers__p05` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p05` | profile_match | 1.00 |
| `citing_papers__p06` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p06` | profile_match | 1.00 |
| `citing_papers__p07` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p07` | profile_match | 1.00 |
| `citing_papers__p08` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p08` | profile_match | 1.00 |
| `citing_papers__p09` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p09` | profile_match | 1.00 |
| `citing_papers__p10` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p10` | profile_match | 0.00 |
| `citing_papers__p11` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p11` | profile_match | 1.00 |
| `citing_papers__p12` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p12` | profile_match | 1.00 |
| `citing_papers__p13` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p13` | profile_match | 1.00 |
| `citing_papers__p14` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p14` | profile_match | 1.00 |
| `citing_papers__p15` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p15` | profile_match | 1.00 |
| `citing_papers__p16` | completed_in_turn:get_citations | 1.00 |
| `citing_papers__p16` | profile_match | 1.00 |
| `corpus_absent_abstain__p01` | profile_match | 1.00 |
| `corpus_absent_abstain__p02` | profile_match | 0.00 |
| `corpus_absent_abstain__p03` | profile_match | 1.00 |
| `corpus_absent_abstain__p04` | profile_match | 1.00 |
| `corpus_absent_abstain__p05` | profile_match | 1.00 |
| `corpus_absent_abstain__p06` | profile_match | 1.00 |
| `corpus_absent_abstain__p07` | profile_match | 1.00 |
| `corpus_absent_abstain__p08` | profile_match | 1.00 |
| `corpus_absent_abstain__p09` | profile_match | 1.00 |
| `corpus_absent_abstain__p10` | profile_match | 0.00 |
| `corpus_absent_abstain__p11` | profile_match | 1.00 |
| `corpus_absent_abstain__p12` | profile_match | 1.00 |
| `corpus_absent_abstain__p13` | profile_match | 1.00 |
| `corpus_absent_abstain__p14` | profile_match | 1.00 |
| `corpus_absent_abstain__p15` | profile_match | 1.00 |
| `corpus_absent_abstain__p16` | profile_match | 1.00 |
| `define_nmr__p01` | profile_match | 1.00 |
| `define_nmr__p02` | profile_match | 1.00 |
| `define_nmr__p03` | profile_match | 1.00 |
| `define_nmr__p04` | profile_match | 1.00 |
| `define_nmr__p05` | profile_match | 1.00 |
| `define_nmr__p06` | profile_match | 1.00 |
| `define_nmr__p07` | profile_match | 1.00 |
| `define_nmr__p08` | profile_match | 1.00 |
| `define_nmr__p09` | profile_match | 1.00 |
| `define_nmr__p10` | profile_match | 1.00 |
| `define_nmr__p11` | profile_match | 1.00 |
| `define_nmr__p12` | profile_match | 1.00 |
| `define_nmr__p13` | profile_match | 0.00 |
| `define_nmr__p14` | profile_match | 1.00 |
| `define_nmr__p15` | profile_match | 1.00 |
| `define_nmr__p16` | profile_match | 1.00 |
| `export_bibtex__p01` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p02` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p03` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p04` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p05` | completed_in_turn:export_citations | 0.00 |
| `export_bibtex__p06` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p07` | completed_in_turn:export_citations | 0.00 |
| `export_bibtex__p08` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p09` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p10` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p11` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p12` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p13` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p14` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p15` | completed_in_turn:export_citations | 1.00 |
| `export_bibtex__p16` | completed_in_turn:export_citations | 1.00 |
| `group_corpus_qa__p01` | calls_within_6 | 0.00 |
| `group_corpus_qa__p01` | profile_match | 1.00 |
| `group_corpus_qa__p02` | calls_within_6 | 1.00 |
| `group_corpus_qa__p02` | profile_match | 1.00 |
| `group_corpus_qa__p03` | calls_within_6 | 0.00 |
| `group_corpus_qa__p03` | profile_match | 1.00 |
| `group_corpus_qa__p04` | calls_within_6 | 1.00 |
| `group_corpus_qa__p04` | profile_match | 1.00 |
| `group_corpus_qa__p05` | calls_within_6 | 1.00 |
| `group_corpus_qa__p05` | profile_match | 1.00 |
| `group_corpus_qa__p06` | calls_within_6 | 0.00 |
| `group_corpus_qa__p06` | profile_match | 1.00 |
| `group_corpus_qa__p07` | calls_within_6 | 0.00 |
| `group_corpus_qa__p07` | profile_match | 1.00 |
| `group_corpus_qa__p08` | calls_within_6 | 1.00 |
| `group_corpus_qa__p08` | profile_match | 1.00 |
| `group_corpus_qa__p09` | calls_within_6 | 0.00 |
| `group_corpus_qa__p09` | profile_match | 1.00 |
| `group_corpus_qa__p10` | calls_within_6 | 1.00 |
| `group_corpus_qa__p10` | profile_match | 1.00 |
| `group_corpus_qa__p11` | calls_within_6 | 0.00 |
| `group_corpus_qa__p11` | profile_match | 1.00 |
| `group_corpus_qa__p12` | calls_within_6 | 0.00 |
| `group_corpus_qa__p12` | profile_match | 1.00 |
| `group_corpus_qa__p13` | calls_within_6 | 0.00 |
| `group_corpus_qa__p13` | profile_match | 1.00 |
| `group_corpus_qa__p14` | calls_within_6 | 0.00 |
| `group_corpus_qa__p14` | profile_match | 1.00 |
| `group_corpus_qa__p15` | calls_within_6 | 1.00 |
| `group_corpus_qa__p15` | profile_match | 1.00 |
| `group_corpus_qa__p16` | calls_within_6 | 1.00 |
| `group_corpus_qa__p16` | profile_match | 1.00 |
| `html_poster_artifact__p01` | profile_match | 0.00 |
| `html_poster_artifact__p02` | profile_match | 0.00 |
| `html_poster_artifact__p03` | profile_match | 0.00 |
| `html_poster_artifact__p04` | profile_match | 0.00 |
| `html_poster_artifact__p05` | profile_match | 0.00 |
| `html_poster_artifact__p06` | profile_match | 0.00 |
| `html_poster_artifact__p07` | profile_match | 0.00 |
| `html_poster_artifact__p08` | profile_match | 1.00 |
| `html_poster_artifact__p09` | profile_match | 0.00 |
| `html_poster_artifact__p10` | profile_match | 0.00 |
| `html_poster_artifact__p11` | profile_match | 1.00 |
| `html_poster_artifact__p12` | profile_match | 0.00 |
| `html_poster_artifact__p13` | profile_match | 0.00 |
| `html_poster_artifact__p14` | profile_match | 0.00 |
| `html_poster_artifact__p15` | profile_match | 0.00 |
| `html_poster_artifact__p16` | profile_match | 0.00 |
| `known_doi_read__p01` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p01` | profile_match | 1.00 |
| `known_doi_read__p02` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p02` | profile_match | 1.00 |
| `known_doi_read__p03` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p03` | profile_match | 1.00 |
| `known_doi_read__p04` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p04` | profile_match | 1.00 |
| `known_doi_read__p05` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p05` | profile_match | 1.00 |
| `known_doi_read__p06` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p06` | profile_match | 1.00 |
| `known_doi_read__p07` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p07` | profile_match | 1.00 |
| `known_doi_read__p08` | completed_in_turn:read_paper | 1.00 |
| `known_doi_read__p08` | profile_match | 0.00 |
| `remember_research_area__p01` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p02` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p03` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p04` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p05` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p06` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p07` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p08` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p09` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p10` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p11` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p12` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p13` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p14` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p15` | completed_in_turn:remember | 1.00 |
| `remember_research_area__p16` | completed_in_turn:remember | 1.00 |
| `reroute_research_to_compute__p01` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p01` | profile_match | 1.00 |
| `reroute_research_to_compute__p02` | completed_in_turn:run_python | 0.00 |
| `reroute_research_to_compute__p02` | profile_match | 1.00 |
| `reroute_research_to_compute__p03` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p03` | profile_match | 1.00 |
| `reroute_research_to_compute__p04` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p04` | profile_match | 1.00 |
| `reroute_research_to_compute__p05` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p05` | profile_match | 1.00 |
| `reroute_research_to_compute__p06` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p06` | profile_match | 1.00 |
| `reroute_research_to_compute__p07` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p07` | profile_match | 1.00 |
| `reroute_research_to_compute__p08` | completed_in_turn:run_python | 0.00 |
| `reroute_research_to_compute__p08` | profile_match | 1.00 |
| `reroute_research_to_compute__p09` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p09` | profile_match | 1.00 |
| `reroute_research_to_compute__p10` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p10` | profile_match | 1.00 |
| `reroute_research_to_compute__p11` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p11` | profile_match | 1.00 |
| `reroute_research_to_compute__p12` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p12` | profile_match | 1.00 |
| `reroute_research_to_compute__p13` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p13` | profile_match | 1.00 |
| `reroute_research_to_compute__p14` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p14` | profile_match | 1.00 |
| `reroute_research_to_compute__p15` | completed_in_turn:run_python | 0.00 |
| `reroute_research_to_compute__p15` | profile_match | 1.00 |
| `reroute_research_to_compute__p16` | completed_in_turn:run_python | 1.00 |
| `reroute_research_to_compute__p16` | profile_match | 1.00 |
| `sota_phip__p01` | calls_within_8 | 0.00 |
| `sota_phip__p01` | profile_match | 1.00 |
| `sota_phip__p02` | calls_within_8 | 0.00 |
| `sota_phip__p02` | profile_match | 1.00 |
| `sota_phip__p03` | calls_within_8 | 0.00 |
| `sota_phip__p03` | profile_match | 1.00 |
| `sota_phip__p04` | calls_within_8 | 0.00 |
| `sota_phip__p04` | profile_match | 1.00 |
| `sota_phip__p05` | calls_within_8 | 0.00 |
| `sota_phip__p05` | profile_match | 1.00 |
| `sota_phip__p06` | calls_within_8 | 0.00 |
| `sota_phip__p06` | profile_match | 1.00 |
| `sota_phip__p07` | calls_within_8 | 0.00 |
| `sota_phip__p07` | profile_match | 1.00 |
| `sota_phip__p08` | calls_within_8 | 0.00 |
| `sota_phip__p08` | profile_match | 1.00 |
| `sota_phip__p09` | calls_within_8 | 0.00 |
| `sota_phip__p09` | profile_match | 1.00 |
| `sota_phip__p10` | calls_within_8 | 0.00 |
| `sota_phip__p10` | profile_match | 1.00 |
| `sota_phip__p11` | calls_within_8 | 0.00 |
| `sota_phip__p11` | profile_match | 1.00 |
| `sota_phip__p12` | calls_within_8 | 0.00 |
| `sota_phip__p12` | profile_match | 1.00 |
| `sota_phip__p13` | calls_within_8 | 0.00 |
| `sota_phip__p13` | profile_match | 1.00 |
| `sota_phip__p14` | calls_within_8 | 0.00 |
| `sota_phip__p14` | profile_match | 1.00 |
| `sota_phip__p15` | calls_within_8 | 0.00 |
| `sota_phip__p15` | profile_match | 1.00 |
| `sota_phip__p16` | calls_within_8 | 0.00 |
| `sota_phip__p16` | profile_match | 1.00 |
| `weather_no_location__p01` | profile_match | 1.00 |
| `weather_no_location__p02` | profile_match | 1.00 |
| `weather_no_location__p03` | profile_match | 1.00 |
| `weather_no_location__p04` | profile_match | 1.00 |
| `weather_no_location__p05` | profile_match | 1.00 |
| `weather_no_location__p06` | profile_match | 1.00 |
| `weather_no_location__p07` | profile_match | 1.00 |
| `weather_no_location__p08` | profile_match | 1.00 |
| `weather_no_location__p09` | profile_match | 1.00 |
| `weather_no_location__p10` | profile_match | 1.00 |
| `weather_no_location__p11` | profile_match | 1.00 |
| `weather_no_location__p12` | profile_match | 1.00 |
| `weather_no_location__p13` | profile_match | 1.00 |
| `weather_no_location__p14` | profile_match | 1.00 |
| `weather_no_location__p15` | profile_match | 1.00 |
| `weather_no_location__p16` | profile_match | 1.00 |
| `weather_with_location__p01` | profile_match | 1.00 |
| `weather_with_location__p02` | profile_match | 1.00 |
| `weather_with_location__p03` | profile_match | 1.00 |
| `weather_with_location__p04` | profile_match | 1.00 |
| `weather_with_location__p05` | profile_match | 1.00 |
| `weather_with_location__p06` | profile_match | 1.00 |
| `weather_with_location__p07` | profile_match | 1.00 |
| `weather_with_location__p08` | profile_match | 1.00 |
| `weather_with_location__p09` | profile_match | 1.00 |
| `weather_with_location__p10` | profile_match | 1.00 |
| `weather_with_location__p11` | profile_match | 1.00 |
| `weather_with_location__p12` | profile_match | 1.00 |
| `weather_with_location__p13` | profile_match | 1.00 |
| `weather_with_location__p14` | profile_match | 1.00 |
| `weather_with_location__p15` | profile_match | 1.00 |
| `weather_with_location__p16` | profile_match | 1.00 |
