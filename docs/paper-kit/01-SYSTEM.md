# 01 System

Everything a reader needs to reconstruct the deployed system: hardware,
models, serving configuration, services, and the two-target deployment split.

---

## 1. Design constraint that shapes everything

Munin is built for a **scientific group that must keep its data on its own
infrastructure**. That single constraint explains most of the non-obvious
choices below, and it is worth stating early in the paper because otherwise
several decisions look like under-engineering:

- The generation model is a quantized open-weights model on a single consumer
  GPU, not a frontier API.
- The faithfulness judge is a sub-1B local model, not GPT-4.
- Web search is an explicit, controllable egress boundary rather than an
  always-on capability.
- The code sandbox has its network namespace disabled, which is part of the
  sovereignty claim rather than mere hygiene.

The trade-off is deliberate and should be named as such: every one of these is
weaker in isolation than the hosted alternative, and the paper's contribution
is that the **harness** recovers the gap.

---

## 2. Hardware

### Cluster (`hugin`, the SLURM head node)

| Component | Spec |
|---|---|
| GPUs | 2x NVIDIA RTX 5090, 32 GB VRAM each (Blackwell, SM 120a) |
| CUDA | 13.0.2, loaded via environment modules |
| Scheduler | SLURM, no `slurmdbd` (so `sacct` job accounting is **unavailable**) |
| Partitions | `vllm-serving`, `llm-batch`, `standard`, `quickdirty`, `gputraining` |

GPUs are exposed to SLURM two ways via `gres.conf`:

| Resource | What it grants | Consumer |
|---|---|---|
| `gpu:batch:1` | Whole GPU 0 | legacy MiroThinker deep-research job (now disabled) |
| `gpu:vllm:1` | Whole GPU 1 | the vLLM service |
| `shard:N` | N/8 of any free GPU (~4 GB per shard) | available, unused |
| `shard:batch:N` | N/8 of GPU 0 | unused |
| `shard:vllm:N` | N/8 of GPU 1, only when vLLM is down | unused |

SLURM refuses to mix whole-GPU and shard allocations on the same physical GPU,
so while vLLM holds GPU 1 the eight `shard:vllm` slots are blocked and shard
jobs land on GPU 0 instead. This is a real constraint on running an eval
concurrently with production traffic.

### VPS

Hetzner CAX21: Ubuntu 24.04, 4 vCPU ARM, 8 GB RAM, plus a 50 GB volume mounted
at `/mnt/uploads`.

**Note for the sovereignty claim:** the VPS is rented, not on-premise. The
corpus and all inference stay on the group's cluster, but chat messages and
outputs transit a third-party host. The paper must scope the claim precisely
rather than saying "no data leaves the premises" unqualified. This is flagged
as an open item in the agent design document (§4.4) and has not yet been
resolved by moving the VPS.

---

## 3. Models

| Role | Model | Details |
|---|---|---|
| Generation | `cyankiwi/Qwen3.8-27B-AWQ-INT4` | Dense 27B, hybrid Gated DeltaNet + Gated Attention, natively multimodal, pack-quantized group-32, ~20 GB on disk. Served as `qwen3.8-27b` since 2026-08-25. Reasoning enabled (emits `<think>` traces) at `reasoning_effort=medium`, pinned by the backend because the model's own default is `xhigh`. |
| Generation (retired 2026-08-25) | `cyankiwi/Qwen3.6-35B-A3B-AWQ-4bit` | Gated DeltaNet + MoE hybrid, 35B total / 3B active, AWQ 4-bit, ~19 GB. Served as `qwen3.6-35b-a3b`. The backbone for every result dated before 2026-08-26; every headline has since been re-measured on Qwen3.8, and on 2026-09-17 this checkpoint was brought back as an eval-only instance and re-run through the full suite on the current protocol, so the Qwen3.6 figure beside each headline is a same-protocol one. |
| Paper embedding | BGE-large-en-v1.5 | 1024d, Qdrant collection `papers_bge`, staged at `/opt/munin/data/models/bge-large`. Production since 2026-07-06. |
| Paper embedding (rollback only) | SPECTER-v1 (`allenai-specter`) | 768d, collection `papers`. Retained for rollback; retiring it is a tracked one-way task. |
| User-document embedding | BGE-base | Separate collection from the paper corpus. |
| Faithfulness judge | MiniCheck-Flan-T5-Large | Under 1B parameters. Runs locally on GPU 0. Never used in production, only in Track B scoring. |
| Legacy deep research | MiroThinker-v1.5-30B | ~17 GB, disabled 2026-07, weights retained on disk. |

### Why BGE needs a query prefix

`PAPER_QUERY_PREFIX` prepends `"Represent this sentence for searching relevant
passages: "` to **queries only**; documents are embedded raw. The prefix is
empty under SPECTER. Dropping it materially shrinks the recall gain, so it is a
correctness-critical configuration detail rather than a nicety.

