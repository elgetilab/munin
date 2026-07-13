# Scorecard — Track D: harness ablation (bare / RAG / agentic)

- generator: bare + RAG via direct vLLM (`qwen3.6-35b-a3b`, :8000, no tools,
  concurrency=1); agentic via live `/api/chat/completions`. | 100 in-corpus
  LitQA2 MCQ questions (seed 7) | 2026-07-13 | git `dfe89f8`

## Accuracy — the headline (all paired, p ~ 0)

| arm | accuracy | precision-of-attempted | abstain | mean cost |
|---|---|---|---|---|
| **RAG** (naive top-5) | **0.150** | 0.52 | 0.70 | 9.6s, 0 tools |
| **bare** (parametric) | **0.320** | 0.48 | 0.25 | 14.1s, 0 tools |
| **agentic** (harness) | **0.560** | **0.86** | 0.31 | 118s, 16 tools |

Paired-bootstrap deltas: **RAG - bare = -0.170 [-0.26,-0.08]**; **agentic - RAG =
+0.410 [0.31,0.51]**; **agentic - bare = +0.240 [0.11,0.37]** (all p~0).

**The harness's value is real, large, and significant** - +0.24 accuracy over the
bare model, and it dominates on precision (0.86 vs 0.48: it answers MORE and
guesses wrong far less - 9 wrong vs bare's 35), at ~8x the wall-clock (118s vs
14s) + 16 tool calls.

**Naive RAG HURTS (below bare).** Verified from the answers: with imperfect top-5
retrieval the model ANCHORS on the retrieved abstracts and abstains ("the
retrieved documents do not contain enough information") instead of using its
correct parametric knowledge - 70% abstain. The value is NOT retrieval per se;
it is the AGENTIC LOOP's iterative multi-source retrieval (multiple queries,
full-paper reads, S2/web) that makes retrieval pay off. (RAG accuracy is
prompt-sensitive; the qualitative anchoring effect is robust.)

## Grounding (faithfulness) — the harness does NOT raise literal grounding

| arm | % claims supported (MiniCheck) |
|---|---|
| RAG | 0.324 [0.270, 0.377] |
| agentic | ~0.33 (Track B reference; direct Track D re-score blocked by GPU OOM / CPU too slow on the median-53-context answers) |
| bare | N/A (no contexts) |

RAG ~= agentic on literal grounding despite a 3.7x accuracy gap. So the harness
improves ANSWER CORRECTNESS + ABSTENTION, not verbatim grounding - consistent
with Track B/C1 (the un-grounded content is faithful synthesis, not fabrication).

## Abstention on fabricated papers (Track C1 set, per arm)

| arm | abstain (auto markers) | genuine confabulation |
|---|---|---|
| RAG | 0.36 | (retrieves unrelated papers; more confab-prone) |
| bare | 0.59 | **~7 / 100** (invents findings, e.g. beta-sheet structure for a nonexistent paper) |
| agentic | 0.80 (0.98 reviewed) | **~0** (C1) |

(Auto markers under-count refusals - most "possible confabulation" are
marker-missed refusals, per the C1 review method; the numbers here are the
DIRECTION.) **Tool-grounding is what causes the good abstention:** the agentic
arm's read_paper 404s the fake DOI and forces honesty, so genuine confabulation
falls from ~7% (bare) to ~0% (agentic). Removing tools (bare) or giving only
naive RAG lets the model invent.

## Cost-accuracy (Pareto)

bare: 0.32 @ 14s/0 tools. RAG: 0.15 @ 10s/0 tools (dominated - worse AND cheap
is no help). agentic: 0.56 @ 118s/16 tools. The harness buys +0.24 accuracy for
~8x wall-clock; self-hosted cost is compute-time, not API dollars.

## Verdict

Track D is the empirical backbone: **the agentic harness measurably and
significantly beats both the bare model and vanilla RAG on accuracy and
abstention quality.** Naive RAG is a trap (anchors on noisy retrieval, below
bare); the agentic loop is what makes retrieval pay off. The cost is real (~8x
time). Scorecard `2026-07-13_harness-ablation.json`.
