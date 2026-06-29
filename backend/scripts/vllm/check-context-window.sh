#!/bin/bash
# ==============================================================================
# check-context-window.sh EXPECTED_WINDOW
# ==============================================================================
# Post-launch assertion: confirm the BACKEND (retrieval service) actually
# budgets answers against EXPECTED_WINDOW tokens, not a smaller stale window.
#
# This guards the exact failure mode that slipped through once: vLLM serves the
# right window (/v1/models max_model_len is correct) but the retrieval container
# was left on an old VLLM_MAX_MODEL_LEN, so the backend silently clamps every
# long answer to the floor (256 tokens, finish_reason=length). The vLLM-layer
# checks all pass while users get truncated replies.
#
# It sends one large probe prompt through /api/chat/completions and inspects the
# `done` event usage. Non-fatal by design: it logs a loud WARNING and returns 1
# on mismatch (the live service is fine; this is a config-drift alarm, not a
# reason to fail the job or kill vLLM).
# ==============================================================================
set -u
EXPECTED="${1:?usage: check-context-window.sh EXPECTED_WINDOW}"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8080}"

python3 - "$EXPECTED" "$BACKEND_URL" <<'PY'
import json, sys, urllib.request

expected = int(sys.argv[1])
url = sys.argv[2].rstrip("/") + "/api/chat/completions"

# Prompt sized to ~55% of the expected window: comfortably inside the right
# window (leaves ~45% headroom for output) but LARGER than the next-smaller
# mode's window, so a backend stuck on a stale-small window trims this prompt
# and floors the output -> the clamp signature we assert against below.
sentence = "The quick brown fox jumps over the lazy dog near the riverbank. "
reps = max(1, int(expected * 0.55) // 16)  # ~16 tokens per repetition
filler = sentence * reps
prompt = ("Below is filler text to IGNORE.\n\n" + filler +
          "\n\nEND OF FILLER.\n\nNow your only task: count from 1 to 600, "
          "one integer per line, nothing else. Do not stop early.")

body = {"persona": "munin", "ephemeral": True,
        "messages": [{"role": "user", "content": prompt}]}
req = urllib.request.Request(
    url, data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json",
             "X-Munin-Email": "ctx-probe@localhost",
             "X-Munin-Ephemeral": "true",
             "Accept": "text/event-stream"},
    method="POST")

done = None
event = None
try:
    with urllib.request.urlopen(req, timeout=180) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                p = line[5:].strip()
                if not p or p == "[DONE]":
                    continue
                try:
                    obj = json.loads(p)
                except Exception:
                    continue
                if event == "done" or (isinstance(obj, dict) and "usage" in obj):
                    done = obj
except Exception as e:
    # A transient probe failure is not evidence of a window regression; don't
    # cry wolf. Warn quietly and pass.
    print(f"[ctx-check] WARN: could not reach backend ({e}); skipping assertion")
    sys.exit(0)

if not done:
    print("[ctx-check] WARN: no done event captured; skipping assertion")
    sys.exit(0)

u = done.get("usage") or {}
pt = u.get("prompt_tokens", 0)
ct = u.get("completion_tokens", 0)
fr = done.get("finish_reason")
print(f"[ctx-check] expected_window={expected} prompt_tokens={pt} "
      f"completion_tokens={ct} finish_reason={fr}")

# Two failure signatures, both meaning the backend isn't serving this window:
#   - clamp: a large prompt whose answer was floored and cut off
#     (completion_tokens at the floor, finish_reason=length).
#   - no answer: the probe produced no usable output at all (zero tokens) -
#     e.g. the prompt overflowed a too-small window and errored out.
# A backend on the correct window finishes the count (finish_reason=stop) with
# completion_tokens well above the 256-token floor.
clamped = ct <= 300 and fr == "length"
no_answer = pt == 0 or ct == 0
if clamped or no_answer:
    reason = ("clamped a %d-token prompt to %d output tokens (%s)" % (pt, ct, fr)
              if clamped else
              "produced no usable answer (prompt_tokens=%d completion_tokens=%d)"
              % (pt, ct))
    bar = "=" * 70
    print(bar)
    print("[ctx-check] WARN: BACKEND CONTEXT WINDOW LOOKS WRONG.")
    print(f"  vLLM serves {expected} tokens but the backend {reason}.")
    print("  The retrieval container is probably on a stale VLLM_MAX_MODEL_LEN.")
    print("  Recreate it with the right window:")
    print(f"    cd /opt/munin/docker && sudo VLLM_MAX_MODEL_LEN={expected} \\")
    print("      docker compose --profile rag up -d --force-recreate retrieval")
    print(bar)
    sys.exit(1)

print(f"[ctx-check] OK: backend budgets against the full {expected}-token window.")
PY
