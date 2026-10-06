# Eval scenarios — catalog

The behaviour/quality scenario corpus for the eval harness. Methodology,
risks, and the optimizer plan live in
[eval-prompt-pipeline.md](eval-prompt-pipeline.md); this file is the
catalog of *what we test*.

Harness: `backend/retrieval/evals/run_eval.py` (on-demand, in-container).
Run: `docker exec munin-retrieval python /app/evals/run_eval.py
--scenario <name> --runs 8`.

## Two check kinds

- **Binary property** — deterministic, evaluated over the event stream /
  persisted conversation (e.g. "artifacts ≤ 1"). Behaviour scenarios.
- **Judged / graded** — quality scored by an LLM-judge against a rubric
  and/or gold references (correctness, citation accuracy, retrieval
  relevance). Scientific & search scenarios. **Gold data: user curates.**

Status legend: ✅ built · 🟡 partial · ⬜ proposed · 🔒 held-out (optimizer
never sees — Goodhart guard).

## Behaviour scenarios

| Scenario | Probes | Checks (binary unless noted) | Status |
|---|---|---|---|
| `pong` | coding + artifact + clarification | artifacts ≤ 1 · every artifact text/html · no phantom-URL warning · clarify-before-work · delegated→code OR html game · no duplicated answer block (fuzzy) | ✅ |
| `clarify_ambiguous` | "help me with my paper" → ask first, don't tool-spam | asks clarification · no tool/artifact before it | ⬜ |
| `simple_factual` | "capital of France?" → answer directly | no tool calls · correct (judge/keyword) | ⬜ |
| `research_toolchoice` | "recent advances in X" → use paper/scholar search | `paper_search`/`semantic_scholar_search` invoked · ≥1 real citation | ⬜ |
| `persona_switch_research` | in chat, ask for a multi-paper review → delegate | `delegated`→`research` | ⬜ |
| `code_debug` | "fix this Python bug" → code path | `run_python` used · artifacts ≤ 1 | ⬜ |
| `refusal` | a request that should be declined | appropriate refusal (judge) | ⬜ 🔒 |

(Open: confirm the in/out list and add any missing probes — this is the
corpus-first deliverable.)

## Quality scenarios (judged — pending gold data)

| Track | Probes | Scoring | Status |
|---|---|---|---|
| Scientific quality | answer correctness + citation accuracy on curated Q&A from the corpus | LLM-judge vs rubric, or gold key-facts/citations | ⬜ (user curating gold) |
| Search / retrieval quality | is the right paper retrieved and ranked | graded relevance: recall@k / MRR / nDCG vs query→relevant-doc labels | ⬜ (labels TBD) |

## Pong baseline (2026-06-10, 8/8 runs)

```
artifacts <= 1 ............................... 3/8   ← model over-produces (cross-turn)
every artifact is text/html .................. 5/8
no phantom-URL warning ....................... 4/8   ← half hallucinate download URLs
clarify-before-work .......................... 8/8   ✓
delegated->code OR produced an html game ..... 5/8
no duplicated answer block (fuzzy) ........... 8/8   ✓ (heuristic may under-detect)
```
Reads as: deterministic fixes hold; the live model-behaviour problems are
over-producing artifacts and hallucinated download URLs — these are the
prompt-tuning targets. Re-run after any prompt change to measure.

## Appendix — pong investigation notes (absorbed from PONG-BUG-TRIAGE.md)

Source chat `d28ef78e` ("code me a pong game"). Findings worth keeping so
they aren't re-investigated:

- **Fixed (deterministic, committed):** HTML artifact downloaded as
  `.txt` (`ac30095`); no "working" spinner while the model wrote an
  artifact (`be93620`); 3× same-title `create_artifact` in one response
  → redundant artifacts (`d53fec1`).
- **NOT a bug:** "ask_clarification should drop sibling tool calls" — it
  already does. The intercept (`chat_service.py` ~2293) returns before
  the single `_run_tool_calls` site; the pong artifacts ran in an
  *earlier* iteration. The "artifacts before clarification" is model
  sequencing across iterations → covered by the eval, not a code fix.
- **Model-behaviour (eval, not unit tests):** no persona switch,
  duplicate answer, too-many artifacts (cross-iteration), hallucinated
  download URLs.
