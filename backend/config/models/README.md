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
| `VLLM_QUANTIZATION` | `--quantization` value; blank lets vLLM read the checkpoint |
| `VLLM_KV_CACHE_DTYPE` | `fp8` on Ada or newer; `auto` on Ampere |
| `VLLM_TOOL_PARSER`, `VLLM_REASONING_PARSER` | must exist in the pinned vLLM build |
| `VLLM_EXTRA_ARGS` | optional, appended verbatim to `vllm serve` |
| `VLLM_GPU_MEM_UTIL`, `VLLM_MAX_MODEL_LEN` | serving profile; the window is 65536 for every committed number |
| `VLLM_MAX_NUM_SEQS_SINGLE`, `VLLM_MAX_NUM_SEQS_TP2` | admission cap per profile; the KV gate at start confirms the pool covers it |
| `LLM_THINKING_MODE` | how sub-tasks turn reasoning off: `enable_thinking`, `effort_low`, `none` |
| `LLM_REASONING_EFFORT` | user-facing turn effort; blank = do not send |
| `KV_KIB_PER_TOKEN` | fp8 KV bytes per token, for the memory arithmetic and the gate |
| `SAMPLING_DEFAULT`, `SAMPLING_CODE` | JSON, the vendor's recommended sampling per persona class |

The sampling values are part of the system under test: every committed
benchmark number for a backbone was produced under that backbone's profile,
and changing a profile invalidates its numbers.
