# A third backbone for the harness: candidate review

Status: **DECIDED 2026-08-25.** The third backbone is **Qwen3.5-9B**, and there
is deliberately no second candidate for the 24 GB tier: a 24 GB card runs the
same model with more headroom, not a different model (section 6.1). Nothing is
implemented. Written 2026-08-25, alongside `done/MODEL-SWAP-QWEN38-PLAN.md`.

> **Correction, same day.** Sections 4 and 6 were re-priced after the real
> quantized checkpoints were found. The first draft estimated Qwen3.5-9B at
> 6.3 GiB at 4 bits by scaling the production model's density; the vendor-grade
> checkpoint is **11 GB on disk**, because a 9B model keeps proportionally much
> more at BF16 (vision encoder, embeddings, `lm_head`, all the linear-attention
> projections). The decision survives the correction, the unqualified "fits
> 16 GB at the production profile on Ampere too" claim does not. See 6.2.

The question: after Qwen3.6-35B-A3B (measured) and Qwen3.8-27B (about to be
measured), what is the right **third** generation model, chosen so that a group
with a single 16 GB or 24 GB card can re-run the harness and check the result?

Everything in section 2 and section 3 was verified against the installed vLLM
build on hugin (`0.20.2rc1.dev221+gac062147f`) and against this repository, not
assumed. Model facts in section 4 come from vendor model cards and configs and
are marked where they are estimates.

---

## 1. Decide what the third model is FOR, because it is two different experiments

These pull in opposite directions and the choice determines the shortlist.

**Experiment A, "the harness is not a Qwen artifact".** `08-LIMITATIONS.md` §3
lists **"One base model"** as an external-validity threat in its own words, and
notes the briefing that motivated Track D warns a stronger base model can
*hurt* a specialised harness. A backbone from a different lab, with different
pretraining data and a different tokenizer, is the only thing that closes it.
Qwen3.6 to Qwen3.8 does not: it is one family, one quantizer, one recipe.

**Experiment B, "an institute with one 24 GB card can reproduce this".** That is
a reproducibility floor, and its value is that it is *cheap to run*, not that it
is independent. Here the ideal model is the one that changes the fewest code
paths, so a bad number is attributable to the backbone rather than to plumbing.

They are not the same model, and a single run cannot serve both cleanly.
**Resolved 2026-08-25 in favour of Experiment B** (section 6): the run is a
hardware floor, not a cross-lab check. Section 1 is kept as written because the
cost of that choice, which is that "One base model" stays on the limitations
list, has to be stated in the paper rather than quietly dropped.

---

## 2. The binding constraint is KV geometry, not parameter count

This is the single most useful finding in this review, and it reorders the
obvious shortlist.

Munin serves `--max-model-len 65536` at `--max-num-seqs 2`, and the backend
budgets to `VLLM_MAX_CONTEXT=60000`. vLLM must be able to hold `max_num_seqs`
sequences each at `max_model_len`, so the worst case is what has to fit, and
main turns really do approach it (27,332 and 37,040 prompt tokens measured
during the Qwen3.8 swap). A 9B model with a fat KV layout can need more VRAM at
64k than a 20B model with a thin one.

### The arithmetic, anchored on a measurement

KV bytes per token = `full_attention_layers x 2 x kv_heads x head_dim x
bytes_per_element`. Sliding-window layers contribute a fixed per-sequence
amount (window-sized), not a per-token one.

Measured anchor, from the Qwen3.8 startup log on the 5090 (31.84 GiB card,
`--gpu-memory-utilization 0.90`): weights 18.91 GiB, KV pool 6.82 GiB, so
**non-KV overhead (activations, CUDA graphs, non-torch) is 2.93 GiB** on this
build at these settings. That number is used below; it is likely a little
generous for a smaller model, so the fits are conservative.

| card | usable at util 0.90 | minus overhead | budget for weights + KV |
|---|---|---|---|
| 16 GiB | 14.3 GiB | 2.9 GiB | **11.4 GiB** |
| 24 GiB | 21.6 GiB | 2.9 GiB | **18.7 GiB** |
| 32 GiB (hugin, for reference) | 28.7 GiB | 2.9 GiB | 25.8 GiB |

### KV cost at 64k, two sequences

