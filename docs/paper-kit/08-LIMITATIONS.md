# 08 Limitations

What is explicitly not claimed, and the caveats that must travel with each
headline number. Stating these plainly is cheaper than being asked.

---

## 1. What is NOT claimed

| Item | Status | Why |
|---|---|---|
| Phase 4 local query pool | **Deferred** | Blocked on human query curation and two-annotator qrels, not on compute. Only 42 real candidate queries exist so far, from one group's actual usage. |
| Track C stratum 2 (local-pool abstention) | Deferred | Depends on Phase 4. |
| Track B answer-level local pool (T7) | Deferred | Depends on Phase 4. |
| MiniCheck as a headline judge (Benchmark T4) | Dropped as a headline | Validated (QA AUROC 0.950) and used for faithfulness, but faithfulness is reported from the local judge only, not cross-validated against a frontier judge. |
| RAGAS external cross-check (T9) | **Dropped** | No frontier API key, no spending cap, and it would egress evaluation data, contradicting the privacy story. The local judge is human-validated at AUROC 0.950. |
| Track F (expert benchmark, validated certification, AstaBench positioning) | **Not run** | Follow-up proposal scope. |
| BEIR nfcorpus / scidocs / trec-covid | Not run | SciFact only. |
| Human-expert comparison | **Context, not a claim** | PaperQA2's 0.660 and the expert mean 0.677 are quoted from their sources, not re-measured here. |
| Certification thresholds | **Provisional, not validated** | Current baselines with a ~15% margin. No predictive-validity evidence. The threshold file says so in its own note field. |
| CSFCube, QASPER (T10) | Not started | |
| A controlled backbone comparison | **Not claimed** | The two Track D runs differ by backbone *and* by a month of retrieval commits. The cross-backbone agreement is reported as suggestive. |

---

## 2. Caveats attached to the headline numbers

### The LitQA2 comparison to PaperQA2 and to humans

Three separate caveats, all of which must appear together:

1. **PaperQA2 was trained on LitQA2 and Munin's off-the-shelf Qwen was not**,
   so the accuracy comparison flatters Munin. This is not a small effect and
   should not be relegated to a footnote.
2. **The human number carries a very large SD** (0.677 ± 0.119) on n = 9.
   Claiming to exceed the human mean is claiming to exceed a noisy point
   estimate.
3. **Every LitQA2 number has measured run-to-run churn.** On Qwen3.6,
   comparing two runs of the same configuration question by question, 34 of
   199 verdicts changed, and only 11 of those were recovered truncations. Six
   flipped correct → incorrect and six the other way purely from
   temperature-0.7 resampling. On Qwen3.8, two identical bare arms one day
   apart scored 0.422 and 0.387, and the agentic arm measured twice under two
   protocols gave 0.874 and 0.884 with 24 of 199 verdicts flipping
   symmetrically. Roughly ±0.035 on a 199-question arm is noise.

**The safe framing:** a strong, real result, not a clean "beats humans"
headline.

### Which agentic number to quote

Several valid numbers exist for the agentic arm on the same 199 questions, on
two backbones, and the paper must pick and justify:

| Number | Backbone | Run | Condition |
|---|---|---|---|
| **0.874** | **Qwen3.8** | 2026-08-26 Track D agentic arm | The paired ablation on the production model, `egress=full`, concurrency 1, all arms 16,384 tokens |
| **0.884** | **Qwen3.8** | 2026-09-14 standalone answer track | 900 s deadline, `egress=full`, concurrency 1, same protocol as the 07-24 run; harness two weeks newer than the ablation arm's |
| 0.864 | Qwen3.6 | 2026-07-24 answer track | 900 s deadline, 0 truncations, 0 unparseable |
| 0.839 | Qwen3.6 | 2026-07-27 Track D agentic arm | The paired ablation, `egress=full`, concurrency 1 |
| 0.814 | Qwen3.6 | 2026-07-24, 300 s | 11 answers truncated, all counted wrong |
| 0.688 | Qwen3.6 | 2026-07-26 companion | Higher concurrency, constrained egress |

