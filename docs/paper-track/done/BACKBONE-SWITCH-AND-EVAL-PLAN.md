# Backbone instances, and the gpt-oss-20b full-suite measurement

Status: **RUN COMPLETE 2026-09-16** (suite 23:22 to 06:02 UTC unattended;
results in RESULTS.md "Third backbone", PAPER.md and the paper kit). Two
field fixes landed during the run: instance vLLM binds 0.0.0.0 and needs a
ufw allow from 172.16.0.0/12 to its port; instances run at util 0.80 because
GPU 0 carries 2.3 GiB of other processes. One post-run fix: the TP=2 restore
raced `vllm-service start` against the completing job (production down
06:03 to 08:03). Implemented 2026-09-15 (evening). Commits `d46096f`
(bench autonomy), `effa344` (model profiles, sampling migration, `/api/models`),
`5900770` (sudoers), `e578f8b` (instances + gates), `dc7cae5` (driver, shadow
corpus, per-track resume). What was built matches sections 2, 4 and 5 below
with these naming differences: the driver is
`backend/benchmarks/scripts/run_suite.sh <slug>`; instance state lives under
`/opt/munin/instances/<name>/`; the shadow corpus is built and dropped by
`munin_bench.abstention.shadow_corpus`; the gates are
`scripts/vllm/backbone_gates.py`; per-backbone scorecards carry a `-<slug>`
suffix. Awaiting: the sudoers install (one root command), the deploy of
`effa344` to production (`deploy.sh vllm`, `retrieval`, `model activate
qwen3.8-27b`, `personas`), and the owner's TP=2 to single switch. Then
`scripts/run_suite.sh gpt-oss-20b`. Supersedes two same-day drafts (parallel
stack; full swap).
Implements `THIRD-MODEL-REVIEW.md` Experiment A, the cross-lab check, with
`openai/gpt-oss-20b`; the Qwen3.5-9B hardware-floor run (Experiment B) is
deferred and its config file is not written.

Decisions taken in discussion, so nobody re-opens them here:

| decision | taken |
|---|---|
| second backbone | **gpt-oss-20b**, full generation suite |
| layout | **two instances at once**: production Qwen3.8 keeps serving users on GPU 1 (single-GPU profile, slower for them); gpt-oss on GPU 0 with its own retrieval container, **eval-only**, never user-visible |
| SLURM batch jobs | none possible during the run; accepted |
| the pipeline | model configs as committed files; `deploy.sh instance up/down/ls`; production stays the fixed primary, "production as just another instance" goes on the TODO, not done now |
| `GET /api/models` | added on the backend, **production instance only**; the VPS gateway proxies it, no per-swap VPS edit again |
| sampling | **moves out of personas into the model profile** (`default` + `code` sets); Qwen profiles reproduce today's values byte for byte; gpt-oss uses OpenAI's recommendation. Bench bare/RAG arms keep their historical `temperature 0.7`, stated |
| model configs written now | `qwen3.8-27b` (production), `gpt-oss-20b`, `qwen3.6-35b-a3b` (record). **Not** `qwen3.5-9b` |
| 16 GB emulation | dropped |
| faithfulness judge | **CPU** (`MUNIN_BENCH_ENTAILMENT_DEVICE=cpu`); gpt-oss keeps util 0.90 on its card |
| autonomy | sudoers rule for the root steps; driver runs the whole thing unattended, no pause after the smoke; nightly vLLM stop disabled for the window and restored after |
| profile switch TP=2 → single | **done by hand by the owner before the run**; driver verifies, does not switch. Driver **does** restore TP=2 at the end |
| end state | gpt-oss instance torn down, production back on TP=2 Qwen3.8 |
| Brave | provisioned, no action |
| 08-26 Qwen3.8 arrays | **backed up 2026-09-15** to `varghele@<vps>:~/backups/munin-bench-artifacts/munin-bench-artifacts-2026-09-15-qwen38-complete.tar.gz`, sha256 `3aa5d909…bfbc`, 36 JSON files, verified remotely |