| model | attn layout | KV/token (fp8) | 2 seqs at 64k, fp8 | 2 seqs at 64k, fp16 |
|---|---|---|---|---|
| Qwen3.8-27B (production) | 16 full of 64, 4 KV heads, hd 256 | 32.0 KiB | 4.00 GiB | 8.00 GiB |
| **Qwen3.5-9B** | 8 full of 32, 4 KV heads, hd 256 | 16.0 KiB | **2.00 GiB** | 4.00 GiB |
| **gpt-oss-20b** | 12 full of 24, 8 KV heads, hd 64 | 12.0 KiB | **1.50 GiB** | 3.01 GiB |
| **Gemma 4 12B** | 12 full of 48, 8 KV heads, hd 256 | 48.0 KiB | **6.28 GiB** | 12.56 GiB |
| **Ministral-3-14B** | 40 full of 40, 8 KV heads, hd 128 | 80.0 KiB | **10.00 GiB** | 20.00 GiB |
| Granite 4.x hybrid | ~1 attention layer in 10, plus Mamba state | near zero | ~0.3 GiB (est) | ~0.6 GiB (est) |

Ministral-3-14B, a 14B model, costs **2.5x the production 27B's KV per token**,
because it is full attention on every one of 40 layers. Gemma 4 12B costs 1.5x.
That is the whole story of which models fit.

### The card generation matters as much as the capacity, and this is a hard gate

Verified in the installed build: `vllm/platforms/cuda.py` gates
`supports_fp8()` on `has_device_capability(89)`, i.e. **Ada or newer**. So:

- **RTX 4060 Ti 16 GB, RTX 5060 Ti 16 GB, RTX 4090, RTX 5090, L4, L40S** (SM 89
  / SM 120): FP8 weights and `--kv-cache-dtype fp8` are available, and the
  fp8 column above applies.
- **RTX 3090 24 GB, RTX A4000 16 GB, A5000, A6000** (SM 86): no native FP8.
  Use INT4 (AWQ/GPTQ via marlin) for weights and assume **fp16 KV**, i.e. the
  right-hand column, double the memory.

An institute's "reliable access" 24 GB card is very often a 3090 or an A5000,
which is Ampere. Any recommendation that only works with fp8 KV is a
recommendation that does not work on the hardware the section title describes.
The fits table in section 4 reports both.

---

## 3. What the harness actually demands of a backbone (verified against the code)

A backbone is not interchangeable here. The harness is tool calls: 8.61 per
query in the committed T11 telemetry, with a `MAX_TOOL_CALLS_PER_MESSAGE` cap of
30 that fires on real turns.

| requirement | where it lives | consequence if the candidate misses it |
|---|---|---|
| **43 tool schemas** (`mcp/schemas.py`, 74 KB of definitions) plus a ~22 KB persona system prompt | every turn | small models degrade on tool selection long before they degrade on prose. This is the real capability floor, not MMLU |
| **A vLLM tool-call parser** for the checkpoint | `--tool-call-parser` | tool-using turns break while plain chat looks fine. This exact failure is called out in `backend/README.md` step 14 |
| **A reasoning parser**, or reasoning traces leak into `content` | `--reasoning-parser qwen3` today; `chat_service.py:893` reads `delta.reasoning_content` | answers become unparseable, which the scorer counts as abstain or wrong |
| **`chat_template_kwargs: {"enable_thinking": false}`** | `database.thinking_off_fields()` | mechanical sub-tasks (summarise, query expansion, equation OCR, memory extraction) emit and pay for reasoning they discard. Cost per query inflates for a plumbing reason, and Track D reports cost |
| **`chat_template_kwargs: {"reasoning_effort": ...}`** | `database.reasoning_effort_fields()`, default `"medium"` | a chat template that *raises* on an unknown kwarg 400s every turn. One that ignores it is fine. Gated by `LLM_THINKING_TOGGLE=0` if needed |
| **A `tokenizer.json`** in the model dir | `deploy.sh stage_qwen_tokenizer` -> `chat_context.QWEN_TOKENIZER_PATH` | context budgeting silently falls back to a char heuristic, so trimming decisions go wrong on exactly the long turns that matter. The mechanism is model-agnostic despite the name; only the env var is Qwen-flavoured |
| **A vision tower** (optional) | `vision.py`, equation OCR and the plot-critique loop | production loses a feature. Irrelevant to every paper number, since no benchmark track sends images |

### Parsers actually present in the pinned build

Enumerated from `vllm/tool_parsers/__init__.py` and `vllm/reasoning/__init__.py`
in `/opt/munin/services/vllm/venv`, so this is what the box can serve today
without a vLLM upgrade:

