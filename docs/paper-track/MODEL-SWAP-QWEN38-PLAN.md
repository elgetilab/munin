# Model swap: Qwen3.6-35B-A3B -> Qwen3.8-27B (cyankiwi AWQ-INT4), and the paper re-measurement

Status: PLAN, not approved. Written 2026-08-24. Revised 2026-08-24 after
inspecting the cyankiwi quantization (see the revision note at the end of
section 1).

Companion to `EVAL-SUITE-MASTER-PLAN.md` and `T2-ABLATION-REFRESH-PLAN.md`.
Supersedes nothing until the re-run lands.

---

## 0. What this is

Swap the generation model under the whole system from
`cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` (MoE, 35B total / 3B active) to
**`cyankiwi/Qwen3.8-27B-AWQ-INT4`** (dense 27B, hybrid Gated DeltaNet plus
Gated Attention, natively multimodal), then re-measure every benchmark whose
number depends on the generation model so `PAPER.md` and `RESULTS.md` describe
the shipped system rather than a retired one.

The important framing: **a model swap invalidates the generation numbers, not
the retrieval numbers.** Section 4 splits the suite on exactly that line, which
is where most of the compute saving lives.

Because the chosen checkpoint comes from the same quantizer, in the same
format, at the same group size as the model already in production, this is much
closer to a drop-in than a migration. Section 2 shows the existing single-GPU
serving profile survives.

---

## 1. Facts verified 2026-08-24 (not assumed)

**Target model.** `Qwen/Qwen3.8-27B`, released 2026-08-14, Apache 2.0.
Dense 27B, 64 layers, hidden 5120, 24 query heads / 4 KV heads, head_dim 256,
262,144 native context (1M via YaRN). Hybrid layer layout: 16 repeats of
(3x Gated DeltaNet + FFN, then 1x Gated Attention + FFN), so only 16 of 64
layers hold a KV cache. Natively vision-language (images and video). Has a
`reasoning_effort` dial (xhigh / medium / low) alongside `enable_thinking` and
`preserve_thinking`, all passed via `chat_template_kwargs`. BF16 release is
55.6 GB.

**Chosen checkpoint: `cyankiwi/Qwen3.8-27B-AWQ-INT4`, 21.02 GB** (5 safetensors
shards). It is the same recipe as the model already in production, which is the
main reason to prefer it. Compared side by side against the live checkpoint on
disk at `/opt/munin/data/models/qwen3.6-35b-a3b-awq-4bit/`:

| | current, in production | proposed |
|---|---|---|
| repo | `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` | `cyankiwi/Qwen3.8-27B-AWQ-INT4` |
| architectures | `Qwen3_5MoeForConditionalGeneration` | `Qwen3_5ForConditionalGeneration` |
| model_type | `qwen3_5_moe` | `qwen3_5` |
| format | `pack-quantized` | `pack-quantized` |
| num_bits / group_size | 4 / **32** | 4 / **32** |
| symmetric | true | false (asymmetric, int8 zero point) |
| preserved at BF16 | 504 modules, vision tower first | ~300 modules: all `linear_attn`, vision tower, `lm_head`, `mtp.*`, visual merger |
| on disk | 23 GB | **21 GB** |

Both preserve the vision tower and the quantization-sensitive DeltaNet
projections at BF16. The proposed checkpoint is 2 GB **smaller** than the one
running today.

**Three consequences that de-risk most of this plan.** The production model is
already a `qwen3_5`-family conditional-generation model with a preserved vision
tower, running on this exact box. So the `qwen3_5` code path, `pack-quantized`
group-32 kernels on Blackwell SM120, the `qwen3_xml` tool parser and the
`vision.py` `image_url` path are all **proven working today**, not hoped for.