---

## 1. What a switch touches today (audited 2026-09-15)

The README checklist names twelve places. The real count is eighteen, and the
misses fail quietly.

| # | where | what | class |
|---|---|---|---|
| 1 | `scripts/vllm/start-vllm-service.sh` | `MODEL_ID` / `MODEL_PATH` / `MODEL_NAME`, parsers, `--quantization`, banner | serving |
| 2 | `scripts/vllm/start-vllm-service-tp2.sh` | same, independently | serving |
| 3 | `deploy.sh:89` `VLLM_MODEL_DIR` | source of the staged tokenizer; miss it and context budgeting silently uses the old tokenizer | serving, **silent** |
| 4 | `docker/docker-compose.yml:275` | `VLLM_MODEL_NAME=qwen3.8-27b` **hardcoded, not `${}`-substituted** | backend, **blocker** |
| 5 | `docker/docker-compose.yml:486-495` | optional `vllm` container profile: model, name, parsers | backend |
| 6 | `docker/docker-compose.yml` | `LLM_THINKING_TOGGLE` / `LLM_REASONING_EFFORT` **not passed through**; container only sees Python defaults | backend, **blocker for gpt-oss** |
| 7 | `retrieval/database.py:63,139-171` | `DEFAULT_LLM_MODEL_NAME`; `thinking_off_fields()` emits only `enable_thinking: false`, inert on gpt-oss | backend |
| 8 | `retrieval/main.py:1209` | second `VLLM_MODEL_NAME` fallback literal | backend |
| 9 | `retrieval/chat_context.py:86` | `QWEN_TOKENIZER_PATH` (mechanism model-agnostic, name not) | backend |
| 10 | `config/munin.env.template`, `config/munin-embedding-map.service` | literals; systemd `Environment=` line | backend |
| 11 | `frontend/gateway/main.py:809` | `/v1/models` synthesised from an env the VPS never sets, so the literal | VPS |
| 12 | `frontend/webui/src/components/Settings.tsx:566` | display literal | VPS |
| 13 | `.env.example` | published defaults + "results used X" | docs |
| 14 | `munin_bench/config.py:73,91` | `VLLM_MODEL_NAME`, `LLM_REASONING_EFFORT` fallbacks | bench |
| 15 | `munin_bench/deep_research/eval_dr.py`, `frozen_variants/regen_variants.py`, `routing/routing_eval.py`, `scripts/knowledge/build_embedding_map.py` | raw `{"enable_thinking": false}` literals | bench, inert on gpt-oss |
| 16 | `docker/docker-compose.shadow.yml` | `extends` of production; follows automatically. Subsumed by the instance generator (§2.3) | none |
| 17 | `shared/personas/*.json` `params` | sampling: chat/research 1.0 / 0.95 / 20 / 0.0 / 1.5, code 0.6 / 0.95 / 20 / 0.0 / 0.0 | **moves**, §2.4 |
| 18 | `retrieval/chat_service.py:586,643,2433` | Qwen-shaped fallbacks | untouched; parse failures logged separately so they read as what they are |

Compose's `.env` is a symlink to `/opt/hugin/config/cluster.env`
(HuginSLURM-owned, root-only). The start scripts already `export` window
variables before `docker compose up` so compose substitutes them; the model
variables ride the same mechanism. `cluster.env` is not touched.

---

## 2. The pipeline

### 2.1 `backend/config/models/<slug>.env`, deployed to `/opt/munin/config/models/`

One committed file per backbone: everything a switch needs, nothing it can
derive. Three files now.

