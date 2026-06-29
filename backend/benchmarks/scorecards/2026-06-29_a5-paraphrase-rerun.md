# Routing scorecard — a5-paraphrase-rerun

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `unknown`  git: `3fbb68f`  reps: 1  seed: 42
- tier: `paraphrase`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-29T07:19:04.071004+00:00

**Mean pass rate: 0.921** (95% CI 0.854–0.987) over 63 items; mean flip-rate 0.000

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 16 | 0.938 | 0.000 | 0.242 |
| artifact | 15 | 0.933 | 0.000 | 0.249 |
| multi_turn | 16 | 0.812 | 0.000 | 0.390 |
| robustness | 16 | 1.000 | 1.000 | 0.000 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
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

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
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
