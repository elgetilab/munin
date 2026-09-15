# Model swap: Qwen3.6-35B-A3B -> Qwen3.8-27B (cyankiwi AWQ-INT4), and the paper re-measurement

Status: **DONE 2026-08-26** for steps 1 and 2 (model switched, TP=2 64k
profile live; Track D, per-arm faithfulness and T11 re-run on the same 199
questions, git `3e0bcfb`, scorecards `2026-08-26_*`). Step 3 landed for
`RESULTS.md` and `PAPER.md` (2026-08-27) and for `docs/paper-kit/` on
2026-09-14 (`PAPER-KIT-REFRESH-PLAN.md`, beside this file). The standalone
answer track and C1 were re-run on Qwen3.8 on 2026-09-14, C2b and
risk-coverage on 2026-09-15; nothing on the retired model remains a headline. Written
2026-08-24. Revised 2026-08-25: retargeted to the cyankiwi checkpoint (revision
note in section 1), and restructured from seven phases to three steps after the
existing switch procedure was found (note below). Moved to `done/` 2026-09-14.

Companion to `EVAL-SUITE-MASTER-PLAN.md` and `T2-ABLATION-REFRESH-PLAN.md`.
Supersedes nothing until the re-run lands.

> **Structure note.** The first draft laid this out as Phases 0 through 6. That
> was plan-document inflation, not real structure: "Phase 1: config and code"
> was re-deriving the checklist that already lives in `backend/README.md` ->
> Common Tasks -> "Switch LLM model", "Phase 4" was one flag on a command that
> "Phase 3" already runs, and "Phase 6" was a grep. There are three steps:
> **switch the model**, **re-run the benchmarks that depend on it**, **update
> the paper**. The mechanical checklist stays in the README where it belongs
> and is not duplicated here.

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
format, at the same group size as the model already in production, the swap
itself is close to a drop-in. Section 2 shows the existing single-GPU serving
profile survives. **The re-measurement, not the swap, is the work.**

---

## 1. Facts verified (not assumed)

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
that it also parses the dense sibling. Step 1 still A/Bs it.

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
for any of these, so a quality sanity check stays in step 1 regardless; the
difference is that cyankiwi has a track record in this exact system.

> **Revision note.** The first draft of this plan was costed against
> barrydeen's 27.8 GB checkpoint and concluded that TP=2 was forced and that
> GPU 0 would be permanently lost to batch work. That conclusion was an artifact
> of the checkpoint, not of the model, and does not hold for the cyankiwi build.

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

**MEASURED on the live service 2026-08-25** (job 964, single GPU, util 0.90).
The projection in the first draft was ~153,000 tokens / 2.3x; the real overhead
was lower than assumed, so the actual figures are better:

| term | projected | **measured** |
|---|---|---|
| weights | 21.0 GB | 18.91 GiB |
| KV pool | ~4.9 GB | **6.82 GiB** |
| KV tokens | ~153,000 | **206,802** |
| worst-case seqs at 65,536 | ~2.3x | **3.16x** |

**`--max-num-seqs` STAYS AT 2. Do not raise it.** This is the one number most
likely to be "optimised" by someone reading the throughput table below, so the
reasoning is recorded here in full.

The admission limit is set by the WORST case, not the average, because vLLM must
be able to hold `max_num_seqs` sequences each at `max_model_len`. Compare the two
models on that basis:

| | KV pool | worst-case seqs at 65,536 | margin at `--max-num-seqs 2` |
|---|---|---|---|
| old Qwen3.6-35B-A3B | 310,827 tok | 4.74x | 2.37x |
| **new Qwen3.8-27B** | 206,802 tok | **3.16x** | **1.58x** |

