# Paper-kit refresh: bring `docs/paper-kit/` to the 2026-08-26 provenance

Status: **DONE 2026-09-14** (`617ea4e` kit refresh, `0bd8806` egress fix,
`b8ce907` Track A wording, `28a91f1` standalone answer track, `38e81cb` C1).
Q1, Q3, Q4, Q5, Q6 taken at their defaults. **Q2 went the other way:** the
standalone answer track was confirmed never re-run and was re-run the same
day (0.884 [0.839, 0.925], agrees with the ablation arm question-paired,
+0.010 p=0.73). Beyond the plan, C1 was re-run on Qwen3.8 at the paper
writer's request (100/100, 0 confabulated local cites), and two reproduce
defects found on the way were fixed (answer-track and C1 commands ran
`egress=off` by default; `run_c1` resumes from the previous capture). C2b
and risk-coverage followed on 2026-09-15 (`e05bd8d`). Moved to `done/`
2026-09-15. Written
2026-09-14 as step 3 of `MODEL-SWAP-QWEN38-PLAN.md` (section 6), the half
that was never done.
No GPU, no new measurements: every number comes from `RESULTS.md`'s
"Model swap ... Track D re-run" section and the three `2026-08-26_*`
scorecards, which already exist.

## 0. Problem

The kit was generated 2026-08-04 at `5441a13` and has not been touched since.
`PAPER.md` and `RESULTS.md` moved to the Qwen3.8-27B headline on 2026-08-26/27;
the kit still names Qwen3.6-35B-A3B as the generation model in its shared
provenance block and quotes 0.839 / +0.538, +0.023 p=0.496 and 8.61 calls per
query as the headlines. Zero mentions of Qwen3.8 or the 08-26 run anywhere in
the eleven files. Anyone drafting from the kit, which is what PAPER.md tells
them to do, writes the wrong paper.

Two smaller drifts ride along, because the kit is also the system description:
the tool inventory (`02` says 42, `schemas.py` has 45) and the serving profile
(`01` says single-GPU `--max-num-seqs 2`, production is TP=2 / 64k /
`--max-num-seqs 8`).

## 1. Framing decision (the one that shapes every edit)

**Qwen3.8 numbers are the headline everywhere. Qwen3.6 numbers stay, as the
second backbone.** Not deleted, not demoted to a footnote: the fact that the
ordering and rough effect size hold across a dense 27B and a 35B/3B-active MoE
is itself a result, and the kit gains a "replicates across backbones" line for
Track D, the faithfulness null, and tool-failure recovery. Every such line
carries the caveat PAPER.md already carries: **suggestive, not controlled**,
16 retrieval commits between the runs, per-arm cross-run deltas are not a model
effect.

This is what PAPER.md and RESULTS.md already do. The kit has to agree with
them, not offer a third framing.

## 2. Edits, per file and section

Numbers are copied from `RESULTS.md` lines 868-1016 or read out of the JSONs
with a script (section 3), never retyped from memory.

### `00-INDEX.md`
- "Generated 2026-08-04 at `5441a13`" becomes a regeneration line with the new
  date and commit, keeping the original line as history.
- One-paragraph summary: 0.874 / 0.387 / 0.211, +0.487 [0.407, 0.568], 4.1x,
  faithfulness +0.010 p=0.776; add one clause for the cross-backbone
  replication.
- "Provenance shared by every number": the generation-model bullet becomes a
  split, mirroring the RESULTS.md header (Qwen3.8 for Track D / B-per-arm /
  T11; Qwen3.6 for Track C; retrieval model-independent).
- Scorecard count: the kit says 13 here, 16 in `scorecards/README.md`, and
  PAPER.md says 15. After adding three it is **19**; fix all three places.
- Glossary: add **Backbone** (the generation model under the harness) since the
  word now does work.

### `01-SYSTEM.md`
- Section 3 Models: replace the Qwen3.6 row with Qwen3.8-27B-AWQ-INT4 (dense
  27B, hybrid Gated DeltaNet + Gated Attention, natively multimodal, pack-quantized
  group-32, served as `qwen3.8-27b`, `reasoning_effort=medium`). Keep Qwen3.6 as
  a "retired 2026-08-26" row because the Track C numbers are on it.