Recommended: **quote 0.874 in the ablation context** (it is the paired,
same-conditions number that the +0.487 delta is computed from) and **0.884 as
the standalone LitQA2 result** on the production model. The two agree
question-paired (+0.010 [−0.035, +0.055], p = 0.73), as the Qwen3.6 pair
0.864 / 0.839 did, so they are one measurement taken twice; the 0.884 run is
on a harness two weeks newer than the ablation's (`05-RESULTS.md` R5), which
is a reason to keep the ablation number for the delta and the standalone one
for the headline, not to average them. Do not average across backbones, and
do not quote 0.688 or 0.814 except as sensitivity points.

### The abstention claim

- **Every Track C headline is on the production backbone** (C1 09-14, C2b and
  risk-coverage 09-15). The cross-backbone deltas (C2b correct abstention
  +0.222, p = 0.015) are suggestive: backbone and seven weeks of harness
  commits moved together, and the 09-15 shadow covers a chunk-index leak path
  the 07-27 design did not have. Within-pair numbers are the claim.
- **The shadow is only as complete as the list of retrieval paths.** The C2b
  design removes the source from every collection the harness searches. A
  retrieval path added after the shadow recipe was written (the chunk index,
  2026-08-30) silently invalidated the recipe until it was noticed on
  2026-09-15. Any future path (a new index, a cache, a citation-graph text
  field) needs the same treatment or the absent arm measures nothing.
- **C1's 1.000 is a 100-item ceiling, not a rate.** Quote it with the Wilson
  interval [0.963, 1.000]; the bootstrap CI is degenerate at a boundary.
- **The C1 classifier is marker-based and the substitution seam is only
  spot-checked.** 13 of the 100 Qwen3.8 refusals also cite a real corpus DOI
  (4 on Qwen3.6). They count as correct abstentions because a not-found marker
  is present; a stricter judge would ask whether the real paper is offered as
  related work or passed off as the asked paper. Four of 13 were read by hand
  and all are the former; the remaining nine are not audited.
- n = 27 on the C2 answerable subset is small. The correct-abstention rate on
  Qwen3.8 is **0.889**, bootstrap [0.777, 1.000] / Wilson [0.719, 0.961]; the
  bootstrap touches the boundary, so quote Wilson, and never the rate bare.
  On Qwen3.6 it was 0.667 [0.481, 0.852] / [0.478, 0.814]. The *change* from
  the old flat loop (+0.467 [0.232, 0.697], p < 0.001, one backbone) is the
  controlled comparison; the backbone step (+0.222 [0.040, 0.420], p = 0.015)
  is not.
- One of 27 still answered wrong when the source was removed, and two
  answered correctly without it. Strong calibration, not perfect.
- C1 remains the cleaner of the two signals because it does not depend on the
  shadow-corpus construction.
- Reading anything across the `egress=off` and `egress=full` C2 pairs is
  invalid. The `egress=full` pair is a robustness result, not an abstention
  measurement.

### The faithfulness null

- It is a null on the **RAG versus agentic** comparison only. The bare arm is
  structurally unscoreable, so the three-arm faithfulness comparison the plan
  originally specified can only ever be a two-arm comparison.
- The absolute level (~0.28 on Qwen3.8, ~0.33 on Qwen3.6) is judged by a sub-1B entailment model whose
  literalness is a known source of false negatives: a claim entailed by two
  passages jointly scores unsupported.
- The context union is generous (all retrieval results), which inflates
  support. The direction of the bias is known but not corrected.
- `% answers fully supported` is approximately zero for arithmetic reasons on
  long answers. It is not a finding and should not be quoted alone.

### Tool reliability

- `web_search` degraded at 1.000 is a measurement artifact of corpus-first
  ranking reserving few web slots, not an outage.
- Recovery rate 1.000 is over 102 queries that hit a failure (86 on Qwen3.6).
  It says failures were absorbed on this benchmark, not that the harness is
  unfailable.
