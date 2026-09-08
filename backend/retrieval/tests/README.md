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

Two files genuinely require this: `test_source_evidence.py` (17/17 with the
network, 14/17 without) and `test_paper_search_merge.py` (11/11 with, 6/11
without). Everything else in the directory passes with no network at all, so a
failure in a third file is a real one, not the trap.

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

## Point every test at a throwaway database

`chat_store` reads `CHATS_DB_PATH` **once, at module import**, and otherwise
defaults to `/data/chats.db`. That is the live chat database in the deployed
container. A test that imports `chat_store` (directly, or via `chat_service`,
or via a lazy `import chat_store` inside a tool) without setting that env var
first therefore opens production and runs `init_db()` against it: the
`CREATE TABLE IF NOT EXISTS` block, the migration block, and
`PRAGMA journal_mode=WAL`. `test_build_full_system_prompt.py` was doing
exactly this until 2026-09-08.

Set it before the first import that can reach `chat_store`:

```python
_DB_DIR = tempfile.mkdtemp(prefix="munin-test-<name>-")
os.environ["CHATS_DB_PATH"] = os.path.join(_DB_DIR, "chats.db")
```

Related trap: if a test swaps a fake in with `sys.modules["chat_store"] = fake`,
restore the previous entry afterwards rather than `sys.modules.pop(...)`.
Popping leaves the real module, and its open connection, unreachable by name,
and a later `import chat_store` builds a fresh module whose `_db` is `None`,
so `close_db()` then closes nothing and the process still hangs.

## Don't assert against shipped config you don't control

`test_plan_approval_hook.py` gated on `delegate_to_persona` because
`shared/personas/research.json` declared it in `params.plan_approval`. Commit
`158e70c` deleted the tool and that config together, and the tests failed for
two and a half months while the code they cover was fine.

Worse, one of them kept PASSING for the wrong reason. With no persona list at
all, `_is_gated_call` falls through to the model-flag branch, which gates
every non-plan tool, so "this tool is not gated" could not fail.

Build the fixture in the test instead: supply a persona dict declaring the
behaviour under test. The test then covers the branch rather than the current
contents of a JSON file someone may legitimately change.

## Every file must run under plain `python`, including the pytest-shaped ones

Nine files in this directory could not even be imported by
`python tests/test_x.py`, so their assertions had never executed in the
container. Three distinct causes, all fixed 2026-09-08:

**No path preamble.** Running `python tests/test_x.py` puts `/app/tests` on
`sys.path`, NOT `/app`, so `import mcp...` / `import main` /
`import deep_research_agent` raised `ModuleNotFoundError`. Those files only
ever worked under pytest, which adds the rootdir itself, while their own
docstrings documented the `docker exec ... python /app/tests/...` invocation
that could not work. Every file needs:

```python
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

**No standalone runner.** Fixing the import alone made four files exit 0
having run nothing, which is worse than failing: a silent green. They now
carry a `__main__` block that runs each zero-argument `test_*` and reports
functions taking parameters as `[SKIP] ... (needs a pytest fixture)` rather
than calling them with nothing.

**Genuinely pytest-native files.** `test_contributors_sync.py` (every test
takes `tmp_path`/`monkeypatch`) and `test_llm_endpoint_config.py`
(`@pytest.mark.parametrize`) have no honest standalone form. pytest is not
installed in the container, so they guard the import and print one SKIP line
naming the host command instead of dying with `ModuleNotFoundError`.

## A test that cannot run is a test that rots

Two of the nine had been quietly wrong for months behind the import error.

`test_vllm_window_consistency.py` parses the SLURM launch scripts. Its
`check-context-window.sh` pattern predated a refactor that resolves the probe
into `$CHECK_WINDOW` first, so it matched the `[WARN] check-context-window.sh
not found` message and extracted the word `not`. And its
`_sh_int` could not read `VAR=${VAR:-65536}`, the overridable form the tp2
script moved to. Worst of all, its runner let the first `AssertionError`
propagate, so the tp2 half never ran while the single-GPU half was red.

Underneath all that sat a stale assertion: `tp2 window > single-GPU window`,
on the reasoning that TP=2 exists to grow the context window. Commit `2d931e2`
retargeted TP=2 at concurrency and fixed the window at 65,536 on purpose, so
the correct invariant is now that the two modes AGREE. Nothing was broken in
the product; the test had simply been unable to notice the design change.
