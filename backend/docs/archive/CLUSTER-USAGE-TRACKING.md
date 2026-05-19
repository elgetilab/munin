# Cluster Issue: Empty `usage` object in SSE `done` event

## Summary

The VPS gateway logs every chat completion request to track per-user
token consumption. It extracts token counts from the SSE `done` event
emitted by the cluster. Currently the cluster sends an **empty usage
object**, so all token counts are recorded as 0.

## Current behaviour

The final two SSE lines of a chat completion stream look like this:

```
event: done
data: {"usage": {}, "finish_reason": "stop"}
```

## Expected behaviour

```
event: done
data: {"usage": {"prompt_tokens": 1234, "completion_tokens": 567}, "finish_reason": "stop"}
```

`total_tokens` is optional — the gateway will compute it from
`prompt_tokens + completion_tokens` if missing.

## Where the gateway reads this

`gateway/main.py`, inside `stream_and_log()`:

```python
for line in text.split("\n"):
    if line.startswith("data: ") and '"usage"' in line:
        data = json.loads(line[6:])
        usage = data.get("usage", {})
        tokens_total = usage.get("total_tokens", 0) or (
            usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        )
```

The same pattern is used for non-streaming responses (JSON body) and
the `/v1/*` OpenAI-compatible proxy.

## What needs to change on the cluster

Find where the `done` SSE event is emitted (likely at the end of the
streaming response generator in the chat completions endpoint) and
populate the usage dict with actual token counts from the model
response.

If the cluster uses vLLM, the `CompletionOutput` / `RequestOutput`
object contains `prompt_token_ids` and `output_token_ids` (or
`usage` on the response). For a typical vLLM setup:

```python
# After the stream finishes:
usage = {
    "prompt_tokens": len(request_output.prompt_token_ids),
    "completion_tokens": len(request_output.outputs[0].token_ids),
}

# Emit the done event:
yield f"event: done\ndata: {json.dumps({'usage': usage, 'finish_reason': finish_reason})}\n\n"
```

If wrapping an OpenAI-compatible client, the usage is usually in the
final chunk or the aggregated response object:

```python
usage = {
    "prompt_tokens": response.usage.prompt_tokens,
    "completion_tokens": response.usage.completion_tokens,
}
```

## Impact

- **Admin dashboard** (`chat.muninai.org` → Profile → Admin → Usage tab):
  all per-user token counts show as 0.
- **Per-user quota enforcement**: the gateway checks monthly token usage
  against `quotas.yml` limits. With 0 tokens logged, quotas are
  effectively unenforced.
- **`/api/usage/me`**: users see 0 tokens used.

## How to verify the fix

1. Deploy the cluster change.
2. Send a chat message via `chat.muninai.org`.
3. Check the gateway DB:
   ```bash
   docker exec munin-api-gateway-1 python3 -c '
   import sqlite3
   conn = sqlite3.connect("/data/gateway.db")
   r = conn.execute("SELECT tokens_total, tokens_input, tokens_output FROM usage_log ORDER BY timestamp DESC LIMIT 1").fetchone()
   print(f"total={r[0]} in={r[1]} out={r[2]}")
   '
   ```
4. Expect non-zero values.

## Optional: `tokens_input` / `tokens_output` columns

The gateway also has `tokens_input` and `tokens_output` columns in
`usage_log`, but currently only populates `tokens_total`. If the cluster
provides `prompt_tokens` and `completion_tokens` separately, the gateway
can be updated to store them individually. This is a minor change on the
VPS side once the cluster data is available.

## Related files

| Location | File | Role |
|----------|------|------|
| VPS | `gateway/main.py` | SSE extraction + logging |
| VPS | `gateway/main.py` → `log_usage()` | Writes to `usage_log` table |
| VPS | `gateway/main.py` → `check_rate_limits()` | Reads `tokens_total` for quota enforcement |
| VPS | `gateway/main.py` → `usage_admin()` | Admin dashboard data |
| Cluster | Chat completions endpoint | Needs to emit usage in `done` event |