```
# config/models/gpt-oss-20b.env
MODEL_SLUG=gpt-oss-20b
MODEL_ID=openai/gpt-oss-20b
MODEL_DIR=gpt-oss-20b                      # under $MUNIN_ROOT/data/models
MODEL_NAME=gpt-oss-20b                     # served name
MODEL_DESC="gpt-oss-20b (MoE 21B / 3.6B active, native MXFP4, text only)"
HF_EXCLUDE="original/* metal/*"            # 38 GB otherwise
VLLM_QUANTIZATION=                         # empty = auto-detect (mxfp4); compressed-tensors for the Qwens
VLLM_KV_CACHE_DTYPE=fp8
VLLM_TOOL_PARSER=openai
VLLM_REASONING_PARSER=openai_gptoss
VLLM_EXTRA_ARGS=
VLLM_GPU_MEM_UTIL=0.90
VLLM_MAX_MODEL_LEN=65536
VLLM_MAX_NUM_SEQS_SINGLE=2                 # held at 2 for the measurement; §2.5 gate confirms the pool covers it
VLLM_MAX_NUM_SEQS_TP2=8
LLM_THINKING_MODE=effort_low               # enable_thinking | effort_low | none
LLM_REASONING_EFFORT=medium                # blank = do not send
KV_KIB_PER_TOKEN=12
# sampling profile (§2.4). OpenAI's recommendation for gpt-oss.
SAMPLING_DEFAULT='{"temperature": 1.0, "top_p": 1.0}'
SAMPLING_CODE='{"temperature": 1.0, "top_p": 1.0}'
```

```
# config/models/qwen3.8-27b.env  (must reproduce production exactly)
MODEL_ID=cyankiwi/Qwen3.8-27B-AWQ-INT4   MODEL_DIR=qwen3.8-27b-awq-int4   MODEL_NAME=qwen3.8-27b
VLLM_QUANTIZATION=compressed-tensors  VLLM_TOOL_PARSER=qwen3_xml  VLLM_REASONING_PARSER=qwen3
LLM_THINKING_MODE=enable_thinking  LLM_REASONING_EFFORT=medium  KV_KIB_PER_TOKEN=32
SAMPLING_DEFAULT='{"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}'
SAMPLING_CODE='{"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}'
```

`qwen3.6-35b-a3b.env`: same as 3.8 with its id/dir/name, `KV_KIB_PER_TOKEN=10`,
and **no** `LLM_REASONING_EFFORT` (3.6 had no effort dial; the 07-27 runs sent
none). Record only.

`LLM_THINKING_MODE` is the one new concept: how a mechanical sub-task
(summarise, query expansion, equation OCR, memory extraction) turns reasoning
off for *this* model. `enable_thinking` sends `{"enable_thinking": false}`;
`effort_low` sends `{"reasoning_effort": "low"}` (gpt-oss cannot disable
reasoning); `none` sends nothing.

### 2.2 Production reads its model config too

