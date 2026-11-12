# Cluster reconnect runbook (2026-11)

Status: written 2026-11-12 from the reports of six work lanes (A merge tool, B
cluster catch-up, C vLLM + Sage-A1.5, D1 Strata, D2 Basalt, E Haiku fallback),
with spot checks re-run by the integrator. Nothing in this runbook has been
executed against production. Every command here is for the operator.

Conventions:

- `[sudo]` marks a command that needs root on the cluster host. `[VPS]` marks a
  command run on the VPS as the operator account (docker group, no sudo
  expected; not verified by this work, which was read-only on the VPS).
- Est. = estimate, not measured. Measured numbers say so.
- "Cluster downtime" is invisible to users until step 6, because users are on
  Lite until then. "User downtime" only exists in step 6.

## Local values

Set these once per shell. The committed copy of this runbook carries
placeholders.

```bash
REPO=<path to the munin checkout on main>          # deploy source for munin
R=$REPO/backend
HS=<path to a HuginSLURM checkout or worktree on main>
STAGING=<operator home>/munin-staging              # staged venvs, weights, packs
BAKEOFF=<scratch dir>/bakeoff                      # PROTOCOL.md, bench.py, sample.sh, *.md
VPS=<vps-user>@<vps-host>
KEY=<ssh-key>
DOMAIN=<public domain>
D=2026-11-XX                                       # date stamp for backups
```

## GPU mapping (correction to the lane brief)

`/etc/slurm/gres.conf` maps `gpu:batch` to `/dev/nvidia0` (PCI 01:00.0,
nvidia-smi index 0) and `gpu:vllm` to `/dev/nvidia1` (PCI 03:00.0). Verified
by probe jobs in lanes C, D1 and D2 and by `nvidia-smi` at 15:20 on
2026-11-12 (index 0 at 2325 MiB, index 1 at 4 MiB). The batch card carries
about 2.3 GiB outside SLURM: the paper pipeline (root, ~1.8 GiB) and the
remote desktop (~0.5 GiB). Below, "GPU0 vLLM" means gres `gpu:vllm` (the
clean card) and "GPU1 engine" means gres `gpu:batch` (the shared card).

## Open decisions (recommended default in bold)

| # | Decision | Default | Why |
|---|---|---|---|
| D1 | Promote Sage-A1.5 as the production backbone now, or only after a full suite run | **Gate on `scripts/run_suite.sh sage-a1.5` first; keep Qwen3.8 meanwhile** | n=30 shows 22 vs 20 correct, p=0.42: parity, not a clear gain. A backbone switch changes the paper's numbers. |
| D2 | If Sage is held back: Qwen3.8 on the old vLLM 0.20.2 venv or on the new 0.33.0 | **Old venv** (the profile has no `VLLM_VENV`, so this is the no-change path) | Qwen3.8 on 0.33.0 was never measured. Moving it needs `VLLM_VENV` in its profile plus a probe + 30-question run (est. 30 GPU-min). |
| D3 | vLLM serving mode | **single on `gpu:vllm`** | Frees the batch card for the GPU1 engine. TP=2 blocks step 4 entirely. Qwen3.8 single measured at 80 tok/s decode, 206,802-token KV pool. |
| D4 | Sage tool parser | **qwen3_coder** (model card, profile default) | Both qwen3_coder and qwen3_xml scored 13/14 on the probe. |
| D5 | Sage concurrency at 64k | **max-num-seqs 3** (committed: 2) | KV pool fits 3.98 requests at 64k; peak use in the eval was 18.5%. 4 leaves no margin. |
| D6 | Run the downloaded Strata / Basalt code for the bake-off | **Yes, approve both, then run PROTOCOL.md** | Both lanes were blocked by the permission classifier; no engine number exists. |
| D7 | GPU1 engine pick | **None until measured; provisional lean Strata** (see step 4) | Operational reasons only. |
| D8 | Qwen Community License 1.0 for Qwen3.8-Flash-Next | **Confirm no commercial munin use before production** | Gateway API keys fit its "Model as a Service" wording. |
| D9 | Merge the four v1.1 branches before the retrieval rebuild | **Yes** | One rebuild instead of two. |
| D10 | Rebuild the sandbox | **Yes, after retrieval** (2.3 min build measured) | No behaviour change, keeps deployed equal to main. |
| D11 | Set `MUNIN_GATEWAY_TOKEN` on both sides during cutover | **No, separate change after the cluster is stable** | Fewer moving parts in the cutover. |
| D12 | `OPENALEX_API_KEY` in cluster.env | **Skip** (optional) | |
| D13 | Remove the retired MiroThinker leftovers (17 GB) | **Yes, after step 6 is stable** | |
| D14 | Run the read-only VPS fetch + dry runs as a rehearsal before the cutover day | **Yes** | The real 840 Lite points and 28 Lite files were never dry-run against the cluster. |
| D15 | Layer 1 (vLLM to Claude) active during deliberate vLLM-off windows (02:00-06:00, experiments) | **On, with `CLAUDE_FALLBACK_MODEL=` empty and 20M tokens/day** | Users keep answers; Sonnet retries cost about 20x Haiku. |
| D16 | Gateway `BACKEND_FAILBACK` | **manual** for the first episodes | The merge then runs before users see cluster history again. Lane E's default is auto. |
| D17 | Periodic cluster-to-Lite history sync | **No** (merge back after each episode) | Lane E's recommendation. |
| D18 | Bring Lite `agent_traces` across | **No** | Diagnostics only. |
| D19 | Engine split: vLLM venv installer in HuginSLURM, profiles and launch scripts in munin | **Accept** | munin already owns the vLLM scripts and `model activate`. |