- **tool parsers**: `qwen3_xml`, `qwen3_coder`, `openai` (gpt-oss), `gemma4`,
  `functiongemma`, `granite4`, `granite`, `mistral`, `olmo3`, `glm45`, `glm47`,
  `minimax_m2`, `seed_oss`, `lfm2`, `hermes`, `llama3_json`, `llama4_pythonic`,
  `deepseek_v3x/v4`, `kimi_k2`, `xlam`, `pythonic`, `phi4_mini_json`.
- **reasoning parsers**: `qwen3`, `openai_gptoss`, `gemma4`, `granite`,
  `mistral`, `olmo3`, `nemotron_v3`, `seed_oss`, `minimax_m2`, `deepseek_r1`,
  `glm45`, `kimi_k2`, `step3`, `hy_v3`, `identity`.

Architectures registered (`model_executor/models/registry.py`):
`Gemma4ForCausalLM` and `Gemma4ForConditionalGeneration`, `GptOssForCausalLM`,
`Mistral3ForConditionalGeneration`, `GraniteMoeHybridForCausalLM`,
`Olmo3ForCausalLM`, `Qwen3_5ForConditionalGeneration` (the family Qwen3.5,
3.6 and 3.8 all use). `mxfp4` is a registered quantization method with
`get_min_capability() == 80`, so gpt-oss should load on SM 120.

### The harness carries Qwen-shaped patches, and this is a measurement risk