`deploy.sh` gets `model activate <slug>`, which writes
`/opt/munin/config/active-model.env` (the production instance's model) and
stages the tokenizer. Both SBATCH scripts, `deploy_retrieval`, `deploy_vllm`
and the embedding-map unit source that file; every model literal in §1 rows
1-10 goes away. **Not a switch tool in this plan** (production does not
change model), but it is what removes the literals, and it is what a group
uses later.

**Acceptance test, before anything else runs:** `deploy.sh model activate
qwen3.8-27b` followed by `deploy.sh retrieval` must leave `docker compose
config` for `retrieval` byte-identical to the running container's environment
(sampling and thinking fields aside, which move from persona to env with the
same values), and the `vllm serve` line identical to today's. If the config
file cannot reproduce the hand-maintained state, it is not trusted to create a
second instance from it.

### 2.3 Secondary instances: `deploy.sh instance up|down|ls`

```
sudo ./deploy.sh instance up gpt-oss-20b --name eval --gpu batch --vllm-port 8001 --api-port 8082
sudo ./deploy.sh instance up gpt-oss-20b --name eval-shadow --vllm eval --api-port 8083 --corpus shadow
sudo ./deploy.sh instance ls
sudo ./deploy.sh instance down eval-shadow ; sudo ./deploy.sh instance down eval
```

`up` for a new vLLM (`--gpu batch`): validates the model env, checks the
checkpoint (downloads with `HF_EXCLUDE` on `--download`), stages the tokenizer
to `/opt/munin/data/models/tokenizer-<slug>/`, renders
`/opt/munin/docker/instances/<name>.yml` (an `extends` of the production
`retrieval` service overriding exactly: `container_name`, port, `VLLM_URL`,
`VLLM_MODEL_NAME`, `LLM_THINKING_MODE`, `LLM_REASONING_EFFORT`,
`SAMPLING_*`, tokenizer mount, and with `--corpus shadow` the two collection
names), submits `start-vllm-instance.sh` via `sbatch` (job name
`vllm-inst-<name>`, partition `standard`, `--gres=gpu:batch:1`,
`--cpus-per-task=6`, `--mem=16G`, `--time=48:00:00`, port and model from the
env; **not** matched by the production scheduler, writes none of the
production status files), waits for both healths, runs the gates (§2.5),
prints the instance table. `--vllm <name>` reuses another instance's vLLM
(the C2b absent arm: same model, different corpus, different container).
`down` stops and removes the container(s) and `scancel`s the job. `ls`
shows name, model, ports, job id, health. The generated `yml` files are
disposable; the C2b `docker-compose.shadow.yml` is retired in favour of
`--corpus shadow` and moves to `docs/archive/` with a stub, per the
archive-over-delete rule.

The gpt-oss instance is reachable only on `127.0.0.1:8082` / `8083`. The
gateway never sees it. `/api/models` on the production container reports the
production model only.

### 2.4 Sampling profiles

Sampling moves from `shared/personas/*.json` `params` into the model env as
`SAMPLING_DEFAULT` and `SAMPLING_CODE`. Personas keep a `sampling_class`
(`default`, or `code` for the code persona) and lose the five numeric keys.
`chat_service` merges: model profile for the persona's class, then any
numeric key a persona still declares wins (escape hatch, unused). The Qwen
profiles are today's values exactly, so production request bodies do not
change; a unit test pins that. Two instances then run off one persona
directory with different sampling, which is what "deploy simultaneously"
needs. The bench's bare/RAG arms keep `temperature 0.7` (historical, in every
committed run) and the scorecard says so.

### 2.5 Gates (the swap plan's step-1 list, mechanised, run by `instance up` and by the driver)

| gate | how | fail means |
|---|---|---|
| KV pool covers the profile | `GPU KV cache size` from the job `.out` ≥ `MAX_NUM_SEQS x MAX_MODEL_LEN` | wrong `MAX_NUM_SEQS` in the model env |
| MoE kernel | `Mxfp4 MoE backend` log line present (expect Marlin on SM120); stamped into provenance | model did not load |
| served name, window | `/v1/models` id and `max_model_len`; `check-context-window.sh` logic against the instance's API port | stale container or wrong env |
| tokenizer | container log `tokenizer loaded from`, sha256 of staged file == checkpoint's | the silent trap, now loud |
| compose diff | `docker compose config` of the instance vs production env: exactly the intended keys differ | leak from or into production |
| plain completion | 200 with content | serving broken |
| thinking-off applies | fixed summarise prompt, completion tokens with the model's `LLM_THINKING_MODE` fields vs without: **off < 0.5x on** | wrong mode |
| `reasoning_effort` | 200 with the kwarg (skipped when blank) | blank it |
| one tool-calling turn | `research` persona through the instance API: ≥ 1 parsed `tool_call`, 0 parse errors | wrong `VLLM_TOOL_PARSER` |
| decode / prefill | single-stream tok/s, 20k prefill; printed as the forecast input | |

### 2.6 Code changes (each small)

