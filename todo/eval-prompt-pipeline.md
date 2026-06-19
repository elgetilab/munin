# Eval + prompt-tuning pipeline — design

Status: design done, build not started. Authored 2026-06-10.

## Goal

When we switch the deployed model (or change a prompt), run one pipeline
that tells us **quantitatively** how the model performs across our tasks,
how it compares to other model baselines, and — optionally — that
**iterates the prompts automatically** to do better. Output drives
decisions: improve the harness, tune prompts, or pick a different model.

Two parts:
- **Part A — baseline:** establish a scorecard on ALL tests for a given
  (model, prompt-set); compare against stored baselines.
- **Part B — optimizer:** automatically iterate prompts to raise the
  Part A score, using the failure feedback to propose improvements.

This is **prompt optimization**, not weight fine-tuning. Prior art to
build on (don't reinvent): **DSPy** (prompts as optimizable params),
**OPRO** ("LLMs as optimizers" — feed scores back, ask for better
prompts; our Part B is essentially this), **APE**, **PromptBreeder**.

## Findings: the prompt optimization surface

What is actually mutable (and where), from the repo:

| Surface | Location | Size / count |
|---|---|---|
| Persona system prompts | `shared/personas/*.json` → `params.system` | chat 15k, code 12.5k, research 16.6k chars |
| Tool descriptions | `backend/retrieval/mcp/schemas.py` | 139 description fields |
| Ambient / capability blocks | `backend/retrieval/capabilities.py`, `chat_service.py` (`_build_full_system_prompt`) | code-built |

**Implication:** the persona prompts are large, hand-tuned documents.
Letting an optimizer rewrite one wholesale will silently break behaviours
no check covers. The optimizer surface should be **narrow and bounded** —
see Risk 2.

## Part A — baseline / leaderboard

Generalise today's `run_eval.py` into: **N scenarios × M checks → one
scorecard per (model, prompt-set)**, persisted as a JSON artifact. Then
"switch model → run → compare to baselines" is a scorecard diff.

Mechanically modest: scenario registry, a scalar **scoring function**
(e.g. weighted mean pass-rate), and result persistence (`evals/results/<model>-<date>.json`).
The real work is the **scenario suite**, below.

Cross-model comparison reality: with one vLLM/GPU, comparing models means
redeploying vLLM with each model in turn (minutes of model load each).
That's why baselines are stored artifacts — run the NEW model once, diff
against stored scorecards; don't re-run old models each time.

## Suite — scenarios (the real bottleneck)

One scenario (pong) is far too few to optimize against without gaming it.
Target ~8–15 diverse scenarios with **robust** checks. Suite quality caps
everything downstream. Categories:

- **Behaviour** (pong-style, deterministic property checks): coding,
  clarification, tool-choice, refusal/safety, multi-turn, persona
  switching. Checks are binary properties over the event stream /
  persisted conversation. ✅ pong is #1.
- **Scientific quality** — see below (judged, not binary).
- **Search / retrieval quality** — see below (graded, not binary).

## Quality evals need a DIFFERENT methodology (important)

Behaviour checks are deterministic binary properties ("artifacts ≤ 1").
**Scientific-quality and search-quality cannot be scored that way** —
they're about correctness, citation accuracy, and retrieval relevance.
The framework must support a second check type:

- **Scientific quality:** answer correctness vs a rubric / gold answer.
  Options: LLM-as-judge against a rubric, or gold-reference questions
  with expected key facts/citations. Needs a curated Q&A set (ideally
  from real papers in our corpus) and a judge (a strong model, possibly
  external). Watch judge bias/variance.
- **Search / retrieval quality:** is the right paper retrieved, and
  ranked well? This is classic IR — **graded relevance** with metrics
  like recall@k, MRR, nDCG against labelled query→relevant-doc pairs.
  Needs a labelled query set; can reuse real `paper_search` /
  `semantic_scholar_search` queries with human/LLM relevance labels.

So the eval framework needs **two check kinds**: (1) binary property
checks (behaviour), (2) graded/judged scores (quality). Part A's
scorecard aggregates both. The optimizer (Part B) can target either, but
quality evals are noisier and judge-dependent — treat with care.

**Open data question:** do we have (or can we build) gold sets — Q&A with
expected facts/citations, and query→relevant-paper labels? Without them,
quality baselines fall back to LLM-judge-only, which is weaker.

## Part B — the optimizer loop (ambitious; risky)

**Near-term form (per decision #2): human + Claude.** Not an automated
software loop yet — run Part A, Claude reads the failure detail, proposes
a bounded prompt edit, the human reviews + applies, re-run. The automated
hill-climb below is the *later* form (self / frontier-API), kept here as
the target architecture.

OPRO-style hill-climb (automated form, deferred):

1. Run Part A → aggregate score + structured failure details.
2. An **optimizer model** reads the failures + the current (bounded)
   prompt block and proposes an edit.
3. Apply the edit; re-run the **full** suite.
4. Keep if aggregate score improves AND nothing regresses (held-out +
   full-suite gate); else discard.
5. Repeat until budget exhausted or no improvement.

## The four make-or-break risks

1. **Goodhart's law (central).** Optimizing to checks games the checks,
   not the UX. Real example from our own suite: "artifacts ≤ 1" is
   "won" by a prompt that never creates artifacts — passes the check,
   breaks the feature. Non-optional mitigations: a **held-out** scenario
   split the optimizer never sees; **diverse** checks per scenario so
   gaming one fails others; the **full-suite no-regression gate**.
2. **Narrow optimization surface.** Don't let the optimizer rewrite a
   15k-char persona prompt. Give it a dedicated, bounded **"tunable
   guidance" block** (e.g. 500–1000 chars appended to the persona
   prompt) or specific tagged sections — auditable, reversible, low
   blast radius.
3. **Cost compounds.** One eval pass ≈ scenarios × N runs × ~1 min,
   sharing one GPU with prod. An optimizer run is that × candidates ×
   iterations = hours of GPU. Hard budget + early-stop + off-peak only.
4. **Cross-model logistics.** Sequential model loads on one GPU; store
   baselines as artifacts (see Part A).

## Recommended phasing (de-risk before autonomy)

- **Phase 1 — baseline suite + scorecards.** Multi-scenario runner,
  scoring, persisted per-model baselines, AND a diverse scenario corpus
  WITH a held-out split. Delivers the "switch model → see vs baselines"
  goal with zero optimizer risk. **Build this next.**
- **Phase 2 — human-in-the-loop optimizer.** Loop proposes edits from
  failures; a human approves/rejects each before apply; re-run to
  measure. Builds trust, catches Goodhart by eye.
- **Phase 3 — autonomous, guard-railed.** Only after Phase 2 proves the
  edits are sane: held-out gate + full-suite no-regression + bounded
  surface + budget; still writes a candidate a human reviews before it
  ships. Resist starting here.

## Decisions (resolved 2026-06-10)

1. **Priority = real baselines first.** "I want real baselines that tell
   me what is going on." → Part A before Part B. Build the **corpus
   first**.
2. **Optimizer = Claude-in-the-loop (interactive), for now.** Self-
   optimization with small local models is weak; frontier-API automation
   is deferred/uncertain. Near-term workflow: run the eval → Claude reads
   the (rich) failure output → proposes prompt edits → human reviews +
   applies → re-run. This is human+Claude, not an automated OPRO loop
   yet. Automated self / frontier-API optimization = later, maybe.
   - **Implication for Part A output:** the scorecard must include
     **per-run failure detail / sample transcripts**, not just pass-
     rates, so Claude can reason about *why* a check failed.
3. **Per-model prompts with a portable fallback.** Prompts become
   model-specific with one portable default. Mechanism (Part B infra,
   not needed for Phase 1 baselines): persona/tunable-block stored as
   `default` + `by_model: {<model>: ...}`; the prompt builder selects by
   the active `VLLM_MODEL_NAME`, falling back to `default`. Phase 1
   measures the current (portable) prompt; per-model storage lands when
   we start optimizing.
4. **Quality-eval gold data: user curates** the research Q&A gold sets
   (facts + citations). Search/retrieval relevance labels still TBD.
5. **Optimization surface (still recommend):** bounded *tunable block*,
   not wholesale persona-prompt rewrites (Risk 2). Confirm when Part B
   starts.
6. **Model scope:** TBD (local vLLM variants assumed; API baselines
   optional later).

## Recommendation / next step

Build **Phase 1** next — most of the value (model-switching baselines),
low-risk, and the prerequisite for any optimizer. Corpus-first per the
decision above. The science/search judged evals slot in as the user's
gold data lands. Part B stays human+Claude until the suite is diverse
enough that Goodhart has somewhere to hide.
