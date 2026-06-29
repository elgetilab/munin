# Routing scorecard — a5-deepresearch-postfix

- **PRE-MIGRATION regression baseline. Not a paper result.**
- model: `unknown`  git: `7fad0f2`  reps: 1  seed: 42
- tier: `paraphrase`
- persona policy: chat (A0 decision A; not expected.profile)
- timestamp: 2026-06-29T09:11:54.266041+00:00

**Mean pass rate: 0.875** (95% CI 0.713–1.000) over 16 items; mean flip-rate 0.000

### By category (phrasing-robustness — paraphrase mean + spread)

| category | n | mean | min | stdev |
|---|---|---|---|---|
| deep_research | 16 | 0.875 | 0.000 | 0.331 |

| item | category | pass | flip | top failure |
|---|---|---|---|---|
| `sota_phip__p01` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p02` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p03` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p04` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p05` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p06` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p07` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p08` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p09` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p10` | deep_research | 0/1 | 0.00 | first tool 'set_plan', expected 'deep_research' |
| `sota_phip__p11` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p12` | deep_research | 0/1 | 0.00 | first tool 'set_plan', expected 'deep_research' |
| `sota_phip__p13` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p14` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p15` | deep_research | 1/1 | 0.00 |  |
| `sota_phip__p16` | deep_research | 1/1 | 0.00 |  |

### Diagnostics (non-gating — completed-in-turn vs the permissive gate)

| item | diagnostic | rate |
|---|---|---|
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