| file | change |
|---|---|
| `docker-compose.yml` | `VLLM_MODEL_NAME=${VLLM_MODEL_NAME:-qwen3.8-27b}`; pass `LLM_THINKING_MODE`, `LLM_REASONING_EFFORT`, `LLM_THINKING_TOGGLE`, `SAMPLING_DEFAULT`, `SAMPLING_CODE`; `vllm` container profile reads the same names |
| `database.py` | `thinking_off_fields()` switches on `LLM_THINKING_MODE`; `sampling_for(class)` reads `SAMPLING_*` |
| `chat_service.py` | persona sampling merge (§2.4) |
| `main.py` | `GET /api/models` (served name, window, `model_path`) |
| `deploy.sh` | `model activate`, `instance up/down/ls`, `stage_tokenizer` (renamed from `stage_qwen_tokenizer`), instance yml renderer, gates |
| `start-vllm-service*.sh` | source `active-model.env`; no model literals; banner from `MODEL_DESC`; `--quantization` only when set; `$VLLM_EXTRA_ARGS`. Two scripts stay (scheduler matches job names) |
| `scripts/vllm/start-vllm-instance.sh` | new: the secondary-instance SBATCH job, parameterised by env |
| `munin-embedding-map.service` | `EnvironmentFile=/opt/munin/config/active-model.env` |
| `frontend/gateway/main.py:809`, `Settings.tsx:566` | read `/api/models` (one VPS deploy) |
| `shared/personas/*.json` | drop numeric sampling keys, add `sampling_class` |
| `munin_bench/config.py` | `thinking_off_fields()` mirror, same env; the four raw literals (§1 row 15) use it |
| `.env.example`, `munin.env.template`, `backend/README.md`, `DECISIONS.md` | the checklist becomes "`deploy.sh model activate <slug>` / `instance up`; to add a model, add `config/models/<slug>.env`" |
| `docs/agent-track/TODO.md` | "production as just another instance" |

---

## 3. gpt-oss-20b, per-model facts (verified 2026-09-15)

| | |
|---|---|
| checkpoint | `openai/gpt-oss-20b`, **downloaded** to `/opt/munin/data/models/gpt-oss-20b`, 12.82 GiB in 3 shards, index total matches, `original/` and `metal/` excluded |
| shape | 24 layers, 12 full / 12 sliding (128); 64 heads, 8 KV heads, head_dim 64; 32 experts, 4 active; 131k ctx; 201k vocab, untied |
| quant | native MXFP4 on experts; attention, router, embeddings, `lm_head` BF16. No smaller vLLM-loadable quant exists; floor ~12 GiB |
| KV | **12 KiB/token fp8**; 0.75 GiB per 64k seq. At util 0.90 on the 5090: ~13 GiB pool, ~1.1M tokens, ~17x at 64k |
| kernel | oracle order on SM120 lands on **Marlin** (FlashInfer-TRTLLM is SM100 only, `triton_kernels` not installed). Accuracy-neutral, slower than native; fine at concurrency 1. Stamp the backend line |
| parsers | `openai` / `openai_gptoss`, both in the pinned build |
| reasoning | always on; `reasoning_effort` low/medium/high via `chat_template_kwargs`, the field the backend already sends. Main turns `medium`, sub-tasks `low` (`effort_low`). Sub-task cost shape changes (short trace instead of none): reported next to Track D cost, per sub-task |
| vision | none. Only the eval instance runs gpt-oss; production keeps vision |
| sampling | `temperature 1.0, top_p 1.0` (OpenAI), both classes |
| 24 GB claim | production profile fits any 24 GB card, Ada (14.3 / 18.7) or Ampere (fp16 KV, 15.8 / 18.7), since Marlin needs only SM80 |

---

## 4. Bench changes (autonomy)