## Lane status at the time of writing

| Lane | Branch | State |
|---|---|---|
| A merge tool | munin `feat/lite-merge` (4daff6a) | Done, rehearsed on DB copies; real VPS fetch never run (classifier). Integrator re-ran tests: 12 passed, 1 skipped. |
| B catch-up | munin `chore/cluster-catchup` (4 commits) + `v1.1-*-rebased` (4 branches) | Done, nothing deployed. Cluster runs main@bc1ee62 (2026-10-16); 7 of 119 newer commits touch backend/shared. |
| C vLLM + Sage | HuginSLURM `feat/vllm-sage-a15` (60528ad, 5199a5c), munin `feat/sage-a15-profile` (55f6206) | Done, measured. Integrator recounted the eval files: Sage 22 correct / 8 abstain, mean 49.8 s, 362 tool calls; Qwen3.8 20 / 10, 95.7 s, 153 calls. |
| D1 Strata | HuginSLURM `feat/gpu1-strata` (a6a4b67) | Partial: staged and verified, never executed. |
| D2 Basalt | HuginSLURM `feat/gpu1-basalt` (6c95008) | Partial: pack downloaded and sha256-verified (fetch log says MODEL_OK, 115,307,561,176 bytes), engine never built. |
| E Haiku fallback | munin `feat/haiku-fallback` (3 commits) | Done in tests, nothing deployed. Integrator re-ran: llm-failover 32 passed, gateway 29 passed. |

State on the cluster host at 15:20 on 2026-11-12 (read-only checks): SLURM
queue empty, both GPUs free of SLURM jobs, `/opt/munin/logs/vllm-health.hold`
present (root, 14:11), the 06:00 `schedule-vllm.sh start` line in
`/etc/cron.d/hugin-cluster` commented out with `# HOLD-2026-11-12`, the 02:00
`stop` line still active, `munin-tunnel` active but unable to bind remote port
18080 because Lite holds it. Test images `munin-retrieval:laneb-catchup-fcc4dd1`
and `munin-sandbox:laneb-catchup-fcc4dd1` exist and warmed the BuildKit cache.

---

## 0. Pre-flight backups

Cluster downtime: none. User downtime: none. Est. 10 min.

```bash
# Cluster: images, deployed trees, config, chats.db (backup API), user docs, Qdrant user_docs
sudo docker tag munin-retrieval:latest munin-retrieval:pre-reconnect-$D        # [sudo]
sudo docker tag munin-sandbox:latest   munin-sandbox:pre-reconnect-$D          # [sudo]
sudo tar -C /opt/munin -czf /opt/munin/pre-reconnect-$D.tgz \
     docker/docker-compose.yml services/retrieval services/sandbox             # [sudo]
sudo cp -a /opt/hugin/config/cluster.env /opt/hugin/config/cluster.env.bak-$D  # [sudo]
sudo python3 -c "import sqlite3; s=sqlite3.connect('/opt/munin/data/chats.db'); d=sqlite3.connect('/opt/munin/data/chats.db.pre-reconnect-$D'); s.backup(d)"  # [sudo]
sudo tar -C /opt/munin/data -cf /opt/munin/data/user_docs.pre-reconnect-$D.tar user_docs   # [sudo] 684 MB measured
docker exec munin-retrieval python -c "import urllib.request as u; print(u.urlopen(u.Request('http://qdrant:6333/collections/user_docs/snapshots', method='POST')).read().decode())"
#   (Qdrant snapshot of the collection the merge writes to; adjust the host name if
#    retrieval reaches Qdrant under another name: docker exec munin-retrieval env | grep -i qdrant)

# Do not prune the docker builder cache from here until step 2 is done:
# a cold retrieval build took 27.5 min (measured), the warm one est. 1-3 min.
```

```bash
# VPS: Lite snapshot (backup API inside the running Lite container) and frontend env
ssh -i $KEY $VPS 'mkdir -p ~/munin-lite/backups && docker exec munin-retrieval python -c "import sqlite3; s=sqlite3.connect(\"/data/chats.db\"); d=sqlite3.connect(\"/data/chats.db.pre-reconnect\"); s.backup(d)" && cp -a ~/munin-lite/.runtime/data/chats.db.pre-reconnect ~/munin-lite/backups/chats.db.pre-reconnect-'$D' && cp -a ~/munin/frontend/.env ~/munin-lite/backups/frontend.env.pre-reconnect-'$D' && cp -a ~/munin-lite/.env ~/munin-lite/backups/lite.env.pre-reconnect-'$D' && chmod 600 ~/munin-lite/backups/*'    # [VPS]
```

One ssh login per command batch. fail2ban: use the known user and key only.

## 1. Merge order

All branches are local, none pushed. Commit messages stay single-line.

### munin

Rehearsed by the integrator in a throwaway detached worktree from `main`
(fcc4dd1), in this order:

| # | Branch | Result |
|---|---|---|
| 1 | `chore/cluster-catchup` | clean |
| 2 | `v1.1-ci-suite-rebased` | clean (its a2f1c40 duplicates d668040; merges as a no-op) |
| 3 | `v1.1-search-replace-rebased` | clean |
| 4 | `v1.1-retire-tools-rebased` | clean; **human review** of 126ad9c and the TaskLog.tsx resolution (drops AgentBlock, AgentResultRow, STOP_REASON_LABELS); persona JSON changes, so research scorecards need a rerun before they are cited |
| 5 | `v1.1-alerts-rebased` | clean |
| 6 | `feat/lite-merge` | clean |
| 7 | `feat/sage-a15-profile` | clean |
| 8 | `feat/haiku-fallback` | **2 conflicts**, both unions (below) |
| 9 | follow-up commit | **required**: classify two test files in `backend/retrieval/tests/suite.txt` |

Conflicts in 8:

- `backend/deploy.sh`, both "Modes:" usage lines (around lines 77 and 2056):
  ci-suite added `nightly-tests`, haiku-fallback added `fallback`. Keep both:
  `... knowledge nightly-tests pipeline retrieval sandbox searxng monitoring fallback verify`.
- `.github/workflows/ci.yml`: alerts added a "monitoring scripts" step, haiku
  added "llm-failover proxy tests" and "api gateway tests" in the same place.
  Keep all three steps.

Semantic conflict in 9: `run_suite.py --check` refuses after the merge because
`test_llm_fallback_status.py` (haiku) and `test_search_replace_edits.py`
(search-replace) were written on branches without `suite.txt`. Both are
hermetic (AST-only / local stores). Append:

```
test_llm_fallback_status.py              hermetic
test_search_replace_edits.py             hermetic
```

Commit: `test(retrieval): classify the fallback status and search/replace edit tests as hermetic`.

Integrator's checks on the merged tree (all with the two resolutions and the
suite.txt lines): `bash -n deploy.sh` ok, ci.yml parses, `deploy.sh --dry-run`
for `all`, `fallback`, `nightly-tests`, `vllm` all rc=0 as a non-root user,
`check_compose.py` 27/27, `check_commit_refs.py` 69/69, hermetic retrieval
tier **99/99 files pass** (47 s, in `munin-retrieval:laneb-catchup-fcc4dd1`,
`--network none`), llm-failover 32, gateway 29, merge_state 12+1 skipped,
frontend/monitoring 13, vLLM health shell test PASS, webui `tsc -b` clean,
vitest 1059/1060. The one vitest failure
(`ArtifactPanel > protein artifacts > reports a structure it cannot fetch`)
fails identically on plain `main` in the same environment, so it is not caused
by the merges; lanes B and E saw it pass in theirs. Check it in CI.

Not run on the merged tree: retrieval live tier, Playwright e2e, single-host
smoke.

### HuginSLURM

Branches `feat/vllm-sage-a15`, `feat/gpu1-strata`, `feat/gpu1-basalt` all
start at `main` (fb28a52). Every pair conflicts in `deploy.sh` only, and every
hunk is an adjacent addition: the usage header comment, the new
`deploy_<mode>()` function and the `case` dispatch. Resolve by keeping all
sides, with one `;;` per case arm:

```bash
    vllm-venv) ... ;;              # from feat/vllm-sage-a15
    strata)  shift; deploy_strata "$@" ;;
    basalt)  deploy_basalt "${2:-}" ;;
```

Merge order: `feat/vllm-sage-a15` now (step 3 needs it). Merge only the GPU1
engine branch you pick (step 4); the other can stay unmerged. The main
HuginSLURM checkout has an uncommitted change in `setup/09-gromacs-setup.sh`
and is one commit ahead of origin: do the merges in a separate worktree
(`git worktree add <dir> main`), run `bash -n deploy.sh`, and deploy from that
worktree, or fast-forward main only after the uncommitted change is resolved.

## 2. Cluster catch-up deploy (lane B)

Source: `docs/CLUSTER-CATCHUP-RUNBOOK.md` (on `chore/cluster-catchup`). Run
from `$REPO` on the merged `main`. Cluster downtime: about 1-2 min at the
retrieval recreate (lane B est.), none for the rest. User downtime: none.
Total est. 20-30 min with a warm build cache.

```bash
# 2.1 cluster.env (read-only check, then edits)
sudo grep -cE '^(MUNIN_DOMAIN|MUNIN_CLUSTER_NAME|NEO4J_PASSWORD|SEARXNG_SECRET|MUNIN_VPS_HOST)=.+' /opt/hugin/config/cluster.env   # [sudo] expect 5
sudo grep -nE '^(DEEPRESEARCH_ENABLED|OPENALEX_API_KEY|MUNIN_GATEWAY_TOKEN|ADMIN_INGEST_TOKEN)=' /opt/hugin/config/cluster.env | cut -d= -f1   # [sudo]
sudo sed -i '/^DEEPRESEARCH_ENABLED=/d' /opt/hugin/config/cluster.env          # [sudo] only if present
# ADMIN_INGEST_TOKEN must be set here (step 6.9 relies on it).

# 2.2 install-only modes
sudo $R/deploy.sh --dry-run all                                               # [sudo] read the plan
sudo $R/deploy.sh compose                                                     # [sudo]
sudo $R/deploy.sh personas                                                    # [sudo]
sudo $R/deploy.sh agents                                                      # [sudo] (agents.yml no longer read after retire-tools)
sudo $R/deploy.sh vllm                                                        # [sudo] scripts + profiles incl. sage-a1.5.env; re-enables munin-vllm-health.timer; the hold file keeps it inert
sudo $R/deploy.sh maintenance                                                 # [sudo]

# 2.3 retrieval (rebuild + recreate), then sandbox
sudo $R/deploy.sh retrieval                                                   # [sudo] est. 1-3 min build warm, 27.5 min cold (measured); 1-2 min cluster downtime
sudo $R/deploy.sh sandbox                                                     # [sudo] D10; 10-30 s; ends with up -d retrieval (harmless now)
sudo $R/deploy.sh nightly-tests                                               # [sudo] optional, from ci-suite (02:30 timer)

# 2.4 verify
sudo $R/deploy.sh verify                                                      # [sudo]
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | head -30
docker exec munin-retrieval python -c "import sys; sys.path.insert(0,'/app'); from mcp.schemas import MCP_TOOLS; assert 'show_protein' in MCP_TOOLS; print('ok')"
for f in $R/retrieval $R/sandbox; do sudo diff -rq --exclude=__pycache__ $f /opt/munin/services/$(basename $f); done   # [sudo] expect no output
```