- Section 4 Serving: production is the TP=2 profile (both RTX 5090s, 64k window,
  `--max-num-seqs 8`, `gpu-memory-utilization 0.85`); the single-GPU profile
  (`--max-num-seqs 2`) is the fallback that frees one card. The paragraph
  calling `--max-num-seqs 2` "the single most important operational number"
  is rewritten as the rule it actually is: **benchmark concurrency must not
  exceed the running profile's `--max-num-seqs`**, whichever profile that is.
  The 131072-window TP=2 experiment paragraph becomes history (the profile
  was retargeted at concurrency and fixed at 64k on purpose, per the tp2 script
  header and `deploy.sh` banner).
- Section 8 Operational envelope: model row, context row (unchanged at 65,536 /
  60,000), corpus row (see `03`).

### `02-ARCHITECTURE.md`
- Section 5 Tool inventory: 42 to 45. Add `list_documents`, `browse_tag_papers`
  to Retrieval and `edit_python` to Execution. Re-count the "eight explicit
  opt-outs" and "31 genuinely read-only" claims against `schemas.py` before
  writing them (a loose grep shows 11 `is_concurrency_safe` mentions; verify
  which are `False`).
- Section 3.2 `search`: the section predates `mode=evidence` chunk-level
  retrieval (`587d93a`, 08-30) and the escalating ladder plus grounded read
  stage (`51f1d5a`, 08-27). Read the current `search_agent.py` and update the
  contract block and the `thin_evidence` paragraph. **Add one sentence stating
  that the architecture described is at commit X while the 08-26 measurement
  is at `3e0bcfb`**, so nobody attributes the accuracy to code that landed after
  the run (open question Q5).
- Any Qwen3.6 mention in the design-principles prose (there is one) becomes
  model-neutral or names both.

### `03-CORPUS.md`
- Corpus-size table gains a row for the 08-26 run. The ablation scorecards do
  not stamp a corpus count; the nearest recorded figure is **68,863 entries on
  2026-08-28** (`3e2b181`), two days after the run. State it as "nearest
  recorded" (open question Q3).

### `04-METHODS.md`
- Section 1 Suite structure: provenance sentence per track (which model each
  track's headline is on).
- Section 9 Constraints: TP=2 profile; the **arm-matching fixes** from the
  swap plan 4b (bare/RAG take the model from `config.VLLM_MODEL_NAME`,
  `reasoning_effort=medium` matches the agentic path, `max_tokens=16,384` on
  all arms, which is what made the 07-27 bare arm's 33 unparseable answers go
  to 0). Add the **sampling is not matched across arms** caveat verbatim from
  RESULTS.md (bare/RAG 0.7 vs persona 1.0/0.95/20/1.5; predates the swap;
  not controlled for).
- Track D subsection: the run-to-run variance figure (~0.035 on a 199-question
  bare arm at temperature 0.7) belongs here, since it is a property of the
  method, not of one result.

### `05-RESULTS.md`
- **R1**: rewrite as the RESULTS.md two-column table (Qwen3.6 07-27 beside
  Qwen3.8 08-26, all three arms, accuracy / abstain / precision of attempted),
  then the paired-deltas table, both columns. Headline row is Qwen3.8. Add
  cost (157.3 s, 6.9 tools per query on Qwen3.8 vs 79.0 s, 8.6 on Qwen3.6) and
  say plainly that wall-clock is not comparable across a dense 27B and a
  3B-active MoE.
- **R2**: both columns (0.326/0.340 +0.023 p=0.496 n=189; 0.282/0.288 +0.010
  p=0.776 n=163). Judge-validation and single-arm subsections unchanged.
- **R3**: unchanged numbers, plus the box PAPER.md already has: retired model,
  not re-run, Qwen3.8 abstains far less outside the harness so do not assume
  carry-over.
- **R4**: unchanged, plus one line: model-independent by construction, not
  re-run.
- **R5** arc table: add the 08-26 row (BGE-large, agent architecture, Track D
  agentic arm, **Qwen3.8**, 0.874, precision 0.946, abstain 0.075). Add a
  "Backbone" column to the table so the 0.864 row is visibly Qwen3.6. State
  that the standalone 900 s answer track was **not** re-run on Qwen3.8 (open
  question Q2).
- **R6**: both columns (calls 1,714 / 1,380; per query 8.61 / 6.93; error
  0.061 / 0.139; degraded 0.240 / 0.379; failures 86 / 102; recovery 1.000 /
  1.000) with the per-tool paragraph: `web_fetch` 0.453 to 0.678 **not
  comparable** (`2ff9aef` redefined failure), `search` 0.000 to 0.122
  **comparable** (mistyped arguments, 11 of 11), both fixed after the run in
  `fd559c9`, so these describe the tool layer during the comparison, not as
  shipped.
- **R7** unchanged.

### `06-ABLATIONS.md`
- 1.2 retitled "Clean run, 2026-07-27, n=199, Qwen3.6"; new **1.3
  "Re-measurement on the production backbone, 2026-08-26, n=199, Qwen3.8 (the
  headline)"**; old 1.3 becomes 1.4 and its table gains the Qwen3.8 column.