**vLLM support is already present on hugin.** The installed build is
`0.20.2rc1.dev221+gac062147f` (torch 2.11.0+cu130). The registry maps both
`Qwen3_5MoeForConditionalGeneration` (running now) and
`Qwen3_5ForConditionalGeneration -> (qwen3_5, Qwen3_5ForConditionalGeneration)`
at line 550, plus `Qwen3_5MTP -> qwen3_5_mtp`. Both candidate tool parsers
(`qwen3xml_tool_parser.py`, `qwen3coder_tool_parser.py`) and
`qwen3_reasoning_parser.py` are present. **No vLLM rebuild is assumed**, and the
custom Blackwell/CUDA-13 venv is not touched.

**Tool parser.** The vLLM recipe for Qwen3.8 suggests
`--tool-call-parser qwen3_coder`; Munin serves `qwen3_xml`. Since `qwen3_xml`
demonstrably parses the Qwen3.5-family MoE in production, the prior is strongly
that it also parses the dense sibling. Phase 0 still A/Bs it, but this is a
routine check rather than the top risk.

**Hardware.** 2x RTX 5090, 32,607 MiB each, no NVLink, SLURM
`ConstrainDevices=yes`. Disk has 2.1 TB free, so the 21 GB pull is a non-issue.

**Alternatives considered and rejected.**
`barrydeen/Qwen3.8-27B-AWQ-4bit` (27.8 GB) keeps entire DeltaNet *blocks*
including their FFNs at BF16, which is where the extra 6.8 GB goes. That is more
conservative but it does not fit a single 5090 with a usable KV cache, so it
forces TP=2 and costs the whole node (see section 2). cyankiwi's narrower
reading, preserving the DeltaNet *projections* specifically, matches the stated
sensitivity and matches the recipe already validated in this system.
`cyankiwi/Qwen3.8-27B-AWQ-FP8` (~28 GB) and
`cyankiwi/Qwen3.8-27B-AWQ-BF16-INT4` (28.85 GB) have the same single-GPU
problem. Neither cyankiwi nor barrydeen publishes accuracy-versus-BF16 numbers
for any of these, so a quality sanity check stays in Phase 0 regardless; the
difference is that cyankiwi has a track record in this exact system.

> **Revision note.** The first draft of this plan was costed against
> barrydeen's 27.8 GB checkpoint and concluded that TP=2 was forced and that
> GPU 0 would be permanently lost to batch work. That conclusion was an artifact
> of the checkpoint, not of the model, and does not hold for the cyankiwi build.
> Section 2 is rewritten accordingly, and the "accept the loss of GPU 0"
> question is withdrawn.

---

## 2. Serving: the existing single-GPU profile survives

Memory arithmetic, KV at fp8, 16 KV-bearing layers, 4 KV heads, head_dim 256:

    KV per token = 16 layers x 2 (K,V) x 4 heads x 256 dim x 1 byte = 32 KB/token

That is ~3.2x the current model's per-token KV, because Qwen3.8 has 16
full-attention layers with 4 KV heads where the current MoE has 10 with 2.
The weights get 2 GB cheaper; the KV cache gets three times more expensive.
Both effects have to be carried through together.

**Empirical anchor, measured from the running service** (`vllm-service-923.out`,
single GPU, `--gpu-memory-utilization 0.90`):

    GPU KV cache size: 310,827 tokens
    Maximum concurrency for 65,536 tokens per request: 4.74x

