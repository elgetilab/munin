# retrieval tests: where to run them

Most files here are standalone: they print `[PASS]`/`[FAIL]` lines and exit
non-zero on failure, so they run under plain `python` as well as pytest.

**The environment you run them in changes the result.** That is the single
thing worth knowing before reading a failure here, because it has twice
produced "pre-existing failures" that were nothing of the sort.

## The three environments

| | has `/app/personas`, `/app/config`, `/data` | has repo `shared/`, `docs/` |
|---|---|---|
| **host**, from the repo | no | yes |
| **deployed container** (`docker exec munin-retrieval`) | yes | no |
| **ad-hoc mount** (`docker run -v retrieval:/app`) | **no** | **no** |

The third is the trap. It looks like the container and is not: it is
`retrieval/` alone, without the persona, config and data mounts that
`docker-compose` gives the real one. Tests needing those fixtures cannot pass
there, and a failure means only that you picked the wrong environment.

```bash
# host
python backend/retrieval/tests/test_config_schemas.py

# deployed container, the closest thing to production
docker exec munin-retrieval python /app/tests/test_config_schemas.py
```

## Running undeployed code against real services

To test repo code before deploying it, mount the repo over `/app` but join the
compose NETWORK, not the container's namespace:

```bash
docker run --rm --network munin-network \
  --add-host host.docker.internal:host-gateway \
  -e QDRANT_HOST=qdrant -e QDRANT_PORT=6333 \
  -e VLLM_URL=http://host.docker.internal:8000 -e VLLM_MODEL_NAME=qwen3.8-27b \
  -v "$PWD/backend/retrieval:/app:ro" -w /app \
  munin-retrieval:latest python tests/test_source_evidence.py
```

`--network container:munin-retrieval` shares the netns but NOT the DNS that
resolves `qdrant`, so every test touching the index silently takes a
"collection is not built yet" branch and asserts against `None` instead of
failing loudly. Add `-v "$PWD/shared/personas:/app/personas:ro"` when a test
needs personas.

## Skipping, and why it matters

A test that cannot find its fixtures should print one `SKIP` line and exit 0,
never fail. A failure that means "wrong environment" trains readers to skim
past failures, which is exactly the habit that lets a real one through.

`test_api_contract.py`, `test_persona_prompt_split.py` and
`test_config_schemas.py` all do this, each naming the path it wanted.

If you add a test that reads something outside `/app`, resolve the path
defensively. `Path(__file__).resolve().parents[3]` raises `IndexError` from
`/app/tests/` (there are only three parents there), which crashed
`test_persona_prompt_split.py` at import in the deployed container for months.
That test would otherwise have passed, since `/app/personas` is present.

## Two ways a test lies about itself

Both of these were live in this directory until 2026-09-07, and neither
looked like what it was.

**A stub whose return SHAPE drifts.** `chat_context.assemble_context` is typed
`-> tuple[list[dict], Optional[dict]]` and its caller does
`messages, compact_info = await ...`. The stub in
`test_stream_error_persistence.py` returned a bare 2-element list, so the
unpack put one message dict into each variable and `messages` became a dict.
The one test that reached `messages.append(...)` died with
`AttributeError: 'dict' object has no attribute 'append'`, a traceback
pointing deep inside `chat_service` and nowhere near the stub. The other 18
"passed" while quietly emitting a bogus `compact_boundary` SSE, because a
truthy `compact_info` is what triggers that event.

So: when you stub a function, copy its return shape from the annotation, not
from what the test happens to consume. A shape error surfaces far from the
stub and only on the paths that read the part you got wrong.

**A suite that passes and then never exits.** `test_persona_handoff.py`
printed `9 passed, 0 failed` and hung forever. `chat_store.get_db()` caches
one `aiosqlite` connection, aiosqlite services it from a NON-daemon worker
thread, and CPython will not exit while one of those is alive. Under a CI
timeout that is indistinguishable from a test that failed.

Any test that touches `chat_store` must call `chat_store.close_db()` when it
finishes. Do it in a `finally` in `main()` and in a `teardown_module()` so
both the standalone and pytest paths are covered. An `atexit` hook does NOT
work here: CPython joins non-daemon threads *before* running atexit
callbacks, so the hook is only reached after the hang it was meant to
prevent.