`chat_service.py` contains at least three workarounds written against Qwen
behaviour: the prose-clarification escape hatch at `:586` ("qwen3-coder
sometimes writes a clarification as prose instead of calling
`ask_clarification`"), the malformed-`ask_clarification` repair at `:2433`, and
the dict/string/None normalisation of `questions` at `:643`. A non-Qwen
backbone will not trigger those paths, and will fail in its own ways that have
no equivalent patch.

**The consequence for the paper**: a third-model run must log tool-call parse
failures and empty turns separately, and report them next to accuracy. Without
that split, a parser mismatch reads as a model result, which is the same class
of artifact as the `xhigh`/4096-token interaction that
`done/MODEL-SWAP-QWEN38-PLAN.md` §4b caught, and the same class as the 0.688
search-degraded run.

---

## 4. Candidates, priced

Weight sizes are anchored on the production checkpoint's realised density
(Qwen3.8-27B AWQ-INT4 g32 loads at 18.91 GiB, i.e. ~0.70 GiB per billion
parameters at 4 bits, ~1.05 at 8 bits). Rows marked (est) are extrapolations,
not measurements.

**That density does not extrapolate downward, and this review got it wrong
once.** The parts a good 4-bit recipe refuses to quantize (embeddings, `lm_head`,
vision tower, linear-attention projections) are close to fixed in size, so they
are a small fraction of a 27B checkpoint and a large fraction of a 9B one. The
published Qwen3.5-9B w4a16 checkpoint is 11 GB against a 19.3 GB BF16 release,
a 43% reduction, where the 27B recipe achieved 62%. **Price a small model from a
published checkpoint, never from a scaled ratio.**

| candidate | params | licence | weights | KV, 2x64k | 16 GiB card | 24 GiB card | parsers in build | vision |
|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-9B** (CHOSEN) | 9B dense, GDN+attn 3:1 | Apache 2.0 | **10.25 GiB w4a16** (RedHat, measured) / 7.14 GiB GPTQ-INT4 (community) | 2.0 fp8 / 4.0 fp16 | **qualified yes**: see 6.2 | yes, 4.2x to 8.5x margin | `qwen3_xml`, `qwen3` | yes |
| **gpt-oss-20b** | 21B MoE, 3.6B active | Apache 2.0 | 12.8 GiB MXFP4 | 1.5 fp8 / 3.0 fp16 | **no**, 14.3 of 11.4 | **yes**, 14.3 of 18.7 | `openai`, `openai_gptoss` | no |
| **Gemma 4 12B** (QAT w4a16-ct) | 13B incl. towers | Apache 2.0 | ~8.5 GiB (est) | 6.3 fp8 / 12.6 fp16 | no at 64k; yes at <=24k | **yes** (fp8 KV only), 14.8 of 18.7 | `gemma4`, `gemma4` | yes + audio |
| **Granite 4.1-8B** | 8B dense hybrid Mamba | Apache 2.0 | ~5.6 GiB INT4 (est) | ~0.3 (est) | **yes**, large margin | yes, very large margin | `granite4`, `granite` | no |
| Ministral-3-14B | 14B dense, full attn | Apache 2.0 | 9.8 GiB INT4 (est) | 10.0 fp8 / 20.0 fp16 | no | marginal: 19.8 of 18.7 at 2 seqs, fits at 1 seq or at 48k | `mistral`, `mistral` | yes |
| Ministral-3-8B | 8B dense, full attn | Apache 2.0 | ~5.6 GiB INT4 (est) | ~8.0 fp8 (est) | no | yes, 13.6 (est) | `mistral`, `mistral` | yes |
| Olmo 3-Think 7B | 7B dense | Apache 2.0 | ~4.9 GiB INT4 (est) | not priced | likely yes | yes | `olmo3`, `olmo3` | no |

### Qwen3.5-9B (Feb 2026, `Qwen/Qwen3.5-9B`)

32 layers in a 3:1 Gated DeltaNet / Gated Attention stack, so only **8 layers
carry a KV cache**, 4 KV heads at head_dim 256, 262,144 native context, vision
tower included, Apache 2.0.

The case for it is that it changes almost nothing. It is
`Qwen3_5ForConditionalGeneration`, the exact architecture family that has now
been proven twice on this box; `qwen3_xml` and the `qwen3` reasoning parser
apply unchanged; `enable_thinking` works; the vision path keeps working; and the
Qwen-shaped fallbacks in `chat_service.py` stay valid. Its KV layout is the
second thinnest in the review at 16 KiB/token, half the production model's.

The case against it is that it is the same lab, the same tokenizer, the same
data recipe. It answers "how small can the backbone be", not "does the harness
generalise". It is also a generation behind the production model (3.5 vs 3.8),
so the comparison mixes size with generation and the paper has to say so.

### Checkpoints, since the 16 GB claim rests entirely on which one is used

| checkpoint | on disk | loaded (est) | what stays BF16 | provenance |
|---|---|---|---|---|
| `Qwen/Qwen3.5-9B` | 19.3 GB | ~18 GiB | everything (BF16 release) | vendor |
| **`RedHatAI/Qwen3.5-9B-quantized.w4a16`** | **11 GB** | ~10.25 GiB | vision encoder, token embeddings, `lm_head`, linear attention | vendor-grade, Apache 2.0, **publishes recovery vs BF16** |
| `mssfj/Qwen3.5-9B-GPTQ-INT4` | 7.67 GB | ~7.14 GiB | not documented | community, no recovery numbers |

**Recommended: the RedHat w4a16 checkpoint**, because it is the only one of the
three that publishes accuracy recovery against BF16, which is the caveat
`done/MODEL-SWAP-QWEN38-PLAN.md` §7.3 has to carry for the cyankiwi checkpoints and
that this run can avoid carrying. Its published recovery is 97.9% to 100.1% on
instruction following and 94% to 99% on most benchmarks, with one visible
outlier at **80.5% on AIME 2025**. That outlier is worth knowing but is probably
not load-bearing here: LitQA2 is multiple-choice literature QA, not competition
maths. Its model card also documents a `--language-model-only` flag that drops
the vision encoder and its memory, which section 6.2 uses.

The 3.6 GB gap between the RedHat and community checkpoints is roughly the whole
16 GB question, which is why the checkpoint has to be named in the scorecard
provenance and not just the model.

Open items: whether `reasoning_effort` is accepted by the 3.5 chat template
(3.8 added it; if 3.5's template ignores unknown kwargs it is a no-op, if it
raises it 400s every turn), and whether the model card's own suggestion of
`--tool-call-parser qwen3_coder` beats the `qwen3_xml` Munin serves. Both are
step-1 A/Bs, not blockers.

### gpt-oss-20b (Aug 2025, `openai/gpt-oss-20b`)

21B MoE, 3.6B active, MXFP4-native, 24 layers alternating full attention and
128-token sliding window, 8 KV heads at head_dim 64, 131,072 context,
Apache 2.0, text only.

This is the best answer to Experiment A and it is not close. Different lab,
different data, different tokenizer, different architecture, and a **native
reasoning-effort dial with exactly the values the backend already sends**
(`low` / `medium` / `high`, and the default is `medium`). Both parsers it needs
are in the pinned build. Its KV layout is the thinnest of any candidate, so it
serves 64k more cheaply than the 9B Qwen. Being a year old and openly weaker
than the 2026 models is a feature for this purpose: if the harness delta
reproduces on a deliberately modest backbone, the claim is stronger, and if it
does not, that is the most interesting negative result the paper could report.

Three real costs. **It does not fit 16 GiB under vLLM at this context window**:
12.8 GiB of weights against an 11.4 GiB budget, before any KV at all. The
widely-quoted "runs in 16 GB" figure is a weights-plus-small-context claim from
edge runtimes, not a 64k, two-sequence vLLM claim, and the review should say so
plainly rather than repeat it. **It is text only**, so `vision.py` has no
backend during the run. **`enable_thinking: false` is not its mechanism**:
reasoning is always on in the harmony format, so every mechanical sub-task pays
for a reasoning trace unless the sub-task calls are switched to
`reasoning_effort: low`. Left unadapted, that inflates the cost column of
Track D for a plumbing reason.

Also unverified: MXFP4 kernels on SM 120. `get_min_capability()` is 80 and a
Triton path exists in the build, so the prior is good, but it is a step-1 gate,
not an assumption.

### Gemma 4 12B, QAT w4a16 compressed-tensors (`google/gemma-4-12B-it-qat-w4a16-ct`)

48 layers with 12 global-attention layers (8 KV heads, head_dim 256) and 36
sliding-window layers at window 1024, 256K context, Apache 2.0, text + image +
audio.

Two things make it attractive. First, it is an **official vendor QAT checkpoint
serialised in compressed-tensors**, which is the exact quantization path
production already runs (`--quantization compressed-tensors`), so it removes the
"community quant of unknown quality" caveat that section 7 of the Qwen3.8 plan
has to carry. Second, it is a genuinely different lab with `gemma4` tool and
reasoning parsers already in the build.

Against it: head_dim 256 on 8 KV heads makes it the second most KV-hungry
candidate here, 48 KiB/token, so it is a 24 GiB model at 64k and an **Ada-only**
one at that (12.6 GiB of fp16 KV puts it out of reach on a 3090). Its tool-call
format is a custom non-JSON serialisation, which is exactly the surface most
likely to interact badly with 43 schemas, and the build ships two plausible
parsers (`gemma4` and `functiongemma`) that would need an A/B like the
`qwen3_xml` vs `qwen3_coder` one.

### Granite 4.1-8B (Apr 2026, IBM)

The dark-horse pick. Hybrid Mamba-2 / transformer at roughly 9:1, so KV is
near-free and 512K context is claimed; Apache 2.0; IBM targets function calling
and RAG explicitly; `granite4` and `granite` parsers are in the build. On memory
alone it is the most comfortable candidate in the review by a wide margin, on
either card generation.

The reason it is not a top recommendation is verification debt: the registry
entry is `GraniteMoeHybridForCausalLM` (a Granite 4.0-H architecture), and
whether Granite 4.1's 8B dense hybrid presents that same architecture string in
this build is **unverified**, as are its layer counts (HF blocked the config
fetch during this review). It is also the least evidenced of the candidates on
agentic tool use at this scale. Worth a 30-minute check before dismissing,
because if it loads, it is the cheapest possible verification arm.

### Rejected, with reasons

| candidate | why not |
|---|---|
| **Mistral Small 4 (2603)** | It is **119B**. "Small" is now relative to Mistral Large 3. Out of scope entirely |
| **Ministral-3-14B / 8B** | Full attention on every layer at head_dim 128 makes them the most KV-hostile options here: 14B needs 10 GiB of fp8 KV for two 64k sequences, so it does not fit 24 GiB at `max-num-seqs 2`, and is impossible on Ampere. Only viable at 1 sequence or a shortened window, both of which change the measured configuration |
| **Olmo 3-Think 32B** | 22 GiB at INT4 leaves no KV budget on a 24 GiB card. The 7B is a legitimate fallback and has a unique selling point (weights **and** training data released, which fits the sovereignty story), but its ~65K context ceiling and lighter agentic evidence make it a second-tier pick |
| **Qwen3.6-27B dense** | Same size class as production, so not "smaller", and not a different family either. It answers neither experiment |
| **Qwen3-Coder-30B-A3B** | Coder-specialised, previous generation, ~17 GiB at INT4 plus KV puts it at the 24 GiB ceiling for no gain over gpt-oss-20b |
| **GLM / MiniMax-M2 / DeepSeek / Kimi / Llama 4** | All far past 24 GiB even at 4 bits |
| **Gemma 4 26B-A4B, Gemma 4 31B** | 31B does not fit; the 26B-A4B MoE is plausible at 24 GiB but adds nothing the 12B does not, at higher risk |

---

## 5. What the third model would actually cost to run

The third model does **not** need the full 30-hour suite, and this is the main
practical argument for doing it at all.

Section 4a of `done/MODEL-SWAP-QWEN38-PLAN.md` establishes that retrieval tracks are
model-independent by construction (AgentRetriever runs against the frozen
committed variant set). That still holds. But a third backbone does not need
the full *generation* suite either, because its job is to test one claim.

| track | run for the third model? | why |
|---|---|---|
| **Track D, 3 arms, n=199** | **yes** | this is the entire point. The paired agentic/bare/RAG comparison is the headline claim, and the third model either reproduces the delta or it does not |
| **T11 tool reliability** | yes, free | derived from Track D telemetry. Also the single best diagnostic for whether a non-Qwen tool parser is misbehaving |
| **C1 fabricated papers, n=100** | recommended | judge-free and fully automatic, so it is the cheapest possible check that abstention behaviour is a harness property rather than a Qwen property |
| LitQA2 standalone answer track | no | Track D's agentic arm already gives an accuracy on the same questions |
| Faithfulness (Track B) | optional | the headline there is a *null*, and a null on a third model is weak information for the GPU hours |
| C2b shadow pair | no | needs the second retrieval stack and doubles the run for a stratum whose headline is already established |
| Retrieval tracks | no | model-independent, per plan §4a |
| Routing regression | yes | it is the deploy gate, cheap, and a model swap is what it exists to catch |

Rough cost, scaled from the committed 4.4h Track D clean run: **9 to 14 hours of
single-GPU time** for Track D plus C1 plus routing on the chosen 9B (a 9B dense
hybrid reads far fewer weight bytes per decode step than the 27B dense, so the
2x placeholder in the Qwen3.8 plan does not apply here and the run may well come
in under the old model's wall-clock), and **$6 to $16 of Brave credit** at the
measured 3.6 billed requests per `web_search` call. That is roughly a third of a
full re-measurement pass.

One caveat on the cheap-looking hours: if the 9B over-tools, wall-clock rises
with the tool-call count rather than with the decode rate, and the
`MAX_TOOL_CALLS_PER_MESSAGE` cap of 30 turns that into truncated turns rather
than into a longer run. Watch calls per query against the 8.61 baseline in the
smoke, in **both** directions.

Add one gate that the Qwen3.8 plan already learned the hard way: **smoke at
`--limit 20` first**, and compare tool calls per query against the 8.61 baseline.
A large drop is a parser problem masquerading as a behaviour change.

---

## 6. Decision (2026-08-25)

**The third backbone is `Qwen/Qwen3.5-9B`, served from
`RedHatAI/Qwen3.5-9B-quantized.w4a16`. There is no second candidate for the
24 GB tier.**

The question the paper will answer with it is Experiment B from section 1: a
group can run this harness on a small self-hosted model. It keeps every harness
code path identical (same `Qwen3_5ForConditionalGeneration` family, same
`qwen3_xml` parser, same `qwen3` reasoning parser, same `enable_thinking`,
vision intact), so a bad number is attributable to the backbone rather than to
plumbing, and it is Apache 2.0 from a lab that keeps its old checkpoints
available.

### 6.1 Why no separate 24 GB model

A 24 GB card is not a different deployment tier for this model, it is the same
deployment with slack. Priced against the RedHat checkpoint at 10.25 GiB loaded
and 16 KiB/token of fp8 KV:

| card | KV pool | worst-case seqs at 64k | or, at `max-num-seqs 2` |
|---|---|---|---|
| 16 GiB (Ada+) | 1.26 GiB | 1.26x | 32k window |
| **24 GiB (Ada+)** | **8.46 GiB** | **8.46x** | 262k native window, still 3.2x |
| 24 GiB (Ampere, fp16 KV) | 8.46 GiB | 4.23x | 128k window comfortably |

So the 24 GB owner spends the extra 8 GiB on `--max-num-seqs`, on
`--max-model-len`, or on both, and gets a strictly better version of the same
system. Adding gpt-oss-20b to occupy that tier would have bought cross-lab
external validity (section 1, Experiment A), which is a real and separate gain,
but it is a second full Track D run and a second set of arm-matching risks.
**Deliberately not taken.** `08-LIMITATIONS.md` §3 therefore keeps its "One base
model" threat, narrowed: the harness delta will have been shown across two model
sizes and two architectures within one family, and not across labs. That
sentence should go in Limitations rather than being left for a reviewer to
notice.

### 6.2 The 16 GB claim, stated precisely

This is the one place the correction at the top of the document bites. At the
production profile (`--max-model-len 65536`, `--max-num-seqs 2`) the RedHat
checkpoint needs 2.0 GiB of fp8 KV against the 1.26 GiB a 16 GiB card leaves it.
**It does not fit unmodified.** Three honest versions of the claim, in
descending order of preference:

1. **`--language-model-only` on a 16 GB Ada-or-newer card**: dropping the vision
   encoder frees roughly 1.4 GiB, giving a ~2.65 GiB pool, i.e. **2.65x at 64k**.
   The production profile fits. Vision is not exercised by any benchmark track,
   so this costs the reproduction nothing. **This is the claim to make.**
2. **Full multimodal weights on a 16 GB Ada-or-newer card**: 64k at
   `max-num-seqs 1`, or 32k at 2. Fine for a single-user group, and concurrency
   1 is what the cost measurement uses anyway.
3. **16 GB Ampere (fp16 KV, e.g. RTX A4000)**: 0.63x at 64k with vision loaded,
   1.33x with `--language-model-only`. Single sequence at 64k only, or drop to
   32k. Do not claim the production profile here.

The first draft of this document asserted the production profile fits 16 GB "on
Ampere as well as Ada". That was an artifact of the scaled weight estimate and is
withdrawn.

**Make it measured rather than computed.** The benchmark runs on a 31.84 GiB
5090, so a "fits 16 GB" claim derived from arithmetic is exactly the kind of
thing a reviewer will ask about. Running the third-model Track D with
`--gpu-memory-utilization 0.45` on the 5090 reproduces a 16 GB card's budget at
util 0.90 to within a hundred MiB, which turns the claim into a measurement at
zero extra cost. Recommended, and cheap.

### 6.3 What holding "raise tokens or concurrency" fixed means for the numbers

Raising `--max-num-seqs` is the right advice for a group running Munin, and the
wrong thing to do while reproducing the paper. Every committed cost number is
wall-clock at **concurrency 1**, because `slurmdbd` is not deployed and
GPU-seconds cannot be read from `sacct` (`01-SYSTEM.md` §8). Accuracy should be
insensitive to the served ceiling; cost is not, and the suite already has one
run, the 2026-07-26 agentic 0.688, that was depressed by exactly this.

So the reproduce instructions need to separate the two: **`--max-num-seqs 2` and
concurrency 1 to reproduce the numbers; raise either freely for actual use.**
That belongs in `10-REPRODUCE.md`, not just here.

---

## 7. Risks specific to a third-model run, highest first

1. **Tool-call parse failures read as model quality.** The harness has
   Qwen-shaped fallbacks and none for anything else. Mitigation: log parse
   failures and empty turns separately, gate on the T11 error/degraded rates,
   and A/B the candidate parsers before the clean run (`openai` for gpt-oss;
   `gemma4` vs `functiongemma` for Gemma).
2. **`enable_thinking: false` silently not applying** (gpt-oss, and any model
   whose template ignores the kwarg). Mechanical sub-tasks then pay for
   reasoning, inflating the cost column that Track D reports. Mitigation:
   measure completion tokens on a summarise call before and after; switch those
   call sites to `reasoning_effort: low` if the toggle is inert.
3. **Sampling parameters are part of the system under test.** The personas carry
   Qwen's recommended `temperature 1.0 / top_p 0.95 / top_k 20 /
   presence_penalty 1.5`. These are wrong for gpt-oss and Gemma. Whichever way
   it is decided (vendor-recommended per model, or held fixed across models),
   the choice has to be made once, before the run, and stated in the scorecard.
   Holding them fixed makes the comparison cleaner but handicaps the new model;
   changing them makes each model run at its own best but adds a confound.
   Recommendation: **use each vendor's recommended sampling and say so**, since
   the claim is about the harness, not about a tuned configuration.
4. **The model literal lives in twelve places.** `backend/README.md` -> Common
   Tasks -> "Switch LLM model" is the canonical checklist. The two that fail
   *silently* are `deploy.sh VLLM_MODEL_DIR` (stale tokenizer, wrong context
   trimming) and `ablation/vllm_answer.py` (now resolved from
   `munin_bench.config`, but it is the arm that bypasses the gateway).
5. **fp8 KV on the target card.** Verified gate at compute capability 8.9. A
   result quoted as "fits a 24 GB card" is false for a 3090 unless it was priced
   with fp16 KV.
6. **Quantization quality.** Resolved by the checkpoint choice: the RedHat
   w4a16 build publishes recovery against BF16, which is more than either
   production checkpoint has ever had. Carry the AIME-2025 80.5% outlier into the
   scorecard note anyway, and keep the step-1 quality smoke test: it is a
   different quantizer from the one the other two runs used, so quant recipe is a
   third variable alongside size and generation.
7. **Reading a small model's abstention as calibration.** A weaker backbone
   abstains more. Track C numbers from a third model are a *different* operating
   point, not a better or worse one, and pooling them with the headline would be
   invalid in the same way pooling across `egress` settings is.

---

## 8. Open questions, after the decision

Resolved on 2026-08-25: **which experiment** (B, the hardware floor),
**which model** (Qwen3.5-9B), **which tier** (16 GB stated, 24 GB is headroom),
and **no second model for the 24 GB tier**. What is left:

1. **Which 16 GB variant is the headline claim?** Section 6.2 recommends
   `--language-model-only`, which fits the production profile on an Ada-or-newer
   16 GB card. Confirm that dropping vision from the reproduction is acceptable
   (no benchmark track sends images, so this is a presentation choice).
2. **Does the Ampere case need to hold?** If the claim must cover an RTX A4000,
   it is a single sequence at 64k or a 32k window, and the wording has to say so.
3. **Emulate the 16 GB budget on the 5090** with `--gpu-memory-utilization 0.45`
   so the fit is measured rather than computed? Recommended, near-zero cost.
4. **Sampling parameters.** The personas carry Qwen3's recommended set. If
   Qwen3.5-9B recommends different values, decide once before the clean run and
   hold it fixed. Same rule as `done/MODEL-SWAP-QWEN38-PLAN.md` §8.4.
5. **`reasoning_effort` on the 3.5 chat template**: no-op, or 400? One curl in
   step 1. If it raises, set `LLM_THINKING_TOGGLE=0` or blank
   `LLM_REASONING_EFFORT`, and record which, because it changes the arm matching
   that `munin_bench/config.py` exists to enforce.
6. **`qwen3_xml` vs `qwen3_coder`**: the model card suggests the latter. A/B it
   in step 1, same as the Qwen3.8 swap does.
7. **Track scope**: is section 5's reduced set (Track D + T11 + C1 + routing)
   accepted, or does the paper want full parity with the two Qwen runs?
8. **Three variables move at once** (size 27B to 9B, generation 3.8 to 3.5,
   quantizer cyankiwi to RedHat). That is unavoidable and fine, but the paper
   should attribute the delta to "a smaller backbone" rather than to size
   specifically.

---

## 9. Sources

Vendor and documentation sources consulted 2026-08-25. Everything about the
installed vLLM build, this repository, and the memory arithmetic was verified
locally and is not sourced below.

- [Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B) (config, licence,
  tool-use and thinking-mode notes)
- [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b) and
  [Introducing gpt-oss](https://openai.com/index/introducing-gpt-oss/)
- [google/gemma-4-31B-it-qat-w4a16-ct](https://huggingface.co/google/gemma-4-31B-it-qat-w4a16-ct)
  and [google/gemma-4-12B-it](https://huggingface.co/google/gemma-4-12B-it)
- [mistralai/Ministral-3-14B-Instruct-2512](https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512)
  and the [vLLM Ministral-3 recipe](https://github.com/vllm-project/recipes/blob/main/Mistral/Ministral-3-Instruct.md)
- [mistralai/Mistral-Small-4-119B-2603](https://huggingface.co/mistralai/Mistral-Small-4-119B-2603)
  (the size check that removed it from scope)
- [IBM Granite 4.0 announcement](https://www.ibm.com/new/announcements/ibm-granite-4-0-hyper-efficient-high-performance-hybrid-models)
  and [ibm-granite/granite-4.0-h-small](https://huggingface.co/ibm-granite/granite-4.0-h-small)
- [Ai2 Olmo 3](https://allenai.org/blog/olmo3)
- [vLLM tool calling documentation](https://docs.vllm.ai/en/stable/features/tool_calling/)