| change | why |
|---|---|
| `run_arm` writes `ablation_runs/<tag>/<arm>.json`; `ablation/compare.py`, `toolreliability/score.py`, `risk_coverage.py` take the dir; the flat 08-26 files move to `ablation_runs/qwen38-27b/` in the same commit | one re-run would overwrite the Qwen3.8 per-query arrays the paired comparison reads (now also backed up off-machine) |
| agentic arm writes `agentic.capture.jsonl` incrementally and resumes | the 08-26 arm died at 191/199; `run_c1` already does this |
| `run_all` passes `args.base_url` into `litqa2-answer` (hardcoded :8080 at run_all.py:114) | on :8082 it would silently measure production |
| `run_all --provenance k=v`; header gains `model_path` from `/v1/models` `root` | the 09-14 C1 scorecard had fields backfilled by hand |
| per-arm `unparseable` split into `empty_content` / `deadline_hit` / `letter_not_found`; tool-call parse errors counted from `tool_events` | a parser problem must read as a parser problem on a non-Qwen model |
| `run_c2` takes both arm URLs (:8082 present, :8083 absent) | today it assumes :8080/:8081 |
| faithfulness judge on CPU via `MUNIN_BENCH_ENTAILMENT_DEVICE=cpu` | both GPUs full; ~1 to 2 h extra, no result change |
| `scripts/run_suite.sh <slug>` driver | §5 |

No change to question sets, prompts, the 900 s deadline, `MAX_OUTPUT_TOKENS`,
scoring, or thresholds.

---

## 5. The run: `run_suite.sh gpt-oss-20b`

Root steps go through a sudoers rule limited to `deploy.sh instance *`,
`deploy.sh model *`, `vllm-service *` and `munin-maintenance *`, installed by
`deploy.sh sudoers` and reviewed before install. Everything else runs as `vi`.
Phases write `runs/<tag>/phase-N.done`; re-invocation skips completed phases;
every gate prints measured vs threshold; a gate failure stops and reports,
never tunes. **No pause after the smoke.**

**Phase 0, preflight.** Production profile is `single` and a `vllm-service`
(not `-tp2`) job is running, else stop: the owner switches by hand. Persisted
profile file says `single` (so the 6 AM cron would not bring TP=2 back).
`vllm-service enable-24x7` so the 2 AM stop does not fire during the window;
the driver records that it did this. Checkpoint present. `MUNIN_EVAL_EGRESS=full`.
Disk ≥ 40 GB. Shadow collections: `papers_shadow` and `papers_chunks_shadow`
rebuilt from snapshot + frozen `removed_dois` (the `2913f9b` recipe) if
absent; leakage check = 0 on five sampled DOIs.

**Phase 1, instances up.** `instance up gpt-oss-20b --name eval ...` and
`--name eval-shadow --corpus shadow --vllm eval`. Gates §2.5 on both.
Decode/prefill forecast printed.

**Phase 2, smoke.** `run_all --tag gpt-oss-20b-smoke --tracks abstention,ablation
--limit 20 --base-url :8082`, `VLLM_URL=:8001 VLLM_MODEL_NAME=gpt-oss-20b`.
Gates against the 08-26 Qwen3.8 arm: agentic calls/query within 0.4x to 2.5x of
6.93; agentic `unparseable` ≤ 25% and `empty_content` ≤ 10%; tool-call parse
errors ≤ 2x the T11 baseline; bare `unparseable` == 0; 0 Brave 402/429 in the
eval container log. Prints cost/query and the revised forecast, then
**continues**.

**Phase 3, clean run**, concurrency 1 throughout, in this order so the paid
tracks are not adjacent:
1. `run_all --tag gpt-oss-20b --tracks ablation,abstention --limit 199
   --with-reliability --certify --date <D> --base-url :8082 --provenance ...`
   (Track D 3 arms n=199, C1 n=100).
2. `run_litqa2 --track answer` on :8082 (standalone, n=199, egress=full).
3. `run_c2 --arm present --base-url :8082` and `--arm absent --base-url :8083`
   (egress=off, n=50 each).