- The "bare arm max_tokens 4096 vs 16,384" defect is a measured condition:
  33 unparseable to 0, `agentic - rag` unchanged (+0.668 to +0.663) while
  `agentic - bare` moved (+0.538 to +0.487). It goes into **section 6
  Measurement-condition ablations** next to the concurrency one, because it is
  the same class of finding: the number moved for a reason that is not the
  system.
- Section 6 also gains the backbone swap as a row, labelled "suggestive".

### `07-FINDINGS.md`
- Section 1 arc paragraph: new numbers, one clause on the replication.
- Section 3 RAG worse than nothing: strengthen. -0.176 [-0.251, -0.096] on a
  correctly budgeted bare arm is the strongest form of the finding; the 07-27
  -0.131 was against a bare arm that was partly truncation.
- Section 4 faithfulness null: "replicates across backbones", both deltas.
- Section 8 tool failures absorbed: recovery 1.000 replicates; calls per query
  fell; the error-rate comparison is split as in R6.
- **New section** (between 8 and 9): **"The harness, not the backbone, keeps
  attempted answers trustworthy."** Outside the harness Qwen3.8 abstains far
  less (bare 0.201 to 0.040, RAG 0.749 to 0.498) and precision of attempted
  falls with it (0.476 to 0.403, 0.708 to 0.420); inside, abstention is
  identical at 0.075 and precision rises 0.908 to 0.946. This is the one
  genuinely new finding from the swap and it deserves a section, not a bullet.
- Section 10 secondary: Qwen3.8 emits mistyped tool arguments where Qwen3.6
  did not (`search` 0.000 to 0.122, every failure an argument type), fixed by
  schema coercion in the executor.

### `08-LIMITATIONS.md`
- Section 2 "The 0.864 versus 0.839 versus 0.814 question": restructure. On
  the production model there is **one** agentic number, 0.874, because only
  the ablation arms were re-run; the tension between "ablation number" and
  "standalone number" existed only on Qwen3.6. Recommend quoting 0.874 for
  both purposes and keeping 0.864/0.839 as the Qwen3.6 pair in a history row.
- Section 2 abstention claim: on the retired model; say so in the first
  sentence, not the last.
- Section 2 tool reliability: the `web_fetch` definitional change.
- Section 3 Internal validity: add (a) the cross-run confound, 16 retrieval
  commits, deployment timing not independently verified; (b) sampling not
  matched across arms; (c) run-to-run variance ~0.035; (d) the 8 agentic
  questions re-run four hours later after the 02:00 cron cancelled vLLM
  (7 of 8 correct, recorded as provenance not as a concern).
- Section 1 "What is NOT claimed" table: add "Track C on Qwen3.8: not
  measured".

### `09-RELATED-WORK.md`
- No change. Anchors were verified 2026-07-26 and nothing in the swap touches
  them.

### `10-REPRODUCE.md`
- Trap 1: `--max-num-seqs 2` becomes the profile rule (as in `01` section 4);
  production is 8 on TP=2.
- Section 2 Environment: model, profile, `reasoning_effort`, the Brave spend
  figure for a full Track D pass (~1,190 billed requests, ~$6, from RESULTS.md).
- Section 5 per-track: the Track D block gains the arm-matching env
  (`VLLM_MODEL_NAME`, `LLM_REASONING_EFFORT`) so the bare/RAG arms match.
- Section 6 What has not been run: Track C on Qwen3.8, and what it needs
  (`papers_shadow` rebuilt, :8081 instance).
