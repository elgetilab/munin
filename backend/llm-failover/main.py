"""LLM failover proxy: vLLM first, llm-claude when vLLM is down.

Retrieval sets LLM_BASE_URL=http://llm-failover:8000 and keeps speaking
OpenAI chat-completions. Every one of its LLM call sites (chat, sub-tasks,
Deep Research, the source agent, the status probe) goes through here without
code changes. See docs/ops/HAIKU-FALLBACK.md for the design and failover.py for
the routing rules.

What this adds over pointing retrieval at one endpoint:

- an active probe of both upstreams, with hysteresis (failover.py);
- a per-request retry on the other upstream when the first attempt failed
  before any byte reached the caller. Once a byte is sent the request is
  committed: a stream that dies mid-way is dropped, which retrieval already
  handles as a dropped stream and retries through here;
- Claude-safe request bodies on the secondary (vLLM-only sampling and
  chat-template fields removed, the model name replaced);
- a cost guard: a daily token budget for the secondary, and a cooldown after
  Anthropic's spend cap answers 402;
- `/status` (JSON) and `/metrics` (Prometheus text) for admins, and a
  `munin_llm` object on `/v1/models` that retrieval copies into /api/status
  for the UI's banner.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

import failover
from failover import PRIMARY, SECONDARY

logger = logging.getLogger("llm-failover")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

# A failure of these statuses on the primary is retried on the secondary for
# that request. 404 is vLLM's answer for an unknown model name, which is what
# retrieval gets while a new backbone is loading under another name. 500 is
# included for the request but not counted as a health failure (PASSIVE_FAIL),
# because one bad request should not move every user to the secondary.
RETRY_STATUSES = {404, 500, 502, 503, 504}
PASSIVE_FAIL = {502, 503, 504}

# vLLM extensions and sampling knobs Claude does not take. llm-claude ignores
# what it does not read already; dropping them here keeps the request the
# secondary sees independent of which vLLM features retrieval uses next.
VLLM_ONLY_FIELDS = (
    "top_k", "min_p", "repetition_penalty", "presence_penalty", "frequency_penalty",
    "length_penalty", "use_beam_search", "best_of", "min_tokens", "ignore_eos",
    "skip_special_tokens", "spaces_between_special_tokens", "include_stop_str_in_output",
    "truncate_prompt_tokens", "prompt_logprobs", "add_generation_prompt",
    "continue_final_message", "echo", "documents", "chat_template", "mm_processor_kwargs",
    "guided_json", "guided_regex", "guided_choice", "guided_grammar",
    "guided_decoding_backend", "structured_outputs", "priority", "request_id",
    "logits_processors", "return_tokens_as_token_ids", "seed", "logit_bias",
    "logprobs", "top_logprobs", "n", "thinking_token_budget",
)


def env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw else default


def env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    return float(raw) if raw else default


def env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def claude_safe(body: dict, model: str, thinking_mode: str) -> dict:
    """The request as the secondary should see it.

    Retrieval marks mechanical sub-tasks with `enable_thinking: false` (Qwen
    profiles) or `reasoning_effort: low` (gpt-oss, LLM_THINKING_MODE=effort_low).
    llm-claude understands only the first, so the second is translated. A
    user-facing `reasoning_effort` (medium by default) is dropped: llm-claude
    sets its own effort from CLAUDE_EFFORT.
    """
    out = {k: v for k, v in body.items() if k not in VLLM_ONLY_FIELDS}
    out["model"] = model
    kwargs = body.get("chat_template_kwargs")
    out.pop("chat_template_kwargs", None)
    if isinstance(kwargs, dict):
        off = kwargs.get("enable_thinking") is False or (
            thinking_mode == "effort_low" and kwargs.get("reasoning_effort") == "low"
        )
        if off:
            out["chat_template_kwargs"] = {"enable_thinking": False}
    return out


def usage_tokens(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return total
    return int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)


class SSEUsage:
    """Pulls `usage` out of an SSE byte stream that arrives in arbitrary chunks."""

    def __init__(self) -> None:
        self.buffer = b""
        self.tokens = 0

    def feed(self, chunk: bytes) -> None:
        self.buffer += chunk
        *lines, self.buffer = self.buffer.split(b"\n")
        for line in lines:
            if line.startswith(b"data: ") and b'"usage"' in line:
                try:
                    tokens = usage_tokens(json.loads(line[6:]))
                except ValueError:
                    continue
                if tokens:
                    self.tokens = tokens


class Metrics:
    def __init__(self) -> None:
        self.requests: dict[tuple[str, str], int] = {}

    def count(self, upstream: str, outcome: str) -> None:
        key = (upstream, outcome)
        self.requests[key] = self.requests.get(key, 0) + 1

    def render(self, state: failover.FailoverState) -> str:
        lines = [
            "# HELP munin_llm_failover_route 1 for the upstream the route points at.",
            "# TYPE munin_llm_failover_route gauge",
        ]
        for name in (PRIMARY, SECONDARY):
            lines.append(f'munin_llm_failover_route{{upstream="{name}"}} {int(state.route == name)}')
        lines += [
            "# HELP munin_llm_failover_active 1 while users are served by the secondary.",
            "# TYPE munin_llm_failover_active gauge",
            f"munin_llm_failover_active {int(state.fallback_active())}",
            "# HELP munin_llm_failover_upstream_up Last probe or request outcome per upstream.",
            "# TYPE munin_llm_failover_upstream_up gauge",
        ]
        for name, health in ((PRIMARY, state.primary), (SECONDARY, state.secondary)):
            lines.append(f'munin_llm_failover_upstream_up{{upstream="{name}"}} {int(bool(health.last_ok))}')
        lines += [
            "# HELP munin_llm_failover_secondary_blocked 1 while the secondary cannot take requests.",
            "# TYPE munin_llm_failover_secondary_blocked gauge",
        ]
        reason = state.secondary_block_reason()
        for r in ("disabled", "spend_cap", "daily_budget", "unhealthy"):
            lines.append(f'munin_llm_failover_secondary_blocked{{reason="{r}"}} {int(reason == r)}')
        lines += [
            "# HELP munin_llm_failover_transitions_total Route changes, by the upstream moved to.",
            "# TYPE munin_llm_failover_transitions_total counter",
        ]
        for name, n in state.transitions.items():
            lines.append(f'munin_llm_failover_transitions_total{{to="{name}"}} {n}')
        lines += [
            "# HELP munin_llm_failover_secondary_tokens_today Tokens the secondary used this UTC day.",
            "# TYPE munin_llm_failover_secondary_tokens_today gauge",
            f"munin_llm_failover_secondary_tokens_today {state.budget.used}",
            "# HELP munin_llm_failover_requests_total Requests by upstream and outcome.",
            "# TYPE munin_llm_failover_requests_total counter",
        ]
        for (upstream, outcome), n in sorted(self.requests.items()):
            lines.append(
                f'munin_llm_failover_requests_total{{upstream="{upstream}",outcome="{outcome}"}} {n}'
            )
        return "\n".join(lines) + "\n"


class AttemptFailed(Exception):
    def __init__(self, reason: str, passive: bool, response: Optional[Response] = None):
        super().__init__(reason)
        self.reason = reason
        self.passive = passive  # counts toward the route's health streak
        self.response = response  # what to return if no other upstream is left


def create_app(
    state: Optional[failover.FailoverState] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    start_probe: bool = True,
) -> FastAPI:
    primary_url = os.environ.get("FAILOVER_PRIMARY_URL", "http://host.docker.internal:8000").rstrip("/")
    secondary_url = os.environ.get("FAILOVER_SECONDARY_URL", "http://llm-claude:8000").rstrip("/")
    primary_model = os.environ.get("FAILOVER_PRIMARY_MODEL", "qwen3.8-27b")
    secondary_model = os.environ.get("FAILOVER_SECONDARY_MODEL", "claude-haiku-5-5")
    thinking_mode = os.environ.get("LLM_THINKING_MODE", "enable_thinking")
    probe_interval = env_float("FAILOVER_PROBE_INTERVAL_S", 5.0)
    probe_timeout = env_float("FAILOVER_PROBE_TIMEOUT_S", 2.0)
    connect_timeout = env_float("FAILOVER_CONNECT_TIMEOUT_S", 3.0)
    spend_cap_cooldown = env_float("FAILOVER_SPEND_CAP_COOLDOWN_S", 3600.0)
    primary_key = (os.environ.get("LLM_API_KEY") or "").strip()

    if state is None:
        state = failover.FailoverState(
            fail_after=env_int("FAILOVER_FAIL_AFTER", 2),
            recover_after=env_int("FAILOVER_RECOVER_AFTER", 6),
            min_hold_s=env_float("FAILOVER_MIN_HOLD_S", 60.0),
            secondary_enabled=env_flag("FAILOVER_SECONDARY_ENABLED", True),
            budget=failover.DailyBudget(limit=env_int("FAILOVER_SECONDARY_DAILY_TOKENS", 20_000_000)),
        )
    metrics = Metrics()
    timeout = httpx.Timeout(connect=connect_timeout, read=900.0, write=60.0, pool=10.0)
    clients = {
        PRIMARY: httpx.AsyncClient(base_url=primary_url, timeout=timeout, transport=transport),
        SECONDARY: httpx.AsyncClient(base_url=secondary_url, timeout=timeout, transport=transport),
    }
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(probe_loop()) if start_probe else None
        try:
            yield
        finally:
            if task:
                task.cancel()
            for c in clients.values():
                await c.aclose()

    app = FastAPI(title="llm-failover", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.failover = state
    app.state.metrics = metrics

    def log_move(moved: Optional[str]) -> None:
        if moved == SECONDARY:
            logger.warning("failover route=secondary model=%s reason=%r",
                           secondary_model, state.switch_reason)
        elif moved == PRIMARY:
            logger.warning("failover route=primary model=%s reason=%r",
                           primary_model, state.switch_reason)

    def primary_headers() -> dict:
        return {"Authorization": f"Bearer {primary_key}"} if primary_key else {}

    async def probe_primary() -> None:
        try:
            r = await clients[PRIMARY].get("/v1/models", timeout=probe_timeout, headers=primary_headers())
            ids = [m.get("id") for m in (r.json().get("data") or [])] if r.status_code == 200 else []
            if r.status_code != 200:
                ok, err = False, f"probe HTTP {r.status_code}"
            elif primary_model not in ids:
                ok, err = False, f"probe: {primary_model} not served (got {ids})"
            else:
                ok, err = True, None
        except (httpx.HTTPError, ValueError) as exc:
            ok, err = False, f"probe {type(exc).__name__}"
        log_move(state.observe_primary(ok, err))

    async def probe_secondary() -> None:
        try:
            r = await clients[SECONDARY].get("/health", timeout=probe_timeout)
            ok, err = r.status_code == 200, (None if r.status_code == 200 else f"probe HTTP {r.status_code}")
        except httpx.HTTPError as exc:
            ok, err = False, f"probe {type(exc).__name__}"
        log_move(state.observe_secondary(ok, err))

    async def probe_loop() -> None:
        while True:
            try:
                await asyncio.gather(probe_primary(), probe_secondary())
                log_move(state.tick())
            except Exception:  # never let the loop die
                logger.exception("probe loop error")
            await asyncio.sleep(probe_interval)

    def munin_llm() -> dict:
        s = state.status()
        return {
            "fallback_active": s["fallback_active"],
            "route": s["route"],
            "since": s["since"] if s["fallback_active"] else None,
            "model": secondary_model if s["fallback_active"] else primary_model,
            "primary_model": primary_model,
            "secondary_block": s["secondary_block"],
        }

    def observe(upstream: str, ok: bool, err: Optional[str] = None) -> None:
        fn = state.observe_primary if upstream == PRIMARY else state.observe_secondary
        log_move(fn(ok, err))

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/status")
    async def status() -> dict:
        s = state.status()
        s.update({"primary_url": primary_url, "secondary_url": secondary_url,
                  "primary_model": primary_model, "secondary_model": secondary_model})
        return s

    @app.get("/metrics")
    async def prom() -> PlainTextResponse:
        return PlainTextResponse(metrics.render(state), media_type="text/plain; version=0.0.4")

    @app.get("/v1/models")
    async def models() -> Response:
        # The id is always the primary's name: retrieval's /api/models matches
        # on it, and API clients keep one stable model name across a failover.
        # `munin_llm` says who is really answering.
        if state.route == PRIMARY or not state.secondary_usable():
            try:
                r = await clients[PRIMARY].get("/v1/models", timeout=probe_timeout, headers=primary_headers())
                if r.status_code == 200:
                    body = r.json()
                    body["munin_llm"] = munin_llm()
                    return JSONResponse(body)
            except (httpx.HTTPError, ValueError):
                pass
            if not state.secondary_usable():
                return JSONResponse({"error": {"message": "no LLM upstream available",
                                               "type": "upstream_unavailable"},
                                     "munin_llm": munin_llm()}, status_code=503)
        return JSONResponse({
            "object": "list",
            "data": [{"id": primary_model, "object": "model", "created": 0,
                      "owned_by": "munin", "root": secondary_model}],
            "munin_llm": munin_llm(),
        })

    async def attempt(upstream: str, method: str, path: str, body: bytes,
                      parsed: Optional[dict], headers: dict) -> Response:
        """One upstream try. Raises AttemptFailed only while nothing has been
        sent to the caller, so the next upstream can still answer."""
        content = body
        send_headers = {"Content-Type": headers.get("content-type", "application/json")}
        if upstream == PRIMARY:
            send_headers.update(primary_headers())
        elif parsed is not None:
            content = json.dumps(claude_safe(parsed, secondary_model, thinking_mode)).encode()
        client = clients[upstream]
        req = client.build_request(method, path, content=content or None, headers=send_headers)
        try:
            resp = await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise AttemptFailed(f"{type(exc).__name__}", passive=True)

        if upstream == SECONDARY and resp.status_code == 402:
            await resp.aread()
            await resp.aclose()
            state.block_secondary(spend_cap_cooldown, "spend_cap")
            logger.error("secondary spend cap reached (402); secondary off for %ds", int(spend_cap_cooldown))
            metrics.count(upstream, "spend_cap")
            raise AttemptFailed("spend cap", passive=False, response=Response(
                content=resp.content, status_code=402,
                media_type=resp.headers.get("content-type", "application/json")))

        if resp.status_code in RETRY_STATUSES:
            data = await resp.aread()
            await resp.aclose()
            raise AttemptFailed(f"HTTP {resp.status_code}", passive=resp.status_code in PASSIVE_FAIL,
                                response=Response(content=data, status_code=resp.status_code,
                                                  media_type=resp.headers.get("content-type")))

        # Read the first chunk before answering: a 200 whose body never comes
        # is still a failure the other upstream can take over.
        iterator = resp.aiter_bytes()
        try:
            first = await iterator.__anext__()
        except StopAsyncIteration:
            first = b""
        except httpx.HTTPError as exc:
            await resp.aclose()
            raise AttemptFailed(f"{type(exc).__name__} before first byte", passive=True)

        out_headers = {"X-Munin-LLM-Upstream": upstream}
        for h in ("retry-after",):
            if h in resp.headers:
                out_headers[h] = resp.headers[h]
        media = resp.headers.get("content-type", "application/json")
        ok_status = resp.status_code < 500

        if "text/event-stream" not in media:
            # Not streamed: buffer the rest, so a failure part way is still
            # retryable. Bodies are small (one completion).
            chunks = [first]
            try:
                async for chunk in iterator:
                    chunks.append(chunk)
            except httpx.HTTPError as exc:
                raise AttemptFailed(f"{type(exc).__name__} mid-body", passive=True)
            finally:
                await resp.aclose()
            data = b"".join(chunks)
            if upstream == SECONDARY:
                try:
                    state.budget.add(usage_tokens(json.loads(data)))
                except ValueError:
                    pass
            observe(upstream, ok_status)
            metrics.count(upstream, "ok" if ok_status else f"http_{resp.status_code}")
            return Response(content=data, status_code=resp.status_code, media_type=media,
                            headers=out_headers)

        observe(upstream, True)
        sse = SSEUsage()

        async def relay() -> AsyncIterator[bytes]:
            outcome = "ok"
            try:
                sse.feed(first)
                yield first
                async for chunk in iterator:
                    sse.feed(chunk)
                    yield chunk
            except httpx.HTTPError as exc:
                # Committed: the caller already has bytes. Abort the
                # connection; retrieval treats it as a dropped stream.
                outcome = "dropped"
                observe(upstream, False, f"{type(exc).__name__} mid-stream")
                raise
            finally:
                await resp.aclose()
                if upstream == SECONDARY:
                    state.budget.add(sse.tokens)
                metrics.count(upstream, outcome)

        return StreamingResponse(relay(), status_code=resp.status_code, media_type=media,
                                 headers=out_headers)

    @app.api_route("/v1/{path:path}", methods=["GET", "POST"])
    async def proxy(path: str, request: Request) -> Response:
        body = await request.body()
        parsed: Optional[dict] = None
        if body:
            try:
                candidate = json.loads(body)
                parsed = candidate if isinstance(candidate, dict) else None
            except ValueError:
                parsed = None
        url = f"/v1/{path}" + (f"?{request.url.query}" if request.url.query else "")
        plan = state.plan()
        last: Optional[AttemptFailed] = None
        for i, upstream in enumerate(plan):
            try:
                response = await attempt(upstream, request.method, url, body, parsed,
                                         dict(request.headers))
                if i > 0:
                    metrics.count(upstream, "fallback")
                    logger.warning("request fallback %s -> %s (%s) path=%s",
                                   plan[0], upstream, last.reason if last else "?", url)
                return response
            except AttemptFailed as exc:
                last = exc
                metrics.count(upstream, "failed")
                if exc.passive:
                    observe(upstream, False, exc.reason)
        assert last is not None
        if last.response is not None:
            return last.response
        return JSONResponse({"error": {"message": f"LLM upstream unavailable: {last.reason}",
                                       "type": "upstream_unavailable", "code": "upstream_unavailable"}},
                            status_code=503)

    return app


def app() -> FastAPI:
    return create_app()