- **The aggregate error rate is not comparable across the two backbones.**
  `web_fetch`'s failure definition changed between the runs (anti-bot
  interstitials became errors instead of content), so 0.061 → 0.139 mixes a
  definition change with behaviour. Only `search` (0.000 → 0.122, mistyped
  arguments) is a clean cross-backbone delta. Both were fixed after the run,
  so the T11 figures describe the tool layer during the comparison, not as
  shipped.

---

## 3. Threats to validity

### External validity

| Threat | Detail |
|---|---|
| **Domain mismatch in every public benchmark** | SciFact is biomedical claim verification; LitSearch is ML/NLP; LitQA2 is biology. Munin's corpus is chemistry / biophysics / membrane biology. The benchmarks measure the *mechanism* on realistic queries, not Munin's own domain. |
| **Single deployment, single group** | One cluster, one corpus, one user population. Nothing here establishes that the design transfers to a different group's corpus, and the encoder relevance floor is explicitly calibrated to this encoder on these candidates. |
| **Two base models, one controlled** | Every headline (Track D, faithfulness, T11, C1, C2b, risk-coverage, standalone LitQA2) is on `qwen3.8-27b` (production) with the Qwen3.6-35B-A3B run kept beside it. The two backbones agree on ordering and rough magnitude, but the comparison is confounded (below), and neither is a frontier model. The briefing that motivated Track D warns that a stronger base model can *hurt* a specialised harness; the dense 27B did not, but that is one data point. |
| **Benchmark answerability** | LitQA2 questions are multiple-choice and frequently answerable from parametric knowledge, which is exactly why the bare arm reaches 0.387 (Qwen3.8) and why the C2 design needed the `egress=off` control. |

### Internal validity

| Threat | Detail |
|---|---|
| **Non-simultaneous arms** | The three ablation arms were captured on the same day at the same concurrency, but the abstention operating points come from several capture dates. The scorer flags this rather than hiding it. |
| **Temperature 0.7** | The measured churn is 34 of 199 verdicts between two runs of the same configuration, and ~0.035 between two identical Qwen3.8 bare arms a day apart. Sub-3-point deltas are inside the noise floor. |
| **Cross-backbone confound** | 16 commits touched `backend/retrieval/` between the Qwen3.6 (07-27) and Qwen3.8 (08-26) Track D runs, several material to the agentic arm (score normalisation before citation re-rank, web_fetch failure semantics, context-budget fixes, PDF resolution). Deployment timing of each was not independently verified against the run window. Per-arm cross-run deltas must not be attributed to the backbone. The within-run three-arm comparison is unaffected. |
| **Sampling not matched across arms** | Bare and RAG arms sample at temperature 0.7; the agentic arm inherits the research persona's 1.0 / top_p 0.95 / top_k 20 / presence_penalty 1.5. Present in every Track D run, not controlled for. A reviewer may reasonably ask whether sampling contributes to the harness delta. |
| **A partial re-run inside the headline** | 8 of the 199 Qwen3.8 agentic questions (indices 191-198) were re-run about four hours after the rest, after the 02:00 vLLM cron cancelled the SLURM job mid-arm. Identical configuration; 7 of 8 came back correct. Recorded as provenance, not as a concern. |
| **Marker-based abstention detection** | Deterministic and auditable, but a curated phrase list. Mitigated by keeping raw answers in the scorecard and by manual review of residuals, not eliminated. |
| **The eval hands over a known source** | Retrieval-of-the-wrong-paper is structurally invisible to the current suite. The tagged-ref audit makes the confabulated-DOI failure *measurable*; it does not make it go away, and it has not yet been measured. |
| **Judge validated on RAGTruth, not on this domain** | QA AUROC 0.950 is on RAGTruth's QA split. Domain transfer to chemistry abstracts is assumed, not shown. |

### Construct validity

- **Risk-coverage points are operating points, not a swept curve.** The chat
  emits a hard abstain decision with no per-item confidence, so one run yields
  one point. A true curve needs the answer-letter logprob captured per item.
