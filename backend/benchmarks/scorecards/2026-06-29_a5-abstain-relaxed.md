# Routing scorecard — a5-abstain-relaxed

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `unknown`  git: `3b2d991`  reps: 1  seed: 42
- tier: `paraphrase`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-29T10:45:19.322657+00:00

**Mean pass rate: 1.000** (95% CI 1.000–1.000) over 16 items; mean flip-rate 0.000

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| abstain | 16 | 1.000 | 1.000 | 0.000 |

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
| `corpus_absent_abstain__p10` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p11` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p12` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p13` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p14` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p15` | abstain | 1/1 | 0.00 |  |
| `corpus_absent_abstain__p16` | abstain | 1/1 | 0.00 |  |

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
