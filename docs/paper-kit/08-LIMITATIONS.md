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
3. **Munin's 0.864 has measured temperature churn.** Comparing two runs of the
   same configuration question by question, 34 of 199 verdicts changed, and
   only 11 of those were recovered truncations. Six flipped correct → incorrect
   and six the other way purely from temperature-0.7 resampling.

**The safe framing:** a strong, real result, not a clean "beats humans"
headline.

### The 0.864 versus 0.839 versus 0.814 question

Three valid numbers for the agentic arm on the same 199 questions, and the
paper must pick and justify one:

| Number | Run | Condition |
|---|---|---|
| 0.864 | 2026-07-24 answer track | 900 s deadline, 0 truncations, 0 unparseable |
| 0.839 | 2026-07-27 Track D agentic arm | The paired ablation, `egress=full`, concurrency 1 |
| 0.814 | 2026-07-24, 300 s | 11 answers truncated, all counted wrong |
| 0.688 | 2026-07-26 companion | Higher concurrency, constrained egress |

Recommended: **quote 0.839 in the ablation context** (it is the paired,
same-conditions number that the +0.538 delta is computed from) and **0.864 as
the standalone LitQA2 result**. Do not average, and do not quote 0.688 or 0.814
except as sensitivity points.

### The abstention claim

- n = 27 on the C2 answerable subset is small. The correct-abstention rate is
  **0.667 [0.481, 0.852]** (bootstrap) / [0.478, 0.814] (Wilson), so the
  interval is roughly ±0.18 wide. Quote it with the interval, never bare. The
  *change* from the old harness is on firmer ground than the level is:
  **+0.467 [0.232, 0.697], p < 0.001**, question-paired over the same 50 frozen
  questions.
- Five of 27 still answered wrong when the source was removed. Strong
  calibration, not perfect.
- C1 remains the cleaner of the two signals because it does not depend on the
  shadow-corpus construction.
- Reading anything across the `egress=off` and `egress=full` C2 pairs is
  invalid. The `egress=full` pair is a robustness result, not an abstention
  measurement.

### The faithfulness null

- It is a null on the **RAG versus agentic** comparison only. The bare arm is
  structurally unscoreable, so the three-arm faithfulness comparison the plan
  originally specified can only ever be a two-arm comparison.
- The absolute level (~0.33) is judged by a sub-1B entailment model whose
  literalness is a known source of false negatives: a claim entailed by two
  passages jointly scores unsupported.
- The context union is generous (all retrieval results), which inflates
  support. The direction of the bias is known but not corrected.
- `% answers fully supported` is approximately zero for arithmetic reasons on
  long answers. It is not a finding and should not be quoted alone.

### Tool reliability

- `web_search` degraded at 1.000 is a measurement artifact of corpus-first
  ranking reserving few web slots, not an outage.
- Recovery rate 1.000 is over 86 queries that hit a failure. It says failures
  were absorbed on this benchmark, not that the harness is unfailable.

---

## 3. Threats to validity

### External validity

| Threat | Detail |
|---|---|
| **Domain mismatch in every public benchmark** | SciFact is biomedical claim verification; LitSearch is ML/NLP; LitQA2 is biology. Munin's corpus is chemistry / biophysics / membrane biology. The benchmarks measure the *mechanism* on realistic queries, not Munin's own domain. |
| **Single deployment, single group** | One cluster, one corpus, one user population. Nothing here establishes that the design transfers to a different group's corpus, and the encoder relevance floor is explicitly calibrated to this encoder on these candidates. |
| **One base model** | Every result uses `qwen3.6-35b-a3b`. The briefing that motivated Track D warns explicitly that a stronger base model can *hurt* a specialised harness, and that has not been tested. |
| **Benchmark answerability** | LitQA2 questions are multiple-choice and frequently answerable from parametric knowledge, which is exactly why the bare arm reaches 0.302 and why the C2 design needed the `egress=off` control. |

### Internal validity

| Threat | Detail |
|---|---|
| **Non-simultaneous arms** | The three ablation arms were captured on the same day at the same concurrency, but the abstention operating points come from several capture dates. The scorer flags this rather than hiding it. |
| **Temperature 0.7** | The measured churn is 34 of 199 verdicts between two runs of the same configuration. Sub-3-point deltas are inside the noise floor. |
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