### Encoder/collection pairing is enforced at three points

Because a half-flip between the two encoders silently serves empty or wrong
results, the pairing is asserted:

1. `database.verify_paper_space()` compares encoder width against the
   collection's actual vector size at startup and **raises**, so a mismatch
   fails the boot rather than serving degraded results.
2. `deploy.sh verify` re-asserts the pairing against `/api/status` and fails
   the deploy on drift.
3. The code defaults are production values (`bge-large` / `papers_bge`), so a
   bare local run, a fresh container, or an eval matches the live stack without
   needing the cluster secrets file.

Point 3 was inverted until 2026-08, and the consequence is worth reporting: an
unconfigured benchmark run **silently measured the retired SPECTER corpus and
looked like it worked**. The general rule this encodes is that when a migration
completes, the defaults move; they do not stay pointing at the rollback.

---

## 4. vLLM serving configuration

Two profiles exist and the choice is an either/or with cluster availability.
**Production runs the TP=2 profile** since the 2026-08-25 model swap. The full
serve invocation, since these flags are load-bearing for the cost and
concurrency results:

```bash
vllm serve /opt/munin/data/models/qwen3.8-27b-awq-int4 \
    --host 0.0.0.0 --port 8000 \
    --tensor-parallel-size 2 \
    --gpu-memory-utilization 0.85 \
    --max-model-len 65536 \
    --max-num-seqs 8 \
    --quantization compressed-tensors \
    --kv-cache-dtype fp8 \
    --served-model-name qwen3.8-27b \
    --enable-auto-tool-choice \
    --tool-call-parser qwen3_xml \
    --reasoning-parser qwen3
```

The single-GPU profile is the same invocation without tensor parallelism at
`--gpu-memory-utilization 0.90 --max-num-seqs 2`; it frees the second card
for batch jobs at the cost of concurrency. The window is 65,536 in both,
deliberately: the TP=2 profile was retargeted at concurrency rather than at
a larger window so that "new model" and "new window" could not be entangled
in the measurements. A dense 27B's KV cache is ~3.2x dearer per token than the
retired MoE's, which is why the flags moved with the model.

Runs as a SLURM job (TP=2: `--gres=gpu:vllm:1,gpu:batch:1`,
`--cpus-per-task=12`, `--mem=32G`; single-GPU: `--gres=gpu:vllm:1`,
`--cpus-per-task=4`, `--mem=16G`; both `--time=20:00:00`), scheduled by cron
6am to 2am, or 24/7 via
`vllm-service enable-24x7`. The backend's context window is pinned to match
(`VLLM_MAX_MODEL_LEN=65536`, `VLLM_MAX_CONTEXT=60000`), exported before the
retrieval container is recreated so the two cannot drift.

**`--max-num-seqs` is the single most important operational number in the
kit.** Benchmark concurrency must not exceed the running profile's value (8
on TP=2, 2 on the single-GPU profile). A run above it silently degrades rather
than erroring, which is exactly what produced a misleading 0.688 agentic
figure before it was caught. Every cost-bearing arm in this kit ran at
concurrency 1 regardless of profile. See `10-REPRODUCE.md`.

TP=2 cannot leave shards free for other jobs because SLURM's
`ConstrainDevices` binds both cards, so it is a deliberate either/or with
cluster availability rather than a free upgrade. The earlier TP=2 experiment
at a 131072 window (`TP2-GPU-SHARING-EXPERIMENT.md` in the repository) is
superseded by the 64k production profile above.

---

## 5. Cluster services

| Service | Port | Purpose |
|---|---|---|
| Retrieval API | 8080 | FastAPI. RAG, chat orchestration, agents, MCP server, deep research. The only externally reachable piece. |
| Qdrant | 6333 | Vector DB. Collections: `papers_bge` (production), `papers` (SPECTER rollback), `user_docs`, plus isolated `eval_*` collections for benchmarks. |
| Neo4j | 7474 / 7687 | Citation graph. |
| GROBID | 8070 | PDF parsing to TEI. Configured with a Crossref polite-pool mailto. |
| SearXNG | 8888 | Web search (supplement tier). |
| vLLM | 8000 | LLM inference. |
| Sandbox | internal | Jupyter-kernel sidecar for the `run_python` tool. Network namespace disabled. |

Everything except the retrieval API binds to `127.0.0.1`. The retrieval API is
exposed to the VPS over an autossh reverse tunnel on VPS port 18080. **There is
no inbound path to the cluster.**

Supporting systemd units:

| Unit | Cadence | Purpose |
|---|---|---|
| `munin-paper-pipeline.service` | always-on | Watches the PDF drop directory every 60s and ingests new papers. |
| `munin-paper-detect.service` | always-on | Every 15 min, a paced detection sweep over the corpus with auto-quarantine. |
| `munin-paper-reattribute.timer` | 04:30 daily | Backfills group attribution for records whose uploader joined the allowlist after ingest. |
| `munin-embedding-map.timer` | 01:30 / 03:00 daily | Rebuilds the 2D paper-embedding map and topical clusters. |
| `munin-tunnel.service` | always-on | autossh reverse tunnel to the VPS. |

---

## 6. VPS services

| Service | Port | Purpose |
|---|---|---|
| Caddy | 80, 443 | Reverse proxy, automatic TLS, forward-auth. |
| munin-auth | 8090 | Email-OTP authentication, signed session cookies. FastAPI + SQLite, no Authentik / PostgreSQL / Redis. |
| api-gateway | 8070 | API-key validation, rate limiting, usage logging, proxy to the tunnel. |
| tusd | 11080 | Resumable file uploads. |
| hook-service | 18088 | Post-upload file organization. |

Subdomains, each behind session-cookie auth except the landing page:
landing (public), `auth.`, `chat.`, `docs.`, `search.`, `research.` (now serves
an unavailable notice), `upload.`, and `api.` (API-key bearer auth,
OpenAI-compatible surface).

### Authentication flow

1. User hits a protected subdomain. Caddy forward-auth sees no session, returns 401.
2. Redirect to the auth subdomain login page.
3. Email is checked against the whitelist (CSV on first boot, then the auth DB).
4. Six-digit OTP by SMTP.
5. OTP verified, signed session cookie set (30 days, domain-scoped).
6. Redirect to the original destination.

API-key requests bypass forward-auth entirely and go to the gateway, which
validates the bearer token, applies per-key quotas from `config/quotas.yml`,
and logs usage.

The cluster receives identity as `X-Munin-Email` and `X-Munin-Name` headers set
by Caddy's forward-auth. The cluster never sees credentials.

---

## 7. Chat UI

React 19 + TypeScript + Tailwind 4, built with Vite, state via Zustand,
markdown via react-markdown with remark-gfm / remark-math / rehype-katex,
syntax highlighting, Recharts for plots, deck.gl for the paper-embedding map.
Tests: Vitest + Testing Library + MSW (219 unit assertions), Playwright for
end-to-end.

Notable user-facing behaviours that are engineering contributions in their own
right and are worth a sentence each in the paper:

- **SSE streaming with resumable turns.** The HTTP listener is decoupled from
  the work: a per-request `Stream` object holds a monotonic event log, and the
  POST response is merely a listener on it. A `GET .../resume` with
  `Last-Event-ID` replays unseen entries and continues live, so a WiFi blip or
  a full browser refresh does not lose an in-flight turn.
- **Background turns.** After a 60s reconnect grace, a listenerless turn is
  promoted to background rather than cancelled (bounded: 2 per user, 30 min
  wall-clock), so the answer completes and persists even if the tab is closed.
  Stop became an explicit endpoint as a consequence.
- **Save-always persistence.** The streaming coroutine wraps its body in
  `try/finally` so the assistant turn is persisted regardless of exit path,
  including `GeneratorExit` on client disconnect. `GeneratorExit` derives from
  `BaseException`, so `except Exception` never caught it: this presented as
  200 OK with no traceback and missing rows.
- **Task execution log**, PWA install, maintenance and sleeping pages driven
  by a single cluster-side flag file surfaced through `/api/status`.

---

## 8. Operational envelope

| Property | Value |
|---|---|
| Availability | vLLM scheduled 6am to 2am (nightly GPU release), or 24/7 on demand |
| Concurrency ceiling | 8 sequences on the TP=2 production profile, 2 on the single-GPU profile (vLLM `--max-num-seqs`) |
| Context window | 65,536 tokens served, 60,000 budgeted by the backend |
| Users | One scientific group, production since 2026-04 |
| Corpus | 68,462 papers at the 2026-07 headline runs, 68,863 entries at 2026-08-28 (nearest recorded count to the 2026-08-26 re-measurement); 3,922 contributor uploads / 63,249 crawler downloads at the 2026-05 audit |
| Repository | 511 commits since 2026-04-13; 739 backend test functions, 219 webui assertions |
| Deploy | Two independent targets: `backend/deploy.sh` (root + systemd on the cluster), `frontend/docker-compose.yml` (VPS). No top-level deploy script, deliberately. |

### Token accounting

SLURM accounting is **not** available (`slurmdbd` is not deployed), so
GPU-seconds cannot be read from `sacct`. Cost is therefore measured as:

- prompt and completion tokens from the vLLM `usage` field, aggregated
  per-purpose through a `ContextVar` (`usage_tracker.py`), and
- wall-clock inference time measured directly at **concurrency = 1**, not
  derived from a blended throughput.

This is why every cost-bearing ablation arm runs at concurrency 1. It is a
methodological constraint imposed by the deployment, and it should be stated in
the paper rather than left implicit.
