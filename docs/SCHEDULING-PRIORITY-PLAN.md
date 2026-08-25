# Scheduling priority: browser chat ahead of API traffic

Status: PLAN, not approved. Written 2026-08-25.

Cross-cut (cluster serve flag + retrieval request body). See the pointer in
`shared/docs/BACKEND-FRONTEND-SYNC.md`.

---

## 1. The problem

vLLM serves 8 batch slots (`--max-num-seqs 8`, TP=2 profile) under **FCFS**
scheduling. Nothing distinguishes an interactive browser chat turn from a
scripted API-key request, so a client looping over a large repo can hold every
slot and an interactive turn waits behind it.

Measured 2026-08-25 on the TP=2 profile, 300-token generations, thinking off:

| N | aggregate tok/s | per-request tok/s | p95 latency |
|---|---|---|---|
| 1 | 108.2 | 108.2 | 2.8s |
| 4 | 347.9 | 87.0 | 3.4s |
| 8 | 604.4 | 75.6 | 4.0s |
| 12 | 486.5 | 40.5 | 7.4s |

8 is the knee; past it throughput *falls* and latency roughly doubles. Those are
300-token generations. Real traffic at `reasoning_effort: medium` runs into the
thousands of tokens, so a held slot is minutes, not seconds, and the queueing
penalty in practice is far worse than the table suggests.

Per-user concurrency caps do **not** solve this: three API users at 4 each is 12
concurrent, exactly the collapse row. Capping bounds one client, it does not
reserve capacity for chat. Priority does, and unlike a reservation it wastes
nothing when chat is idle.

## 2. What vLLM gives us (verified on the installed build)

Installed vLLM is `0.20.2rc1.dev221+gac062147f`.

- `--scheduling-policy {fcfs,priority}` exists; the config default is `fcfs`,
  which is what both serve scripts run today.
- `vllm.entrypoints.openai.chat_completion.protocol.ChatCompletionRequest`
  carries `priority: int = 0`.
- **Lower means earlier.** This is the opposite of the intuitive reading and is
  the single easiest thing to get backwards here.
- The field's own description: *"Any priority other than 0 will raise an error
  if the served model does not use priority scheduling."*

That last point sets a hard ordering constraint: **the serve flag must be live
before anything sends a non-zero priority**, or every API request 400s while
browser chat keeps working. An asymmetric failure that looks like "the API is
broken" rather than "the config is half-applied".

## 3. Design

**Chat keeps the default 0 (highest). Only API traffic is demoted.**

That choice is deliberate: it means the interactive path needs no change at all,
so the blast radius is one function, and a misconfiguration degrades API latency
rather than breaking chat.

**Where the distinction already exists.** `main.py:_is_raw_mode_request()`
already separates the two: an API-key request arrives with no persona, no
`conversation_id`, no `project_id` and is routed to `_raw_chat_proxy`, while
browser chat goes through the persona path in `chat_service`. So the signal
needs no new header, and it also covers any direct API use that does not
traverse the VPS gateway.

**Setting it in the cluster, not the gateway.** The VPS gateway relays bodies
byte-for-byte; injecting a field there would mean parsing and re-serialising
every request, including streaming ones. The backend already destructures the
body in `_raw_chat_proxy`, so that is where it belongs.

Implementation, mirroring the existing `reasoning_effort_fields()` pattern:

```python
# database.py
# 0 = disabled (and the only value that is safe when the server is on FCFS).
LLM_PRIORITY_API = int(os.getenv("LLM_PRIORITY_API", "0"))

def api_priority_fields() -> dict:
    """Demote API-key traffic below interactive chat, or `{}`."""
    return {"priority": LLM_PRIORITY_API} if LLM_PRIORITY_API else {}
```

```python
# main.py, in _raw_chat_proxy, beside the reasoning-effort default
if "priority" not in forward:
    forward.update(api_priority_fields())
```

**Default 0 means the feature ships inert.** Turning it on is one env var, and
the failure mode of forgetting the serve flag is contained to whoever sets it.

Suggested value: `LLM_PRIORITY_API=100`. The magnitude is irrelevant (any
positive number sorts after 0); 100 leaves room to introduce intermediate tiers
later without renumbering.

## 4. Rollout order (the flag first, always)

1. Add `--scheduling-policy priority` to **both** `start-vllm-service.sh` and
   `start-vllm-service-tp2.sh`. Deploy with `deploy.sh vllm`, restart vLLM.
   At this point every request is still priority 0, so behaviour is identical to
   FCFS. **This step is a no-op by design** and can be verified as such.
2. Assert the flag took: send one request with `"priority": 100` directly to
   vLLM and confirm a 200, not a 400. This is the gate; do not proceed on a 400.
3. Set `LLM_PRIORITY_API=100` in the cluster env, `deploy.sh retrieval`.
4. Verify under load (section 6).

Rollback is unsetting `LLM_PRIORITY_API` and redeploying retrieval. The serve
flag can stay: with everything at 0, `priority` and `fcfs` behave the same.

## 5. Risks

- **Getting the sign backwards.** Lower is earlier. Setting chat to 100 and
  leaving API at 0 would achieve precisely the opposite of the goal, and would
  look fine in every smoke test that does not measure under contention.
- **API starvation.** Under sustained chat load, API requests could wait a long
  time. With 8 slots and current volumes this is unlikely, but it is unbounded
  in principle, so section 6 includes a starvation check. If it bites, the fix
  is a floor (age-based promotion) rather than abandoning priority.
- **Preemption cost.** vLLM's priority scheduler may preempt a running
  lower-priority request. On this model that is dearer than usual: Qwen3.8 is a
  hybrid, and its Gated DeltaNet recurrent state has to be **recomputed** rather
  than re-read from a KV page. Worth measuring whether preemption actually
  fires, rather than assuming it does not.
- **Half-applied config.** Covered by the ordering in section 4.
- **Benchmarks are unaffected.** The Track D bare/RAG arms in
  `munin_bench/ablation/vllm_answer.py` talk to vLLM directly and send no
  `priority`, so they stay at 0. Nothing about the paper numbers moves.

## 6. Verification

The point is contention behaviour, so a smoke test proves nothing. Needed:

1. **No-op check** (after step 1): re-run the concurrency sweep from section 1.
   Numbers should match within noise.
2. **Contention check**: saturate all 8 slots with long API-style requests, then
   time an interactive chat turn to first token. Compare against the same
   measurement on FCFS. The FCFS number is the thing this feature exists to
   improve; capture it *before* step 1 or it is lost.
3. **Starvation check**: with chat traffic running continuously, confirm the API
   requests still complete and record their worst-case wait.
4. **Preemption check**: watch the engine log for preemption during the
   contention test.

## 7. Alternative, if this proves unstable

A global cap on concurrent API-key requests (say 5 of 8), enforced in the
gateway, reserving 3 slots for chat. Simpler and bounded regardless of user
count, but it wastes those 3 slots whenever chat is idle, which is most of the
time. Priority is preferred for exactly that reason; this is the fallback, not
the plan.