4. Faithfulness per arm over the Track D outputs, judge on CPU.
5. Derived: T11 `toolreliability.score ablation_runs/gpt-oss-20b/agentic.json`,
   risk-coverage over ablation + C1 + C2, routing anchor tier on :8082,
   `pipelines.compare` against `2026-08-26_harness-ablation.json` and
   `2026-09-14_answer-qwen38-900s.json`.
6. Post-run: grep the eval container log for 402/429 over the whole window;
   non-zero marks the scorecard `degraded: brave` and exits non-zero.
   Certification verdict printed, not gated (thresholds are Qwen3.6-derived;
   a FAIL is a finding).

**Phase 4, teardown.** `instance down eval-shadow`, `instance down eval`,
shadow collections dropped (snapshot kept), `vllm-service disable-24x7` if
phase 0 enabled it, then `vllm-service stop && vllm-service start tp2` and the
window check. Prints the files to commit: scorecards in both folders (rule),
T11, routing, risk-coverage, `runs/<tag>/driver.log`.

Human steps after: RESULTS.md dated section, PAPER.md claim 1 third point and
provenance, paper-kit 04/05/06/08/10, README rows, correct the Granite row in
`THIRD-MODEL-REVIEW.md` §4 (it is dense attention, 80 KiB/token, not hybrid).

### Time and cost

Anchors: Qwen3.8 agentic 157 s/q (8.7 h), bare/RAG 8 s/q, standalone answer
6.1 h shared-GPU, C1 ~3 h, C2b ~3 h, routing ~1 h; Qwen3.6-A3B agentic 79 s/q.
gpt-oss reads ~4 GiB per decode step against ~19 for the 27B, so expect the
A3B regime even through Marlin, unless it over-tools.

| item | estimate |
|---|---|
| phases 0-2 | ~1.5 h, < $1 |
| Track D | 4 to 6 h |
| standalone answer | 3 to 4 h |
| C1 + C2b | 3 to 4 h |
| faithfulness (CPU), routing, derived | 2 to 3 h |
| teardown + TP=2 restore | 15 min |
| **total** | **~14 to 19 h**, own GPU, so the cost columns are clean |
| Brave | $6 to $16 at Qwen3.8 tool rates; the smoke replaces this with a number |

---

## 6. Risks, highest first

1. **Marlin MXFP4 on SM120 fails to load.** Prior good; if it fails, install
   `triton_kernels` into a *copy* of the venv, never production's. First
   thing `instance up` finds out.
2. **Harmony tool calls through 43 schemas.** One parser; the tool-call gate
   and the smoke's parse-error count are the defence. Parse failures are
   reported separately from accuracy either way.
3. **The acceptance test is not a no-op.** Any diff from `model activate
   qwen3.8-27b` against running production is fixed before an instance is
   created from the same machinery.
4. **Sampling migration changes production bodies.** Pinned by a unit test
   that the Qwen profile reproduces the persona values exactly.
5. **Cron.** `enable-24x7` for the window; the persisted `single` profile
   protects the 6 AM start if anything restarts. Both restored in phase 4.
6. **CPU.** Production single holds 12 of 20 cores, the instance asks 6, the
   containers share the rest. The judge on CPU competes; it runs last.
7. **`effort_low` cost shape.** Reported, per sub-task, next to the cost
   column.
8. **Certification thresholds** are Qwen3.6-derived. Printed, not gated.

---

## 7. Deferred (TODO.md)

- Production as just another instance (`instance up qwen3.8-27b --name prod
  --gpu vllm`), retiring the two hand-maintained SBATCH scripts.
- Qwen3.5-9B hardware-floor run and its config file; the 16 GB emulation.
- User-visible model picker over multiple instances; `/api/models` listing all.
- Bench bare/RAG arms on the model sampling profile (breaks comparability
  with every committed run; needs its own decision).