`verify` must print `paper space - bge-large (1024d) / papers_bge (1024d)`.
Until step 3 brings vLLM up, chat through the cluster has no LLM (or goes to
Claude once step 5 is in). That is expected; users are still on Lite.

Rollback: `sudo docker tag munin-retrieval:pre-reconnect-$D munin-retrieval:latest`
(same for sandbox), `sudo tar -C /opt/munin -xzf /opt/munin/pre-reconnect-$D.tgz`,
`cd /opt/munin/docker && sudo docker compose --profile rag up -d --no-build sandbox retrieval`
(this is the live project directory, never the repo), restore cluster.env from
the `.bak`. `[sudo]` throughout.

## 3. GPU0 vLLM 0.33.0 + Sage-A1.5 (lane C)

Cluster downtime: vLLM restart est. 1-2 min (Sage measured 64 s to ready),
retrieval recreate 1-2 min. User downtime: none (pre-cutover).

### 3.1 Install the engine and weights (always, both decision outcomes)

```bash
# HuginSLURM worktree with feat/vllm-sage-a15 merged
sudo UV_CACHE_DIR=$HOME/.cache/uv $HS/deploy.sh vllm-venv 0.33.0             # [sudo] est. 5-10 min from the warm uv cache (first cold download took ~25 min, measured)
sudo chown -R $(id -un): $HOME/.cache/uv                                      # [sudo]
sudo install -d -m 0755 /opt/munin/data/models/sage-a1.5-awq-int4             # [sudo]
sudo cp -a $STAGING/models/sage-a1.5-awq-int4/. /opt/munin/data/models/sage-a1.5-awq-int4/   # [sudo] 26.5 GiB, est. 2-5 min
sudo chown -R root:root /opt/munin/data/models/sage-a1.5-awq-int4             # [sudo]
```

The installer re-installs from the frozen 202-package constraints (a venv
cannot be moved), runs `vllm download-kernels` (without precompiled FlashInfer
kernels the first start OOM-compiles the MoE kernel, measured) and checks
version, sm_120 kernels, parsers and drift.

### 3.2 Decision gate

Measured on one RTX 5090 (`gpu:vllm`), all on the 2026-10-05 retrieval image:

| Metric | Sage-A1.5 AWQ-INT4 on vLLM 0.33.0 | Qwen3.8-27B AWQ on vLLM 0.20.2 (single GPU) |
|---|---|---|
| Weights loaded | 24.07 GiB | 18.91 GiB |
| KV pool at 64k | 260,545 tokens (3.98 full-context requests) | 206,802 tokens |
| Startup to ready | 64 s (restart ~50 s) | 181 s |
| Decode, 1 stream | 206.3 tok/s | 80.1 tok/s |
| Prefill 8k / 32k | 22,100 / 16,300 tok/s | 3,800 / 3,370 tok/s |
| Tool/reasoning probe | 13/14 (qwen3_coder and qwen3_xml) | 13/14 (qwen3_xml) |
| LitQA2 first 30, egress off: correct / abstain / wrong | 22 / 8 / 0 (acc 0.733) | 20 / 10 / 0 (acc 0.667) |
| Paired delta | +0.067 [-0.067, +0.200], p=0.424 | |
| vs historical Qwen3.6 on the same 30 | +0.000 [-0.10, +0.10] | |
| Mean / median s per question | 49.8 / 32.4 | 95.7 / 79.5 (24 of 30 on TP=2, latency indicative only) |
| Tool calls per question, hard error rate | 12.07, 0.0 | 5.10, 0.026 |

Gate rule. Promote Sage only if all hold; otherwise keep Qwen3.8 (D2):

1. A full suite run (`scripts/run_suite.sh sage-a1.5`, run on the new
   retrieval image) shows agentic accuracy not below Qwen3.8's by more than
   the suite's CI half-width, and correct-abstention not worse.