- Section 7 Artifact locations: the three 08-26 scorecards.
- Section 8 Reproducibility posture: the 07-27 checkpoint is retired and the
  run dirs are archived off-machine (the RESULTS.md header warning), so the
  Qwen3.6 column is **reproducible from artifacts, not re-runnable**.

### `scorecards/`
- Copy `2026-08-26_harness-ablation.json`,
  `2026-08-26_harness-ablation-faithfulness.json`,
  `2026-08-26_toolreliability-qwen38_toolreliability.json` verbatim.
- `README.md`: three index rows (R1, R2, R6, marked current; the 07-27 rows
  marked "Qwen3.6, second backbone"); count 16 to 19; and a schema correction:
  the `meta` block described applies to `run_all` scorecards, while the
  ablation / faithfulness / risk-coverage files carry only
  `track / n_paired / git_sha / date / per_arm / deltas` and **no model or
  encoder field**, so the two headline ablation files are distinguishable only
  by `git_sha` and `date`. Add caution 8 saying exactly that.

## 3. Method and gates

1. Before editing, a throwaway script reads `per_arm` and `deltas` out of the
   three 08-26 JSONs and the three 07-27 twins and prints the R1/R2/R6 tables
   in Markdown. The kit tables are pasted from that output, not typed.
2. After editing, grep gates on the whole kit:
   - `0.839`, `0.538`, `0.302`, `0.171`, `8.61`, `1,714`, `0.023` may appear
     only on a line or in a table that also says Qwen3.6 or "07-27".
   - `qwen3.6` / `35b-a3b` may not appear in any provenance block or table
     header without "retired" or a date beside it.
   - `42 MCP tools`, `max-num-seqs 2` as a production claim, `13 raw
     scorecard`, `16 committed`: zero hits.
   - `4.9x`: zero hits (it is 4.1x now).
3. Every results table states **model and encoder** in its caption. This is
   already the rule for encoder; it now applies to model.
4. No em-dashes in new prose.
5. One commit, message naming the swap plan step it closes; then the
   paper-track README row "(no plan file) `docs/paper-kit/` refresh" moves to
   the Done table and this plan moves to `done/`.

## 4. Open questions, each with the default I will take unless told otherwise

**Q1. Framing.** Qwen3.8 headline, Qwen3.6 kept as second backbone with the
"suggestive, not controlled" caveat. *Default: yes.* Alternative: strip
Qwen3.6 to a limitations footnote, which throws away the only cross-backbone
evidence and disagrees with PAPER.md.

**Q2. The standalone LitQA2 number.** The 900 s answer track (0.864) was not
re-run on Qwen3.8; only the ablation arms were. *Default: quote 0.874 (the
ablation agentic arm) as the production-model LitQA2 result and say the
standalone track was not repeated.* Alternative: re-run
`run_litqa2 --track answer` on Qwen3.8 (roughly 5 h GPU, ~$6 Brave) to get a
standalone figure. That is a measurement, not a docs refresh, and it is out of
scope here, but it is cheap and would remove a sentence of caveat from the
paper. Say the word and it becomes its own item.

**Q3. Corpus count for the 08-26 run.** Not stamped in the scorecards. *Default:
"68,863 (nearest recorded count, 2026-08-28)".* Alternative: query Qdrant now,
which gives today's count, not the run's.

**Q4. Track C placement.** *Default: stays in R3 and `07` section 6 as a
Qwen3.6 result with the caveat box up front.* Alternative: move it under
limitations until re-run. I would not; C1's "0 confabulated local citations"
is a property of the retrieval-side check as much as of the model, and the
kit should keep saying what was measured.

**Q5. Architecture snapshot.** `02` describes the shipped system, which now
includes search changes that post-date the 08-26 measurement (`51f1d5a`,
`587d93a`, `3571bc9`). *Default: describe the current architecture and state
both commits (measured at `3e0bcfb`, described at HEAD) in one sentence.*
Alternative: freeze `02` at `3e0bcfb`, which would make the kit describe a
system that is no longer deployed.

**Q6. Regeneration line.** *Default: keep "Generated 2026-08-04 from `5441a13`"
and add "Refreshed 2026-09-xx from `<sha>`".*

## 5. Effort

One session, no GPU, no deploy. The reading (`search_agent.py` for Q5, the
`schemas.py` recount) is the only part that is not copy-from-RESULTS.md.