Backing the real overhead out of that: 32.6 GB card at an effective 0.887
(vLLM's CUDA-graph profiling discounts the requested 0.90) is a 28.9 GB budget;
minus 23 GB of weights leaves 5.9 GB, of which KV received 3.18 GB. So
**~2.7 GB goes to activations, CUDA graphs and non-torch overhead on this box.**
That is a measured number for this card and this vLLM build, not a guess.

**Projection for the proposed checkpoint, single GPU at util 0.90:**

| term | value |
|---|---|
| effective budget | 28.9 GB |
| weights | 21.0 GB |
| activations / graphs / DeltaNet state | ~3.0 GB (measured 2.7 on the MoE, rounded up for a dense 27B) |
| **KV pool** | **~4.9 GB** |
| **KV tokens @ 32 KB** | **~153,000** |
| **concurrency at 65,536** | **~2.3x** |
| concurrency at 131,072 | ~1.2x |

So **single GPU at the current 64k window with `--max-num-seqs 2` fits**, which
is exactly the production setting today, and benchmarks run at concurrency 1
regardless. Headroom is genuinely tighter than today's 4.74x, and `util 0.93`
buys roughly another 50k tokens of KV if wanted.

**The deployment architecture therefore does not change.** Keep both scripts:

- `start-vllm-service.sh` (single GPU, `gpu:vllm:1`, 64k) stays the default.
  GPU 0 stays free for batch, deepresearch and user jobs.
- `start-vllm-service-tp2.sh` (both cards, 128k) stays the optional large-window
  profile, with the same tradeoff it has today.

This also means the ~30 hours of benchmark time in section 5 does **not** need
exclusive whole-node time and can share the node with batch work, rather than
being scheduled as a drained weekend.

Proposed single-GPU serve line (a minimal edit to the existing script, not a
new one):

    vllm serve /opt/munin/data/models/qwen3.8-27b-awq-int4 \
        --host 0.0.0.0 --port 8000 \
        --gpu-memory-utilization 0.90 \
        --max-model-len 65536 \
        --max-num-seqs 2 \
        --quantization compressed-tensors \
        --kv-cache-dtype fp8 \
        --served-model-name qwen3.8-27b \
        --enable-auto-tool-choice \
        --tool-call-parser <settled in Phase 0, prior is qwen3_xml> \
        --reasoning-parser qwen3

Note `--dtype float16` is dropped relative to the current script: the checkpoint
carries BF16 tensors for the preserved modules, so let vLLM take the config
dtype rather than forcing fp16 on them.

**Held back deliberately.** MTP speculative decoding
(`--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`) is
distribution-preserving in principle and the MTP head is preserved at BF16 in
this checkpoint, but it is an untested interaction with the reasoning parser on
this build. A throughput optimisation must not be introduced in the same change
as the measurement. Enable it after the clean run, measure the speedup, adopt it
for production separately.

Hold `--max-model-len` at **65536** for the whole re-measurement, matching the
profile every existing committed number was produced under. Raising it would
confound "new model" with "bigger window" and make the before/after diff
uninterpretable. The 262k native headroom is future work and deserves its own
ablation.

---

## 3. Code and config changes

112 references to `qwen3.6` / `35b-a3b` across 44 files, but only a small set is
functional. Everything else is prose that gets a provenance sweep in Phase 6.

Functional, must change:

| File | What |
|---|---|
| `backend/scripts/vllm/start-vllm-service.sh` | `MODEL_ID`, `MODEL_PATH`, `MODEL_NAME`, drop `--dtype float16`, update the banner and the ~19 GB download note |
| `backend/scripts/vllm/start-vllm-service-tp2.sh` | same three constants; the 128k profile stays valid and roomier than single-GPU |
| `backend/deploy.sh:89` `VLLM_MODEL_DIR` | **critical**: `stage_qwen_tokenizer` copies `tokenizer.json` out of this dir. If it is not updated, the retrieval container budgets context with the OLD tokenizer and every trim decision is silently wrong |
| `backend/retrieval/database.py:63` `DEFAULT_LLM_MODEL_NAME` | default served-model name |
| `backend/retrieval/main.py:1090` | `VLLM_MODEL_NAME` fallback literal |
| `backend/docker/docker-compose.yml` (275, 477, 659) | `VLLM_MODEL_NAME`, `VLLM_SERVE_MODEL`, parser defaults. Already parameterised, so mostly a default bump |
| `backend/docker/docker-compose.shadow.yml:87` | Track C2's shadow instance must match, or the paired arms differ by model |
| `backend/config/munin.env.template:43`, `backend/config/munin-embedding-map.service:24` | deployed env |
| `backend/benchmarks/munin_bench/config.py` `VLLM_MODEL_NAME` | bench default |
| `backend/benchmarks/munin_bench/ablation/vllm_answer.py:15` `MODEL` | **the bare arm talks to vLLM directly**; miss this and the bare arm silently runs the old model while agentic runs the new one |
| `.env.example` (129, 134, 141) | published defaults and the "results used X" note |

Behavioural, worth a deliberate decision:

- `thinking_off_fields()` in `database.py:126` sends
  `chat_template_kwargs: {"enable_thinking": false}` for mechanical sub-tasks
  (summarise, query expansion, equation OCR). Qwen3.8 still honours
  `enable_thinking`, and the current Qwen3.5-family model already accepts this
  field in production. Verify in Phase 0 anyway; a 400 on this field is the
  documented failure signature.
- `reasoning_effort` is new. **Leave it unset (model default) for the entire
  re-measurement.** A `reasoning_effort` sweep is an attractive follow-up
  ablation (accuracy vs tokens vs latency) but folding it into the swap makes
  every headline number un-attributable.
- Vision: both the old and new checkpoints preserve the vision tower at BF16, so
  `vision.py`'s `image_url` path should be at parity or better. Smoke-test it;
  do not benchmark it (there is no vision claim in the paper).

---

## 4. What gets re-run, and what deliberately does not

This is the part that saves days of GPU time, and it is a correctness argument
before it is a cost argument.

### 4a. Model-independent: DO NOT re-run

| Track | Why it does not move |
|---|---|
| Phase 3 BEIR / SciFact | BM25 / dense / RRF over an `eval_scifact` collection. No LLM in the loop |
| Phase 5 LitQA2 **retrieval** | AgentRetriever runs against the **frozen** committed variant set precisely so the ranker measurement cannot depend on a non-deterministic expander (`munin_bench/frozen_variants/`, and the README says so explicitly) |
| Encoder bake-off, migration Phase A | encoder comparisons, no generation |
| T8 LitSearch (incl. the citation-rerank normalisation fix) | ranker measurement |

Re-running these would not produce new information; it would produce new noise
against a claim that did not change. The paper's provenance block must then say
so plainly: **retrieval numbers carry over from the BGE-large corpus and are
model-independent by construction; generation numbers are re-measured on
Qwen3.8-27B.**

One honest caveat to state in Limitations: in *production* the AgentRetriever
expands queries with the live LLM, so real-world expansion quality does move
with the model. The benchmark freezes it; that drift shows up in Track D/E
end-to-end numbers, not in Track A. This is already the documented position.

### 4b. Model-dependent: MUST re-run

Ordered by dependency, because several tracks consume another track's output.

| # | Track | Command | n | New generation? |
|---|---|---|---|---|
| 1 | D: ablation, 3 arms | `munin_bench.ablation.run_arm --arm {bare,rag,agentic}` then `ablation.compare` | 199 x 3 | yes |
| 2 | T11: tool reliability | derived from the Track D agentic telemetry | - | no, derived |
| 3 | B: faithfulness per arm | `munin_bench.ablation.faithfulness` (MiniCheck judge unchanged) | 189 | no, scores #1's transcripts on GPU |
| 4 | C1: fabricated papers | `munin_bench.abstention.run_c1`, `egress=full` | 100 | yes |
| 5 | C2b: shadow pair | `munin_bench.abstention.run_c2 --arm {present,absent}`, `egress=off` | 50 x 2 | yes, needs the :8081 shadow stack on the SAME model |
| 6 | C: risk-coverage | `munin_bench.abstention.risk_coverage` over #1, #4, #5 | - | no, derived |
| 7 | Phase 5 LitQA2 answer | `run_litqa2 --track answer --concurrency 1` | 199 | yes, kept for continuity with the pre-agent arc |
| 8 | Routing regression (A0-A5) | `munin_bench.routing.routing_eval` | - | yes. Not a paper number, but it is the deploy gate and a model swap is exactly what it exists to catch |

Then one unified certification pass:

    run_all --tag qwen38-27b --tracks litqa2-answer,faithfulness,abstention,ablation \
            --with-reliability --certify --date <D>

`--certify` gates against `certification_thresholds.json`. Note the thresholds
were set from the 2026-07-13 baseline with a ~15% margin and are explicitly
provisional. **Expect to have to decide, not just read, the verdict**: if
Qwen3.8-27B lands inside the margin the gate passes; if it exceeds the old
baseline the thresholds should be deliberately re-based, and if it fails, that
failure is itself a finding worth reporting rather than a reason to tune.

Note the deliberate omission of `beir-scifact,litqa2-retrieval` from the
`--tracks` list, per 4a.

### 4c. Free bonus result

`munin_bench.pipelines.compare <old>.json <new>.json` does a paired bootstrap
over per-query arrays. The 199 LitQA2 questions are identical across the old and
new runs, so old-model vs new-model is a legitimate **paired** comparison at
zero extra compute. That gives the paper a model-sensitivity result it does not
currently have: how much of the 0.839 agentic accuracy is the harness and how
much is the specific backbone. Given the paper's central claim is "the harness
is what produces the accuracy", a second backbone reproducing the harness delta
is a genuinely strong addition, not a chore. The MoE-to-dense jump makes it a
more informative second point than a same-family size bump would have been.

### 4d. Still not fillable by compute

Unchanged by this plan, and the paper should keep saying so: Phase 4 local query
pool, T3 stratum 2, T7 answer-level local pool. All three are blocked on human
query curation and two-annotator qrels. A new model does not unblock them.

---

## 5. Execution phases

**Phase 0: serving proof (half a day, no benchmarks).**
Pull the checkpoint. Serve it single-GPU by hand outside SLURM if the node is
free, else via a short interactive job. Then, in order:
1. Read `GPU KV cache size` and `Maximum concurrency` out of the startup log.
   **Gate:** at least ~131,000 tokens, i.e. 2x at 65,536. If lower, raise
   `--gpu-memory-utilization` to 0.93 and re-read; if still short, fall back to
   the TP=2 profile and reinstate the whole-node scheduling assumption.
2. `/v1/models` returns `max_model_len` 65536.
3. A plain completion works.
4. `chat_template_kwargs: {"enable_thinking": false}` returns 200, not 400.
5. **Tool calling**: drive one multi-tool turn through the live chat and confirm
   parsed `tool_calls`. A/B `qwen3_xml` against `qwen3_coder` and pick the one
   that parses cleanly. Prior is `qwen3_xml`, since it parses the Qwen3.5-family
   MoE today.
6. One vision turn through `vision.py`.
7. A quick quality sanity check against the unquantized model's published
   behaviour, since cyankiwi publishes no accuracy-versus-BF16 numbers. A handful
   of LitQA2 questions answered sanely is enough; this is a smoke test, not a
   measurement.
8. Measure single-stream decode tok/s and prefill on a realistic 20k prompt.
   **Gate:** extrapolate Track D wall-clock from it before committing. A dense
   27B reads roughly an order of magnitude more weight bytes per decode step
   than a 3B-active MoE, so a slowdown is expected; the question is whether it is
   1.5x or 5x. If the extrapolation exceeds ~24h for Track D, evaluate MTP
   speculative decoding before the clean run rather than after.

**Phase 1: config and code.** Section 3. Deploy retrieval, and confirm
`stage_qwen_tokenizer` actually picked up the NEW `tokenizer.json`.

**Phase 2: pilot.** Track D agentic at `--limit 20`, `egress=full`. Confirms
tool-call rate, abstention behaviour, verdict parseability and Brave burn per
query before spending the real budget. Compare tool calls/query against the old
8.61: a large jump is a cost signal, a large drop is a behaviour signal, both
matter.

**Phase 3: the clean run.** Sequence 4b in order, concurrency 1, one
uninterrupted window. Pre-flight: Brave balance funded and asserted (section 6),
`X-Munin-Egress` set per track and recorded. Batch work may share the node,
since vLLM holds only `gpu:vllm:1`.

**Phase 4: derived tracks + certification.** T11, faithfulness, risk-coverage,
`run_all --certify`, and the 4c paired old-vs-new comparison.

**Phase 5: paper update.** `RESULTS.md` gets a new dated section per track (never
edit old sections in place; the arc is the value). `PAPER.md` provenance block
changes model, and gains an explicit sentence that retrieval numbers are on the
old provenance by construction. `docs/paper-kit/` 04-METHODS, 05-RESULTS,
06-ABLATIONS, 08-LIMITATIONS and the scorecard bundle all follow.

**Phase 6: prose sweep.** The remaining ~100 `qwen3.6` mentions across README,
DESIGN, DECISIONS, agent-track docs.

### Time budget

Old-model anchors: Track D clean run was 4.4h for 199 x 3 arms at concurrency 1
(agentic 79.0s/query, bare 14.7s, RAG 9.1s).

| Item | Old | Estimate at 2x slower decode |
|---|---|---|
| Track D, 3 arms | 4.4h | 9 to 13h |
| LitQA2 answer | ~4h | 8h |
| C1 | ~2h | 4h |
| C2 pair | ~2h | 4h |
| Faithfulness scoring (GPU, judge only) | ~1h | 1h |
| Pilot + Phase 0 | - | 2h |
| **Total** | | **~28 to 32h** |

The 2x factor is a placeholder that **Phase 0 step 8 replaces with a
measurement**. This is GPU 1 time only, not whole-node time, so it can run
alongside batch work. Expect to need a second window if the first run trips
anything.

---

## 6. Brave Search API cost

### Pricing (checked 2026-08-24)

Brave's Search endpoint is **$5.00 per 1,000 requests**, 50 req/s. The free tier
was retired in February 2026 and replaced with **$5/month in credits** (~1,000
requests) on metered billing against a saved card. There is a separate Answers
endpoint at ~$4/1k plus token charges; Munin does not use it. Munin calls
`api.search.brave.com/res/v1/web/search`, so **Search-endpoint pricing applies**.

Unaffected by the checkpoint choice.

### Measured burn rate from the last clean run

`BRAVE_MAX_QUERIES=3` (`web.py:48`), so **one `web_search` tool call fans out to
up to 3 billed Brave requests**, serialized at `BRAVE_SEARCH_QPS=1`.

Observed on the 2026-07-27 clean run: 307 `web_search` calls across 199 agentic
questions (T11 scorecard), and the Track D note records ~1,093 actual Brave
calls for that run, i.e. **~3.6 billed requests per `web_search` call** once
retries are included. Track C1 is much heavier per query: 528 `web_search` calls
across 100 questions (5.28/query), which makes sense because the model is
hunting for papers that do not exist.

### Estimate for one full re-measurement pass

Only tracks that run at `egress=full` cost anything. `MUNIN_EVAL_EGRESS`
defaults to `off`, and the bare and RAG arms make no tool calls at all.

| Run | Egress | Queries | web_search (old model) | Brave reqs @3.6x | Cost |
|---|---|---|---|---|---|
| Phase 0 + pilot (limit 20) | full | ~20 | ~35 | ~130 | $0.65 |
| Track D agentic | full | 199 | 307 | ~1,105 | $5.53 |
| Track D bare + RAG | full | 398 | 0 | 0 | $0.00 |
| Track C1 fabricated | full | 100 | 528 | ~1,900 | $9.50 |
| LitQA2 answer | off | 199 | 0 billed | 0 | $0.00 |
| Track C2b headline pair | off | 100 | 0 billed | 0 | $0.00 |
| Track C2b `egress=full` pair (optional, for comparability with the old pair) | full | 100 | ~528 est | ~1,900 | $9.50 |
| **One clean pass** | | | | **~5,035** | **~$25** |

### Recommended provisioning

- **One clean pass: ~$25.** ~$16 if the optional C2 `egress=full` pair is dropped.
- **Realistic total: $60 to $75.** Assume one shakedown pass that trips something
  plus one clean pass, and note the new model's tool-call rate is unknown. A
  chattier model could plausibly double `web_search` volume; the pilot in Phase 2
  is what turns this from a guess into a number, and it costs under $1 to find out.
- **Provision $100** on the card so a mid-run 402 is impossible.

**Why the ceiling matters more than the mean.** A Brave 402 or 429 mid-run does
not fail loudly: `web_search` degrades and the run keeps going. That is exactly
the failure mode that produced the misleading 0.688 agentic figure on 2026-07-26
and cost a full re-run. So: fund before starting, assert the balance in the
Phase 3 pre-flight, and grep the run log for 402/429 before certifying, the same
way the 07-27 run did.

**Knob, deliberately not turned.** `BRAVE_MAX_QUERIES=3 -> 1` would cut the bill
by ~3x, but it changes the system under test. Do not touch it for the headline
run.

**Not Brave, but adjacent:** the same run made 451 `semantic_scholar_search` and
221 `web_fetch` calls. S2 is free but rate-limited, and `web_fetch` already runs
a 45% error rate. Neither costs money; both are worth watching in the log.

---

## 7. Risks, highest first

1. **Throughput.** Dense 27B vs 3B-active MoE. Could turn a long weekend into a
   week. Measured in Phase 0 step 8, mitigated by MTP if needed.
2. **KV headroom.** ~2.3x concurrency at 64k versus 4.74x today, on a projection
   that assumes ~3 GB of non-KV overhead. Read the real number in Phase 0 step 1
   before trusting it. Fallbacks: util 0.93, then TP=2.
3. **Quant quality unverified.** cyankiwi publishes no accuracy-versus-BF16
   numbers. Mitigated by recipe identity with the model already in production and
   by the Phase 0 step 7 sanity check. If accuracy craters, compare against
   `Qwen/Qwen3.8-27B-FP8` before concluding anything about the model itself.
4. **Stale tokenizer.** `deploy.sh` `VLLM_MODEL_DIR` not updated means context
   budgeting silently uses the old tokenizer. Fails quietly, corrupts trimming.
5. **Shadow stack drift.** `docker-compose.shadow.yml` must move to the new model
   or Track C2's paired arms differ by model as well as by corpus, which destroys
   the pairing.
6. **`vllm_answer.py` MODEL literal.** The bare arm bypasses the gateway. Miss it
   and the headline ablation compares two different models to each other.
7. **Tool-call parser.** `qwen3_xml` vs `qwen3_coder`. Downgraded from top risk
   because `qwen3_xml` parses the Qwen3.5-family MoE in production today. Still
   gated in Phase 0 step 5, because the entire harness is tool calls.
8. **Silent Brave exhaustion.** Section 6.
9. **Confounding.** Context window, `reasoning_effort` and MTP all held fixed
   through the measurement, deliberately.

---

## 8. Open questions before starting

1. **Re-run the retrieval tracks anyway?** Recommendation is no (section 4a). Say
   if you want them re-run for a uniform provenance line regardless.
2. **Keep the old-model results as a model-sensitivity comparison?** (Section 4c.)
   Recommendation is yes; it is free and it strengthens the central claim.
3. **`reasoning_effort`:** leave at model default for the headline (recommended),
   or pin a value?
4. **Brave budget:** confirm ~$100 provisioned.
5. **Optional C2 `egress=full` pair:** run it (+$9.50) for comparability with the
   old pair, or skip it since the headline C2 is `egress=off`?

*(Withdrawn after the cyankiwi checkpoint review: "accept the permanent loss of
GPU 0 to TP=2?" TP=2 is no longer required. See the revision note in section 1.)*
