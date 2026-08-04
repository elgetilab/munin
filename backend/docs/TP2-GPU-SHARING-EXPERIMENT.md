# Experiment: running batch jobs alongside whole-GPU TP=2 vLLM

Status: **PARKED / future exploration.** Not active work. Captured so the dead
ends aren't re-walked and the one viable path is written down. Owner: varghele
(also the cluster's SLURM admin).

## Goal

Run vLLM tensor-parallel across both RTX 5090s (`start-vllm-service-tp2.sh`,
128k context) while still leaving a little GPU capacity free for a few small
batch jobs - i.e. not have TP=2 block the entire cluster.

## What was tried and CONFIRMED not to work: gres/shard (2026-06-29)

The hugin shards (`shard:batch` on GPU0, `shard:vllm` on GPU1; 8 x ~4 GB each)
look like they'd let vLLM claim 6/card and leave 2/card free. They do not, for
two independent reasons:

1. **`gres/shard` is single-GPU by design (SLURM 23.11.4).** A shard job's
   shards must all come from ONE physical GPU. The claim that would span both
   cards (`--gres=shard:vllm:6,shard:batch:6`) is UNSCHEDULABLE - it pends on
   `Reason=Resources` even on a fully idle node. Even an untyped `shard:12` fails
   (12 > 8 won't fit on one card, won't span). TP=2 needs two devices; shards
   can't deliver two devices to one job. No config setting changes this.
2. **`ConstrainDevices=yes`** (cgroup) locks a shard job to its allocated card -
   verified: a `shard:batch:2` job sees only GPU 0 via `nvidia-smi`. So forcing
   `CUDA_VISIBLE_DEVICES=0,1` is blocked at the kernel, not just the env. (This
   is the second wall; #1 stops you first.)

Net: TP=2 + free shards is impossible on this SLURM/cgroup config. The reliable
options are the **single-GPU <-> TP=2 toggle**: TP=2 (both cards, 128k, no batch)
vs `start-vllm-service.sh` (single GPU, 64k, all of GPU 0's shards free).

## The one viable path (unproven): gres/mps

CUDA MPS is the mechanism actually designed for sharing a GPU among processes,
with memory protection. Sketch:

- Add `mps` to `GresTypes` in `slurm.conf`; define per-GPU MPS in `gres.conf`
  (e.g. `Name=mps Count=100 File=/dev/nvidia0` and `.../nvidia1`).
- Run vLLM TP=2 as an MPS client claiming ~70% on EACH card; batch jobs claim
  the rest.
- Cap each client's VRAM with `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` so a batch job
  cannot OOM the server (the real protection shards lack).

### Why it's a research project, not a quick fix
- **TP=2-under-MPS is uncharted.** vLLM runs a rank per GPU with NCCL all-reduce;
  making both ranks MPS clients across two cards (plus batch jobs as other
  clients) is not a documented/common setup. Expect to debug NCCL <-> MPS.
- **MPS adds latency + sharing jitter** - bad for a latency-sensitive serving
  model. Measure chat p50/p95 with and without a co-resident batch job.
- **Cluster-wide change** (MPS control daemon lifecycle, mixing MPS/non-MPS jobs
  on a card, per-user MPS servers). Touches everyone's GPU scheduling.

### Test plan if revisited
1. Configure `gres/mps` on hugin (start with one card to learn the daemon).
2. Launch vLLM TP=2 as MPS clients on both cards (~70%); confirm NCCL init +
   both GPUs load + `/v1/models` window correct.
3. Submit a small batch job in the remaining MPS budget; confirm
   `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT` prevents it from exceeding its slice and
   OOMing vLLM.
4. Measure chat latency impact under co-resident load. Decide go/no-go.

### Recommendation
Only worth it if "batch jobs alongside 128k vLLM" becomes a frequent, hard
requirement. With Miro (MiroThinker, GPU 0's main batch tenant) being retired
(see docs/paper-track / memory), GPU 0 frees up anyway, so the simple toggle is likely the
better trade. Revisit MPS only if that assumption breaks.

See also: memory `reference_hugin_gpu_sharding`, `project_miro_retirement`;
scripts in `backend/scripts/vllm/`.
