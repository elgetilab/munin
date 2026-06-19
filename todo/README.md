# todo/ — working roadmap

Planning + design docs for in-flight and upcoming work. These are
**working documents** (rougher than `shared/docs/`, which holds canonical
contracts). As an item ships, its design moves to a commit / canonical
doc and the entry here is marked done or removed.

Status legend: ⬜ not started · 🟡 in progress · ✅ done · ❄️ parked

## Roadmap

| Item | Doc | Status |
|---|---|---|
| Eval + prompt-tuning pipeline (Parts A/B) | [eval-prompt-pipeline.md](eval-prompt-pipeline.md) | ⬜ design done, build not started |
| Eval scenario catalog (the corpus) | [eval-scenarios.md](eval-scenarios.md) | 🟡 pong built; ~6 behaviour proposed |
| → Phase 1: multi-scenario baseline + scorecards | eval-prompt-pipeline.md §Part A | ⬜ |
| → Behaviour scenario suite (pong is #1) | eval-scenarios.md | 🟡 1 of ~7 |
| → Scientific-quality baselines (judged) | eval-scenarios.md §Quality | ⬜ user curating gold |
| → Search / retrieval-quality baselines (graded) | eval-scenarios.md §Quality | ⬜ labels TBD |
| → Phase 2: human-in-the-loop prompt optimizer | eval-prompt-pipeline.md §Part B | ⬜ |
| → Phase 3: autonomous optimizer (guard-railed) | eval-prompt-pipeline.md §Part B | ⬜ |

## Already shipped (context)

- Pong-chat bug triage — 3 deterministic fixes committed + deployed
  (`e22d1a4` .txt download, `6618045` spinner, `eff7ee3` artifact dedup)
  and the first eval scenario built. Lasting findings + the baseline are
  absorbed into [eval-scenarios.md](eval-scenarios.md); the
  `PONG-BUG-TRIAGE.md` scratchpad has been deleted. The eval harness it
  produced (`backend/retrieval/evals/run_eval.py`) is Phase 1's seed.

## Conventions

- One concern per doc; link between them.
- Keep "Open decisions" sections live — they're what we resolve before
  building each phase.
- Convert relative dates to absolute when noting them.