The new model has **less** worst-case headroom than the model that ran at 2, not
more, because its per-token KV is 3.2x larger (16 full-attention layers x 4 KV
heads x head_dim 256 = 32 KB/token, against ~10 KB on the old MoE). Raising the
cap to 6 would need 6 x 65,536 = 393,216 tokens against 206,802 available, i.e.
**1.9x oversubscribed**, which forces preemption precisely on the heavy fan-out
turns where it costs most. Preemption is also more expensive here than on a pure
attention model: recomputing Gated DeltaNet recurrent state is not a plain KV
recompute. 3 is the arithmetic ceiling, with no margin.

**Do not reason from average KV usage.** Sampling the engine log during live
turns shows only ~8-9% of the pool per running request, which naively suggests
~11 concurrent. That figure is drawn from sub-agent and tool calls with small
prompts. Main turns measured 27,332 and 37,040 prompt tokens, and the backend
trims to `VLLM_MAX_CONTEXT=60000`, so a worst-case turn genuinely approaches the
full window. The average is not the constraint.

**Throughput, measured** (300-token generations, thinking off): 79.2 tok/s at
concurrency 1, **143.1 tok/s at 2** (1.81x, so batching is nearly free), and flat
at ~143 for 4 and 8 because everything above 2 queues. Prefill runs 1,250-2,955
tok/s. Single-stream decode is ~77 tok/s steady state.

One consequence worth knowing: a SINGLE backend turn was observed at
`Running: 2, Waiting: 3`, because the agentic harness fans out internally. The
cap therefore throttles one user's turn, not just concurrent users. That is a
real latency cost, and the honest answer is that it is the price of the KV
budget rather than a misconfiguration to be tuned away. The levers that do not
oversubscribe KV are `--enable-prefix-caching` (the agent fan-out shares a long
system-prompt prefix), MTP speculative decoding, and the TP=2 profile.


**The deployment architecture therefore does not change.** Both scripts stay:
`start-vllm-service.sh` (single GPU, 64k) remains the default with GPU 0 free
for batch work, and `start-vllm-service-tp2.sh` (both cards, 128k) remains the
optional large-window profile. The ~30 hours of benchmark time in section 4
does **not** need exclusive whole-node time.

Serve-line changes relative to the current script: new `MODEL_ID` / `MODEL_PATH`
/ `MODEL_NAME`, and **drop `--dtype float16`**, because the checkpoint carries
BF16 tensors for every preserved module and forcing fp16 on them is gratuitous.
Everything else (`--quantization compressed-tensors`, `--kv-cache-dtype fp8`,
`--max-model-len 65536`, `--max-num-seqs 2`, `--enable-auto-tool-choice`,
`--reasoning-parser qwen3`) is unchanged.

**Held back deliberately.** MTP speculative decoding
(`--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`) is
distribution-preserving in principle and the MTP head is preserved at BF16 in
this checkpoint, but it is an untested interaction with the reasoning parser on
this build. A throughput optimisation must not be introduced in the same change
as the measurement. Enable it after the clean run, measure the speedup, adopt it
for production separately.

Hold `--max-model-len` at **65536** for the whole re-measurement, matching the
profile every existing committed number was produced under. Raising it would
confound "new model" with "bigger window". The 262k native headroom is future
work and deserves its own ablation.

---

## 3. Step 1: switch the model (half a day)

**The mechanical checklist is `backend/README.md` -> Common Tasks -> "Switch
LLM model", and is not repeated here.** It was updated on 2026-08-25 to cover
all eleven places the model is named; the previous version named three and
would have left a swap half-applied. `SETUP-CLUSTER.md` section 4 now points at
it rather than carrying a competing short list.

Two items on that checklist deserve emphasis for this particular swap:

- **`deploy.sh` `VLLM_MODEL_DIR`.** `stage_qwen_tokenizer` copies `tokenizer.json`
  out of it. Miss it and the retrieval container keeps budgeting context with
  the old tokenizer: no error, just wrong trim decisions in every long turn.
- **Persona sampling params.** `shared/personas/*.json` carry
  `params.temperature` (1.0 chat/research, 0.6 code), `top_p` 0.95, `top_k` 20,
  `min_p` 0.0, `presence_penalty` (1.5 chat/research, 0.0 code). These are
  Qwen3's recommended set. If Qwen3.8 recommends different values, changing them
  is legitimate but it changes the system under test, so decide **before** the
  clean run and hold it fixed throughout.

