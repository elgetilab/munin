# Backbone model profiles

One file per generation model the stack knows how to serve. `sudo ./deploy.sh
model activate <slug>` copies the chosen file to
`/opt/munin/config/active-model.env`; the vLLM SLURM scripts, `deploy.sh
retrieval` (before `docker compose up`) and the embedding-map unit read that
file, so the model is named in exactly one place. `sudo ./deploy.sh instance up
<slug> ...` serves a second profile beside production on another GPU and port.

To add a model: copy the closest file, change every line, run `deploy.sh
model validate <slug>`. Every key is required unless marked optional; the
switch refuses a file with a missing key rather than guessing.

| key | meaning |
|---|---|
| `MODEL_SLUG` | file name and instance tag |
| `MODEL_ID` | HuggingFace repo, used only for the download |
| `MODEL_DIR` | directory under `$MUNIN_ROOT/data/models` |
| `MODEL_NAME` | `--served-model-name`, what every client sends as `model` |
| `MODEL_DESC` | one line for banners and `instance ls` |
| `HF_EXCLUDE` | optional, space-separated `hf download --exclude` globs |
| `VLLM_VENV` | optional; the vLLM venv this model runs on. Blank or absent = `/opt/munin/services/vllm/venv`. A newer build is installed by HuginSLURM `deploy.sh vllm-venv <version>` under `/opt/munin/services/vllm-<version>/venv`; `model activate` refuses a profile whose venv has no `vllm`, so switching back to the previous profile also switches the engine back |
| `VLLM_QUANTIZATION` | `--quantization` value; blank lets vLLM read the checkpoint |
| `VLLM_KV_CACHE_DTYPE` | `fp8` on Ada or newer; `auto` on Ampere |
| `VLLM_TOOL_PARSER`, `VLLM_REASONING_PARSER` | must exist in the pinned vLLM build |
| `VLLM_EXTRA_ARGS` | optional, appended verbatim to `vllm serve` |
| `VLLM_GPU_MEM_UTIL`, `VLLM_MAX_MODEL_LEN` | serving profile; the window is 65536 for every committed number |
| `VLLM_MAX_NUM_SEQS_SINGLE`, `VLLM_MAX_NUM_SEQS_TP2` | admission cap per profile; the KV gate at start confirms the pool covers it |
| `LLM_THINKING_MODE` | how sub-tasks turn reasoning off: `enable_thinking`, `effort_low`, `none` |
| `LLM_REASONING_EFFORT` | user-facing turn effort; blank = do not send |
| `LLM_THINKING_BUDGET` | optional; cap on the reasoning trace in tokens, sent as vLLM's per-request `thinking_token_budget` (vLLM >= 0.33) on thinking-on calls, never more than half of that call's `max_tokens`. Blank or absent = none. For a backbone without an effort dial (sage-a1.5) |
| `VLLM_REASONING_END_STR` | optional; the text vLLM forces when a thinking budget runs out, passed as `--reasoning-config` by the start scripts. `\n` escapes are decoded. Blank or absent = vLLM's default (the parser's bare end token) |
| `KV_KIB_PER_TOKEN` | fp8 KV bytes per token, for the memory arithmetic and the gate |
| `SAMPLING_DEFAULT`, `SAMPLING_CODE` | JSON, the vendor's recommended sampling per persona class |
| `LLM_VISION` | optional; `0` for a text-only backbone (Sage-A1.5, gpt-oss), which the backend then sends no images: plot feedback relies on the text `figure_summary`, attached images become a note and an input file for code. Blank decides from `VLLM_EXTRA_ARGS` (`--language-model-only` means text-only) |

The sampling values are part of the system under test: every committed
benchmark number for a backbone was produced under that backbone's profile,
and changing a profile invalidates its numbers.

Test starts before promotion: `MUNIN_MODELS_DIR` (checkpoint root, default
`$MUNIN_ROOT/data/models`) and `MUNIN_VLLM_VENV` (overrides the profile's
venv) let `start-vllm-instance.sh` serve a profile from staging paths, e.g.
`sbatch --gres=gpu:vllm:1 --export=ALL,MUNIN_MODEL_PROFILE=<file>,MUNIN_MODELS_DIR=$HOME/munin-staging/models,MUNIN_VLLM_VENV=$HOME/munin-staging/vllm-0.33.0/venv,INSTANCE_HOST=127.0.0.1,INSTANCE_PORT=8100,INSTANCE_STATE_DIR=$HOME/munin-staging/state start-vllm-instance.sh`.