2. Tool hard-error rate at or below Qwen3.8's; no tool markup in answers.
3. No runaway generations (watch: the quantized checkpoint's
   `generation_config.json` lists only `<|endoftext|>` as stop token while the
   tokenizer's eos is `<|im_end|>`; none seen in 30 questions + probe).
4. D8-style licence check is not needed here (Apache-2.0).

On today's n=30 Sage passes 2 and 3 and is at parity on 1, which is "fit, not
clearly better". Speed is clearly better (2.6x decode, about 2x per question).
If the user accepts parity plus speed without the full suite, promotion is
reversible in one command (rollback below). Any suite run writes scorecards:
copy each new `backend/benchmarks/scorecards/*.json` into
`docs/paper-kit/scorecards/` in the same commit.

### 3.3a Promote Sage (gate passed)

```bash
# Optional D5: set VLLM_MAX_NUM_SEQS_SINGLE=3 in backend/config/models/sage-a1.5.env, commit, then:
sudo $R/deploy.sh vllm                                                        # [sudo] re-installs profiles
sudo $R/deploy.sh model activate sage-a1.5                                    # [sudo] writes active-model.env, stages tokenizer, recreates retrieval (1-2 min); refuses if the 0.33.0 venv lacks vllm
sudo vllm-service stop; sudo vllm-service start single                        # [sudo] D3; ready in ~64 s
curl -s http://127.0.0.1:8000/v1/models    # adjust to the serving port in the vLLM log; expect sage-a1.5
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | grep -iE 'model|vllm'
```

### 3.3b Keep Qwen3.8 (gate not passed)

```bash
sudo vllm-service stop; sudo vllm-service start single                        # [sudo] D3 (or tp2 to restore the old layout, which blocks step 4)
```

The 0.33.0 venv and Sage weights stay installed beside the old venv.

### 3.4 Re-arm the vLLM automation (both outcomes; required)

```bash
sudo rm /opt/munin/logs/vllm-health.hold                                      # [sudo]
sudo sed -i 's/^# HOLD-2026-11-12 0 6 \* \* \* root/0 6 * * * root/' /etc/cron.d/hugin-cluster   # [sudo] restores the 06:00 start
grep -n schedule-vllm /etc/cron.d/hugin-cluster                               # expect both 02:00 stop and 06:00 start active
```

Without this, the active 02:00 stop line takes vLLM down and nothing brings it
back. If step 4's bake-off still needs a GPU later, re-create the hold only for
that window. The health timer and cron start vLLM in whatever mode
`vllm-service` saved (single after 3.3).

Rollback (Sage to Qwen3.8):
`sudo $R/deploy.sh model activate qwen3.8-27b && sudo vllm-service stop && sudo vllm-service start single` `[sudo]`
(use `tp2` instead of `single` only if no GPU1 engine runs).

## 4. GPU1 engine: Strata vs Basalt (lanes D1, D2)

**No engine number exists.** Both lanes were stopped by the permission
classifier before running downloaded third-party code, and neither tried to
get around it. The table compares what is known; the pick is the user's.

| | Strata (D1) | Basalt (D2) |
|---|---|---|
| Version | v0.2.13 (Terminal/control 0.1.59.1 hotfix, engine 0.1.59 CUDA 13) | v0.3.2, commit ffc68faa (released 2026-11-08, 4 days before testing) |
| Delivery | Prebuilt binaries from a signed release feed; complete local feed mirrored, offline install | Built from source (cmake, est. 5 min) |
| Pack | Unsloth Qwen3.8-Flash-Next-GGUF UD-Q4_K_XL @ 38bb39ee, 4 shards, 111.3 GB | Same quant converted to `.basalt` @ 65de5a9e, 115,307,561,176 bytes, keeps the original MTP drafter |
| Staged + verified | 11/11 files SHA-256 ALL_OK (105 GiB) | pack sha256 OK (MODEL_OK), engine source unbuilt |
| SLURM job | `strata-gpu1.sbatch`: partition llm-batch, 8 CPU, `--mem=150G`, 90 min | `start-basalt.sh`: partition gputraining, 8 CPU, `--mem=120G`, `--propagate=NONE` |
| `sbatch --test-only` | accepted (integrator, 2026-11-12) | accepted (lane D2) |
| Production placement | **llm-batch has MaxTime 04:00:00 and gputraining caps 128000M (< 150G)**: no current partition can run it 24x7 as written | gputraining (no time cap) fits 120G; script sets `--time=20:00:00`; the shelved 128 GB branch would break it |
| Port / key | 127.0.0.1:8201, key via `--api-key-env` | 127.0.0.1:8202, key only on the command line (visible in `ps`) |
| Tool calls | not known | grammar-constrained tool calls in the server code (read, not run) |
| Upstream claims (not ours) | 63-88 tok/s decode, 1,110-1,320 tok/s at 32k on a 32 GB R9700 | KLD 0.0496, top-1 92.63% for this pack |
| Cold start, RAM/VRAM, prefill, decode 1/2/4, tools, reasoning, compat | **not measured** | **not measured** |
| Licence | Qwen Community License 1.0 (weights), D8 | engine MIT; weights same as Strata, D8 |
| Munin wiring | none (no profile, health check or routing) | none |

Recommendation: measure before choosing. With equal numbers, lean **Strata**
for operations (prebuilt signed binaries, key not on the command line, offline
reinstall), but its job needs a partition decision (a 24x7 partition with
>=150G, or `--mem` lowered after measuring the real peak). Basalt is the
simpler SLURM fit today but is very young and needs a source build. Neither
lane wired the engine into munin; that is follow-up work whichever wins.

### 4.1 Measure (after D6)

Each session at most 90 GPU-min on `gpu:batch`, no sudo (memlock is raised
inside the job). Keep vLLM on `gpu:vllm` single, or hold it.

Strata:

```bash
S=$STAGING/strata; W=<worktree of feat/gpu1-strata>/scripts/llm/strata
$W/install-strata.sh --prefix $S --models $S/pkgs/UD-Q4_K_XL
STRATA_PREFIX=$S STRATA_MODELS=$S/pkgs/UD-Q4_K_XL STRATA_LOGDIR=$S/logs STRATA_MODE=prepare sbatch -o $S/logs/strata-gpu1-%j.out $W/strata-gpu1.sbatch
STRATA_PREFIX=$S STRATA_MODELS=$S/pkgs/UD-Q4_K_XL STRATA_LOGDIR=$S/logs sbatch -o $S/logs/strata-gpu1-%j.out $W/strata-gpu1.sbatch
python3 -I $BAKEOFF/bench.py --base http://127.0.0.1:8201/v1 --key-file $S/home/api-key --model <from /v1/models> --out $BAKEOFF/strata-results.json all
scancel -n strata-gpu1
```

Basalt:

```bash
B=$STAGING/basalt; W=<worktree of feat/gpu1-basalt>/scripts/llm/basalt
$W/install-basalt.sh engine $B/engine
cd $W && sbatch -o ~/basalt-%j.out -e ~/basalt-%j.err --time=01:30:00 --export=ALL,BASALT_PREFIX=$B/engine,BASALT_MODEL_DIR=$B/models,BASALT_KEY_FILE=$B/api-key start-basalt.sh
$BAKEOFF/sample.sh <jobid> <batch-card UUID> $BAKEOFF/basalt-mem.tsv &
python3 -I $BAKEOFF/bench.py --base http://127.0.0.1:8202/v1 --key-file $B/api-key --model qwen3.8-flash-next --out $BAKEOFF/basalt-results.json all
scancel <jobid>
```

Expect first-run fixes in both job scripts (never executed). Fill the
"not measured" row of the table from `strata.md` / `basalt.md`.

### 4.2 Promote the pick

Strata (merge `feat/gpu1-strata` into HuginSLURM first; decide its partition):

```bash
cd <HuginSLURM worktree> && sudo ./deploy.sh strata --seed $STAGING/strata/pkgs   # [sudo] copies to /opt/hugin/strata and /opt/munin/data/models/qwen3.8-flash-next-ud-q4_k_xl, scripts to /opt/cluster/scripts/llm/strata; est. 5-15 min (111 GB copy)
```

Basalt (merge `feat/gpu1-basalt`; build the engine into the stage dir first):

```bash
cd <HuginSLURM worktree> && sudo ./deploy.sh basalt $STAGING/basalt          # [sudo] verifies against basalt.env, engine to /opt/cluster/engines/basalt/v0.3.2, pack to /opt/munin/data/models/basalt, key to /opt/munin/config/basalt-api-key (0600); est. 5-15 min
```

Cluster and user downtime: none (a new service beside vLLM). Delete the
loser's staging tree afterwards (105-107 GiB each).

## 5. Haiku fallback layer 1 on the cluster (lane E)

Cluster downtime: one retrieval recreate (1-2 min), one Prometheus recreate.
User downtime: none (pre-cutover). Est. 15 min.

```bash
sudoedit /opt/hugin/config/cluster.env                                        # [sudo] add:
#   ANTHROPIC_API_KEY=<key from a dedicated Console workspace with a spend limit>
#   CLAUDE_MODEL=claude-haiku-5-5
#   CLAUDE_FALLBACK_MODEL=                     (D15: no Sonnet retries)
#   FAILOVER_SECONDARY_DAILY_TOKENS=20000000
sudo $R/deploy.sh --dry-run fallback                                          # [sudo] expect only llm-claude + llm-failover
sudo $R/deploy.sh fallback                                                    # [sudo]
sudoedit /opt/hugin/config/cluster.env                                        # [sudo] add LLM_BASE_URL=http://llm-failover:8000 (keep VLLM_URL on the real vLLM)
sudo $R/deploy.sh retrieval                                                   # [sudo] recreate only; image already built in step 2
sudo $R/deploy.sh monitoring                                                  # [sudo] adds the scrape job
docker exec munin-llm-failover python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://localhost:8000/status')))"
curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | grep -A4 llm_fallback
```

Optional live test (cluster still private): `sudo vllm-service stop`, send one
chat through the cluster API, watch `docker logs -f munin-llm-failover`, start
vLLM again; the route returns after 6 good checks and at least 120 s.

Shortcut: adding `LLM_BASE_URL` and running `fallback` before step 2.3 saves
one retrieval recreate. Kept separate here so step 2 is verifiable on its own.

Rollback: remove `LLM_BASE_URL`, `sudo $R/deploy.sh retrieval`, then
`cd /opt/munin/docker && sudo docker compose --profile llm-claude --profile llm-failover stop llm-failover llm-claude` `[sudo]`.

## 6. Cutover: Lite to cluster

User downtime: from 6.3 (freeze) to 6.10 (tunnel up), est. 20-40 min. The
merge itself measured 0.77 s for the DB on a copy, 1.8 s for 840 synthetic
points, 0.16 s for 28 synthetic files.

Do D14 a day earlier: steps 6.4 and 6.8 (dry runs only) against the live Lite
state, to find conflicts while nothing is frozen.

**6.1 Verify the cluster end to end (local).** `sudo $R/deploy.sh verify`
`[sudo]`; `/api/status` shows vLLM up with the intended model and
`llm_fallback` on primary; one chat through 127.0.0.1:8080 with an admin API
key that uses `paper_search` and `run_python`; `show_protein` registered; GPU
state as intended (`nvidia-smi`, `squeue`). Est. 10 min.

**6.2 Announce** the window to users (time, expected 30 min, chats are kept).

**6.3 Freeze Lite** (maintenance screen, Deep Research refused). Wait 1-2 min
for turns in flight.

```bash
echo "{\"message\": \"Moving back to the cluster\", \"since\": \"$(date -u +%FT%TZ)\"}" |
  ssh -i $KEY $VPS 'docker exec -i munin-retrieval sh -c "cat > /data/maintenance.json"'   # [VPS]
```

The flag does not block API-key clients; 6.7 catches anything they write.

**6.4 Fetch** (read-only on the VPS; needs Lite retrieval running).

```bash
STAGE=$STAGING/lite-merge/$(date -u +%Y%m%dT%H%MZ)
$R/scripts/maintenance/fetch_lite_state.sh --host $VPS --key $KEY --out $STAGE
python3 -m json.tool $STAGE/manifest.json | head -40   # row counts, user_docs.files (expect ~28), qdrant_export.points == remote_points_count (expect ~840)
```

**6.5 Stop the tunnel** so nothing grabs 18080 when Lite stops.

```bash
sudo systemctl stop munin-tunnel                                              # [sudo]
```

**6.6 Stop Lite retrieval.** Users now get gateway errors behind the
maintenance screen.

```bash
ssh -i $KEY $VPS 'cd ~/munin-lite && docker compose stop retrieval'          # [VPS] (the VPS Lite project, not the cluster repo)
```

**6.7 Final Lite snapshot** (cold copy; the container is stopped, so a file
copy of the DB and its WAL is consistent).

```bash
ssh -i $KEY $VPS 'F=~/munin-lite/backups/final-'$D'; mkdir -p $F && cp -a ~/munin-lite/.runtime/data/chats.db* $F/ && chmod -R go-rwx $F && ls -la $F'   # [VPS]
mkdir -p $STAGE/final && scp -i $KEY "$VPS:munin-lite/backups/final-$D/chats.db*" $STAGE/final/
```

**6.8 Merge** (lane A, `docs/install/lite-merge.md`). Stage inside the
production retrieval container, dry-run all three, then apply files, points,
DB in that order.

```bash
TOOL=$R/scripts/maintenance/merge_state.py
docker exec munin-retrieval mkdir -p /data/merge
docker cp $STAGE munin-retrieval:/data/merge/lite
docker cp $TOOL munin-retrieval:/tmp/merge_state.py
M="docker exec munin-retrieval python /tmp/merge_state.py"
SRC=/data/merge/lite/final/chats.db     # the cold copy from 6.7

$M db --source $SRC --target /data/chats.db --work-dir /data/merge/work --backup-dir /data/backups/merge --docs-dir /data/merge/lite/user_docs
$M files --source-dir /data/merge/lite/user_docs --target-dir /data/user_docs
$M qdrant --source /data/merge/lite/user_docs.points.jsonl
```

Go/no-go: all three exit 0, no `refused` key, DB plan close to the rehearsal
(603 rows on 2026-11-12, more by now), 0 conflicts, 0 orphans,
`external.attachment_refs_without_file_in_docs_dir` = 0. If anything is
refused, read `conflict_samples` / `orphan_samples` before any policy flag;
if unsure, abort to rollback A.

```bash
$M files --source-dir /data/merge/lite/user_docs --target-dir /data/user_docs --apply
$M qdrant --source /data/merge/lite/user_docs.points.jsonl --apply
$M db --source $SRC --target /data/chats.db --work-dir /data/merge/work --backup-dir /data/backups/merge --apply
```

The DB report must say `"result": "merged"`, `"replan_after_write": "no-op"`
and equal `messages` / `messages_fts` in `fts_after`. Run each again: all must
report nothing to do. Known: 9 Lite `sandbox_generated` artifacts keep their
rows but their files are gone (they lived in Lite's RAM sandbox), and one
cluster conversation has 3 assistant messages at one index (2026-08-27
double-persist bug), copied as is.

```bash
docker exec munin-retrieval rm -rf /data/merge /tmp/merge_state.py           # keep /data/backups/merge until users confirm
```

**6.9 Restore the ingest path** so the queued KB upload is processed.
`hook_service.py` skips the cluster push when `ADMIN_INGEST_TOKEN` is empty,
and the cluster refuses with 503 when its own is unset; files stay in
`/mnt/uploads/complete/` either way.

```bash
sudo grep -c '^ADMIN_INGEST_TOKEN=.\{64\}' /opt/hugin/config/cluster.env      # [sudo] expect 1
ssh -i $KEY $VPS 'grep -c "^ADMIN_INGEST_TOKEN=.\{64\}" ~/munin/frontend/.env; ls /mnt/uploads/complete | head'   # [VPS] expect 1, and the queued file(s)
```

If the frontend value was blanked or changed for Lite, restore it from the
pre-reconnect backup (or the cluster value) and recreate only the hook service:
`cd ~/munin/frontend && docker compose up -d --no-deps hook-service` `[VPS]`.
If the backfill cron is installed, `/root/.backfill.env` needs the same token
(`[VPS sudo]`). The queued file is pushed on the next retry sweep once 6.10 is
done; to push it now, run `backfill_contributed.py` once per
`frontend/scripts/BACKFILL-README.md`. Which exact change was made for Lite
was not visible to this work (VPS read-only); confirm before editing.

**6.10 Start the tunnel.**

```bash
sudo systemctl start munin-tunnel                                             # [sudo]
journalctl -u munin-tunnel -n 5 --no-pager                                    # no "remote port forwarding failed"
ssh -i $KEY $VPS 'curl -s 127.0.0.1:18080/health'                             # [VPS] answers from the cluster
```

Check first that the gateway's `BACKEND_URL` is `http://127.0.0.1:18080`
(Lite used the same port, so it should be unchanged), and that the VPS webui
and Caddy include the artifact features (a702931, 528943f, 03512e4, 95279af);
deploy the frontend per `docs/LITE-DEPLOY.md` if they are behind.

**6.11 Verify through the VPS** (est. 15 min):

- Signed out: `curl -sI https://$DOMAIN/` and the chat and admin paths
  answer 302 to the login page; `/api/*` without a session is refused.
- A live chat as a normal user: streams, uses a paper tool, model name in
  `/api/status` is the intended one, `llm_fallback` on primary.
- History: an old cluster conversation and a Lite-era conversation (created
  after 2026-10-21) both list, open with all messages and are found by search.
- Artifacts: the artifact viewer opens a document artifact, one of the 3 Lite
  widget artifacts (`application/vnd.munin.widget+json`) renders, and a
  protein (`show_protein` / `/structure`) renders in the viewer.
- Uploads: a user document upload completes and is searchable in chat; the
  queued KB upload has moved to `processed/` on the VPS.

Then announce the end of the window. Lite's maintenance flag stays until step 7.

**Rollback A (before 6.10, merge refused or verification of 6.8 failed):**
restore the DB if it was applied:
`docker exec munin-retrieval python -c "import sqlite3,sys; s=sqlite3.connect(sys.argv[1]); d=sqlite3.connect('/data/chats.db'); s.backup(d)" /data/backups/merge/chats.db.pre-merge-<stamp>.db`;
start Lite again (`cd ~/munin-lite && docker compose start retrieval` `[VPS]`),
remove the flag (`docker exec munin-retrieval rm /data/maintenance.json` `[VPS]`),
leave munin-tunnel stopped. Users are back on Lite with nothing lost.
Copied files and points are additive and harmless; remove them only if needed
(Qdrant snapshot from step 0).

**Rollback B (after 6.10, cluster misbehaves):** `sudo systemctl stop munin-tunnel`
`[sudo]`, start Lite retrieval and remove its flag `[VPS]`. Rows users wrote
on the cluster meanwhile go back with the reverse merge,
`--since <cutover time> --on-conflict newer-wins` (lite-merge.md, "The other
direction").

## 7. Haiku fallback layer 2 on the VPS (lane E)

Only after the cluster has run cleanly for an agreed period (suggested: a few
days). User downtime: one api-gateway recreate (seconds). Est. 30 min. All
`[VPS]`, per `docs/ops/HAIKU-FALLBACK.md` "VPS".

1. Lite becomes the standby on 18081: in `~/munin-lite/.env` set
   `RETRIEVAL_HOST_PORT=18081`, remove the maintenance flag only after the
   container is up on 18081, then from `~/munin-lite`:
   `docker compose up -d --no-deps --no-build retrieval`. Confirm 18080 still
   answers from the cluster (`curl -s 127.0.0.1:18080/health`) and 18081 from
   Lite.
2. Gateway: upload `frontend/gateway/` and `frontend/docker-compose.yml` from
   main, add to `~/munin/frontend/.env`:
   `BACKEND_STANDBY_URL=http://127.0.0.1:18081` and `BACKEND_FAILBACK=manual`
   (D16), then `docker compose build api-gateway && docker compose up -d --no-deps --no-build api-gateway`.
   Log line: `backend failover on: cluster ..., standby ..., mode normal`.
3. Web UI with the DegradedBanner (`docs/LITE-DEPLOY.md`, "Web UI").
4. Check as an admin: `GET https://$DOMAIN/api/backend-mode` shows
   `mode: normal`, cluster ok, standby ok. A forced rehearsal
   (`PUT /api/backend-mode {"mode":"reduced"}`) is a real episode and needs a
   merge afterwards.
5. After every reduced episode: run step 6.3-6.8 with
   `fetch_lite_state.sh --since <episode start>` and the DB merge with
   `--since <episode start>`, then switch back (manual failback).
6. Keep Lite's code in step with cluster schema changes, or the merge needs
   `--allow-schema-drift`.

Rollback: remove `BACKEND_STANDBY_URL` from the frontend `.env` and recreate
api-gateway; the gateway then behaves exactly as before.

## Afterwards

- D13: `sudo systemctl disable --now deepresearch-daemon`, remove its unit,
  scripts and `/opt/munin/data/models/mirothinker-v1.5-30b` (lane B step 9).
  `[sudo]`
- Install the VPS cluster probe from `v1.1-alerts-rebased` (MONITORING.md).
- Remove the lane test images (`munin-retrieval:laneb-catchup-fcc4dd1`,
  `munin-sandbox:laneb-catchup-fcc4dd1`, `munin-retrieval-tests:laneb-catchup`)
  once production runs the new images.
- Push the merged branches (operator).

## Known gaps (not done or not measured)

- Real Lite Qdrant export (840 points) and Lite files (28, 87 MB) never
  dry-run against the cluster's 12,807 points / 408 files.
- Strata and Basalt: every protocol number.
- Sage at TP=2, Sage beyond 64k, Qwen3.8 on vLLM 0.33.0.
- New retrieval image boot and model-load time.
- Retrieval live test tier, Playwright e2e, single-host smoke on the merged
  tree.
- Layer 1 against a real vLLM failure under load, a real Anthropic 402, real
  Claude latency and cost; layer 2 against the real tunnel and Lite.
- Which ingest setting was changed on the VPS for Lite (6.9).