Then, before any benchmark is trusted, in order:

1. Read `GPU KV cache size` and `Maximum concurrency` out of the startup log.
   **Gate:** at least ~131,000 tokens (2x at 65,536). If lower, raise
   `--gpu-memory-utilization` to 0.93 and re-read; if still short, fall back to
   the TP=2 profile and reinstate the whole-node scheduling assumption.
2. `/v1/models` reports `max_model_len` 65536.
3. A plain completion works.
4. `chat_template_kwargs: {"enable_thinking": false}` returns 200, not 400.
   A 400 on this field is the documented failure signature and would mean every
   mechanical sub-task (summarise, query expansion, equation OCR) is broken while
   plain chat looks fine.
5. **Tool calling.** Drive one multi-tool turn through the live chat and confirm
   parsed `tool_calls`. A/B `qwen3_xml` against `qwen3_coder`. Prior is
   `qwen3_xml`.
6. One vision turn through `vision.py`.
7. Quality smoke: a handful of LitQA2 questions answered sanely, since cyankiwi
   publishes no accuracy-versus-BF16 numbers. A smoke test, not a measurement.
8. Single-stream decode tok/s and prefill on a realistic 20k prompt.
   **Gate:** extrapolate Track D wall-clock before committing to step 2. A dense
   27B reads far more weight bytes per decode step than a 3B-active MoE, so a
   slowdown is expected; the question is whether it is 1.5x or 5x. If Track D
   extrapolates past ~24h, evaluate MTP speculative decoding first.

---

## 4. Step 2: re-run the benchmarks that depend on the model (~30h GPU)

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

One honest caveat for Limitations: in *production* the AgentRetriever expands
queries with the live LLM, so real-world expansion quality does move with the
model. The benchmark freezes it; that drift shows up in Track D/E end-to-end
numbers, not in Track A. This is already the documented position.

### 4b. Model-dependent: MUST re-run

**Arm-matching (FIXED 2026-08-25, `vllm_answer.py`).** The bare and RAG arms
call vLLM **directly** on port 8000, bypassing both the gateway and
`_raw_chat_proxy`, so anything the backend applies to the agentic arm has to be
restated for them or the arms differ by more than the harness. Three things had
drifted and are now resolved:

| | was | now |
|---|---|---|
| model | `"qwen3.6-35b-a3b"` hardcoded | `config.VLLM_MODEL_NAME` (env-overridable) |
| reasoning effort | unset, i.e. Qwen3.8's `xhigh` | `config.LLM_REASONING_EFFORT` = `medium`, matching the agentic path |
| output budget | `max_tokens=4096` | `config.MAX_OUTPUT_TOKENS` = 16384, matching `chat_context.DEFAULT_MAX_OUTPUT_TOKENS` |

The middle row was the dangerous one. At `xhigh` one measured question consumed
11,374 completion tokens and returned an **empty** answer at an 8K cap, so a 4K
cap at `xhigh` would have returned empty content on a substantial fraction of
questions. The scorer counts that as unparseable/abstain, which would have
**depressed the bare arm and inflated the headline harness delta** for a reason
having nothing to do with the harness. Same class of artifact as the 0.688
search-degraded run, and harder to spot because every component looks healthy in
isolation.

The constants live in `munin_bench/config.py` under the same env var names the
backend uses (`LLM_REASONING_EFFORT`, `VLLM_MAX_OUTPUT_TOKENS`), so one export
matches both sides. The client timeout also went 300s -> 900s to match the
agentic arm's deadline, so no arm is truncated by the client while the model is
still producing. Record the effort in the scorecard provenance.