- **Cost is wall-clock and tokens, not GPU-seconds.** `slurmdbd` is not
  deployed, so job accounting is unavailable. Wall-clock at concurrency 1 is a
  proxy.
- **"Agentic" is one specific harness.** The ablation compares Munin's harness
  to two baselines, not the class of agentic harnesses to the class of RAG
  systems.

---

## 4. Known unresolved engineering issues

Items that are real, known, and not fixed. Listing them is better than being
asked.

| Issue | Status |
|---|---|
| GROBID mis-keys some papers under a reference's DOI | Guard flags it at ingest (70 records currently carry the flag). The correct repair is Crossref-by-title rather than by the suspect DOI. Not implemented. |
| `web_fetch` 45% error rate | Partly unfixable at the fetch layer: publisher datacenter-IP walls. Retry-with-backoff recovers the transient share. |
| Neo4j author graph and Qdrant author payload | Reconciled after the 2026-08 repair, but the two stores were knowingly out of step between runs. |
| Old SPECTER `papers` collection | Retained as rollback. Retiring it is a tracked, one-way, root-owned task. |
| Deep Research breadth | Munin reads roughly 5 distinct sources against a frontier system's ~50 on the same capstone question. Diagnosed as upstream retrieval relevance plus corpus depth, not a design flaw, but not closed. |
| Scoped abstention | If a user is in a sub-corpus and the answer is not there but *is* consortium-wide, `not_found` is misleading. `coverage_note` addresses it in `search`; a fifth outcome value in `source` was considered and not added. |
| MiroThinker deep-research path | Disabled 2026-07, kept in code, no parity evaluation before deletion. |

---

## 5. Deliberate design trade-offs (not defects, but reviewers will ask)

| Trade-off | Rationale |
|---|---|
| Tool allowlists were replaced by a soft bias | The hard boundary (a research profile literally could not call `run_python`) was retired because delegation made it vestigial. Any tool is now reachable from any profile. If a specific tool needs a hard wall it gets an explicit per-tool guard. |
| Chunking is an overflow fallback, not the default | The oracle proved 0.82 with full text; chunk-and-rank adds a retrieval-miss failure mode the oracle never tested, so it structurally cannot reach that ceiling. Chunking must earn its place on cost against the no-chunk baseline. |
| Deep Research runs in-process, not as a SLURM job | The real contention is inside vLLM's continuous batching, which SLURM cannot see. A SLURM job cap would not bite. |
| One model for every call, including summarisation | One vLLM instance serving one model. The cost levers are caching, batching, and context length, not model size per mode. |
| Snowballing is depth 1 | Depth 2 is transitively unbounded (40 references each) and needs measurement first. |
| The audit annotates rather than repairs | A silent repair generation would hide the error from the human. |
| `is_concurrency_safe` defaults True | With 31 read-only tools and 8 mutators, explicit-only for the mutators is the smaller surface area, at the cost that a new mutating tool that forgets the flag over-parallelises until noticed. |

---

## 6. Two items requiring an author decision before publication

These are not limitations of the work; they are choices that must be made
deliberately rather than by omission.

### 6.1 Corpus acquisition

The paper crawler includes a Sci-Hub download path for paywalled material, and
crawler downloads are 94% of the corpus. Options: describe it factually,
describe acquisition generically ("open-access resolvers plus institutional
access"), or remove it from the published configuration. Whatever is chosen,
the choice should be conscious, because a reader who inspects the released code
will find it.

### 6.2 The sovereignty claim's scope

Inference, the corpus, the vector store, the citation graph, the code sandbox,
and the faithfulness judge all run on the group's own cluster. **The frontend
VPS is rented from a commercial provider**, so chat messages and outputs
transit a third-party host, and `web_search` sends queries to a commercial
search API (an explicit, controllable egress boundary, but a real one).

The defensible claim is therefore precise rather than absolute: *the corpus and
all model inference remain on the group's own hardware, and outbound access is
a per-request control that defaults off for evaluation*. An unqualified "no
data leaves the premises" is not supportable as currently deployed, and a
reviewer of a sovereignty claim will ask.