**Still mismatched, and a judgement call for the run owner: sampling.** The
bare/RAG arms send `temperature=0.7` and no `top_p`/`top_k`/`presence_penalty`,
while the agentic arm inherits the research persona's `temperature 1.0,
top_p 0.95, top_k 20, presence_penalty 1.5`. This mismatch predates the model
swap and was present in the committed 0.839/0.302 run, so aligning it now would
change the arms' relationship to every prior number as well. Deliberately left
alone: decide before the clean run whether the ablation isolates *harness* (align
sampling) or *deployed configuration* (leave as is), and state the choice in the
scorecard either way.

Most of it is one command. `run_all` runs the three ablation arms, C1,
faithfulness (with its own capture), writes one committed scorecard and
certifies it:

    PYTHONPATH=$HOME/.cache/munin_bench_deps:. NEO4J_PASSWORD=... $PY \
      -m munin_bench.pipelines.run_all --tag qwen38-27b \
      --tracks litqa2-answer,faithfulness,abstention,ablation \
      --limit 199 --with-reliability --certify --date <D>

> **`--limit 199` is not optional.** `run_all` computes the ablation size as
> `n = args.limit or 100`, so the default gives a **100**-question run, while
> every committed headline number is n=199. Omitting the flag produces a
> scorecard that looks fine and is not comparable to anything. The same value is
> a harmless cap for the other three tracks (their pools are 189 and 100).

Note the deliberate omission of `beir-scifact,litqa2-retrieval` from `--tracks`,
per 4a.

Four things `run_all` does not cover, run separately:

| Track | Why separate | New generation? |
|---|---|---|
| C2b shadow pair | needs the second retrieval stack on :8081 (`docker-compose.shadow.yml`), `egress=off` | yes, 50 x 2 |
| T11 tool reliability | `munin_bench/toolreliability/`, reads Track D agentic telemetry | no, derived |
| Risk-coverage | `munin_bench.abstention.risk_coverage` over the ablation + C1 + C2 outputs | no, derived |
| Routing regression A0-A5 | `munin_bench.routing.routing_eval`. Not a paper number, but it is the deploy gate and a model swap is what it exists to catch | yes |

`--with-reliability` is the pong behavioural registry, **not** T11. The two are
easy to confuse because both are called reliability; only T11 is in the paper.

Smoke first: the same `run_all` line with `--limit 20` and a throwaway `--tag`.
That confirms tool-call rate, abstention behaviour, verdict parseability and
Brave burn per query for under $1. Compare tool calls/query against the old
8.61: a large jump is a cost signal, a large drop is a behaviour signal.

**Certification.** `--certify` gates against `certification_thresholds.json`,
whose thresholds came from the 2026-07-13 baseline with a ~15% margin and are
explicitly provisional. **Expect to decide, not just read, the verdict**: inside
the margin passes; above the old baseline means the thresholds should be
deliberately re-based; a fail is itself a finding worth reporting rather than a
reason to tune.

### 4c. Free bonus result

**Back the old artifacts up off-machine first.** This comparison reads
per-query arrays that exist ONLY in `ablation_runs/` (gitignored, untracked);
the committed scorecard has `per_arm` and `deltas` but no per-query data, and
`run_arm.run()` overwrites `ablation_runs/<arm>.json` in place. One re-run
destroys the old side of the comparison permanently. The 2026-07-27 set is
archived at `varghele@<vps>:~/backups/munin-bench-artifacts/` (sha256-verified
2026-08-25); hugin's home directory is NOT a backup, since it shares a disk with
the repo.

`munin_bench.pipelines.compare <old>.json <new>.json` does a paired bootstrap
over per-query arrays. The 199 LitQA2 questions are identical across the old and
new runs, so old-model vs new-model is a legitimate **paired** comparison at
zero extra compute. That gives the paper a model-sensitivity result it does not
currently have: how much of the 0.839 agentic accuracy is the harness and how
much is the specific backbone. Given the central claim is "the harness is what
produces the accuracy", a second backbone reproducing the harness delta is a
genuine strengthening. The MoE-to-dense jump makes it a more informative second
point than a same-family size bump would have been.

### 4d. Still not fillable by compute

Phase 4 local query pool, T3 stratum 2, T7 answer-level local pool. All three
are blocked on human query curation and two-annotator qrels. A new model does
not unblock them, and the paper should keep saying so.

### Pilot measurements, 2026-08-25 (TP=2, `egress=off`, no Brave spend)

Run through `run_arm`'s internals so nothing was written to `ablation_runs/`.

| arm | n | accuracy | abstain | unparseable | median | extrapolated 199q |
|---|---|---|---|---|---|---|
| bare | **199** | **0.422** (84) | 0.050 (10) | **0** | 7.2s | **0.43h** |
| rag | 3 | - | - | 0 | 7.3s | ~0.5h |
| agentic | 3 | 2 of 3 | 0 | **1** | 172s | see below |

**The old bare arm was under-measured, and the published harness delta is
partly an artifact of that.** The 2026-07-27 bare arm scored 0.302 with **33
unparseable** out of 199, at `max_tokens=4096`. Those 33 are truncations scored
as failures, not wrong answers. With the budget raised to 16,384 (matching the
agentic arm) the new bare arm returns **0 unparseable** and 0.422. Some of that
gain is the new model and some is purely the budget fix, and the two cannot be
separated from this run alone. The consequence for the paper is concrete: the
headline `agentic - bare = +0.538` was inflated by a bare arm that was losing
16.6% of its questions to truncation. Expect the re-run's delta to be
materially smaller, and say why in the write-up rather than presenting it as a
regression. **A fair old-vs-new comparison would need the old model re-scored at
16,384 too**, which is no longer possible: that checkpoint is not deployed.

**The 900s deadline is no longer reliably sufficient.** One of the three agentic
queries hit exactly `900.2s` after **24 tool calls** and returned unparseable;
the other two finished in 164s and 172s with 5 and 12 calls. Caveat: this pilot
ran at `egress=off`, where `web_search` returns a deliberate tool-failure, and
the harness spends calls working around it. That is the same mechanism behind
the 2026-07-26 search-degraded run (11.1 calls/query, 0.688). So the deadline
hit may be an egress artifact rather than a model property, and the agentic
timing here is **not** a usable estimate. Resolve it with a small `egress=full`
pilot before committing to the clean run, and budget for raising the deadline.

Extrapolating from the two clean agentic queries only: ~168s x 199 = **~9.3h**,
so Track D lands near **10-11h** total rather than the 9-13h guessed earlier.
Treat as provisional until the `egress=full` pilot.

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
| Smoke + step 1 | - | 2h |
| **Total** | | **~28 to 32h** |

The 2x factor is a placeholder that **step 1 item 8 replaces with a
measurement**. This is GPU 1 time, not whole-node time, so batch work can share
the node. Expect a second window if the first run trips anything.

Pre-flight for the clean run: Brave balance funded and asserted (section 5),
`X-Munin-Egress` set per track and recorded.

---

## 5. Brave Search API cost

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
calls, i.e. **~3.6 billed requests per `web_search` call** once retries are
included. Track C1 is much heavier per query: 528 `web_search` calls across 100
questions (5.28/query), which makes sense because the model is hunting for
papers that do not exist.

### Estimate for one full re-measurement pass

Only tracks that run at `egress=full` cost anything. `MUNIN_EVAL_EGRESS`
defaults to `off`, and the bare and RAG arms make no tool calls at all.

| Run | Egress | Queries | web_search (old model) | Brave reqs @3.6x | Cost |
|---|---|---|---|---|---|
| Step 1 + smoke (limit 20) | full | ~20 | ~35 | ~130 | $0.65 |
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
  chattier model could plausibly double `web_search` volume; the smoke run turns
  this from a guess into a number for under $1.
- **Provision $100** on the card so a mid-run 402 is impossible.

**Why the ceiling matters more than the mean.** A Brave 402 or 429 mid-run does
not fail loudly: `web_search` degrades and the run keeps going. That is exactly
the failure mode that produced the misleading 0.688 agentic figure on 2026-07-26
and cost a full re-run. So: fund before starting, assert the balance in the
pre-flight, and grep the run log for 402/429 before certifying, as the 07-27 run
did.

**Knob, deliberately not turned.** `BRAVE_MAX_QUERIES=3 -> 1` would cut the bill
by ~3x, but it changes the system under test. Do not touch it for the headline
run.

**Not Brave, but adjacent:** the same run made 451 `semantic_scholar_search` and
221 `web_fetch` calls. S2 is free but rate-limited, and `web_fetch` already runs
a 45% error rate. Neither costs money; both are worth watching in the log.

---

## 6. Step 3: update the paper

`RESULTS.md` gets a new dated section per re-run track. **Never edit old
sections in place**; the arc from SPECTER to BGE to the agent architecture is
itself a result, and the file's own header says numbers are copied from
scorecards rather than memory.

`PAPER.md`'s shared-provenance block changes model, and gains an explicit
sentence that the retrieval numbers are on the old provenance by construction
(section 4a). Then `docs/paper-kit/` 04-METHODS, 05-RESULTS, 06-ABLATIONS,
08-LIMITATIONS and the scorecard bundle follow.

Last, the prose sweep: ~100 remaining `qwen3.6` / `35b-a3b` mentions across
`README`, `DESIGN.md`, `DECISIONS.md` and the agent-track docs. A grep, not a
phase.

---

## 7. Risks, highest first

1. **Throughput.** Dense 27B vs 3B-active MoE. Could turn a long weekend into a
   week. Measured in step 1 item 8, mitigated by MTP if needed.
2. **KV headroom.** ~2.3x concurrency at 64k versus 4.74x today, on a projection
   assuming ~3 GB of non-KV overhead. Read the real number in step 1 item 1.
   Fallbacks: util 0.93, then TP=2.
3. **Quant quality unverified.** cyankiwi publishes no accuracy-versus-BF16
   numbers. Mitigated by recipe identity with the production model and the step 1
   item 7 sanity check. If accuracy craters, compare against
   `Qwen/Qwen3.8-27B-FP8` before concluding anything about the model itself.
4. **`run_all --limit` default.** n=100 instead of 199, silently. Section 4b.
5. **Stale tokenizer.** `deploy.sh` `VLLM_MODEL_DIR`. Fails quietly, corrupts
   trimming.
6. **Persona sampling params.** Part of the system under test. Decide once,
   before the clean run, and hold fixed.
7. **`vllm_answer.py` MODEL literal.** The bare arm bypasses the gateway. Miss it
   and the headline ablation compares two different models to each other.
8. **Shadow stack drift.** `docker-compose.shadow.yml` must move to the new model
   or Track C2's paired arms differ by model as well as by corpus.
9. **Tool-call parser.** `qwen3_xml` vs `qwen3_coder`. Low, because `qwen3_xml`
   parses the Qwen3.5-family MoE today. Still gated, because the harness is tool
   calls.
10. **Silent Brave exhaustion.** Section 5.
11. **Confounding.** Context window, `reasoning_effort`, MTP and persona sampling
    all held fixed through the measurement, deliberately.

---

## 8. Open questions before starting

1. **Re-run the retrieval tracks anyway?** Recommendation is no (section 4a).
2. **Keep the old-model results as a model-sensitivity comparison?** (Section 4c.)
   Recommendation is yes; it is free and it strengthens the central claim.
3. **`reasoning_effort`:** leave at model default for the headline (recommended),
   or pin a value?
4. **Persona sampling:** keep Qwen3's current values, or adopt whatever Qwen3.8
   recommends? Either is defensible; it has to be decided before the clean run.
5. **Brave budget:** confirm ~$100 provisioned.
6. **Optional C2 `egress=full` pair:** run it (+$9.50) for comparability with the
   old pair, or skip it since the headline C2 is `egress=off`?

*(Withdrawn after the cyankiwi checkpoint review: "accept the permanent loss of
GPU 0 to TP=2?" TP=2 is no longer required.)*
