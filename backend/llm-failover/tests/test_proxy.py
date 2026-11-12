"""The proxy against in-process fake upstreams (httpx.MockTransport)."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main
from failover import PRIMARY, SECONDARY, DailyBudget, FailoverState


class Upstreams:
    """Programmable fakes. host 'primary' is vLLM, host 'secondary' llm-claude."""

    def __init__(self):
        self.primary = "ok"  # ok | down | 400 | 404 | 503 | stream
        self.secondary = "ok"  # ok | down | 402 | stream
        self.seen: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        body = json.loads(request.content) if request.content else {}
        self.seen.append((host, request.url.path, body))
        mode = self.primary if host == "primary" else self.secondary
        if mode == "down":
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "qwen3.8-27b"}]})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if mode in ("400", "404", "503"):
            return httpx.Response(int(mode), json={"error": {"message": mode}})
        if mode == "402":
            return httpx.Response(402, json={"error": {"message": "spend limit"}})
        if body.get("stream"):
            sse = (
                f'data: {{"model": "{host}", "choices": [{{"delta": {{"content": "hi"}}}}]}}\n\n'
                'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}\n\n'
                "data: [DONE]\n\n"
            )
            return httpx.Response(200, content=sse.encode(), headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"model": host, "choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}})


class _Body(httpx.AsyncByteStream):
    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        for i in range(0, len(self.data), 64):
            yield self.data[i:i + 64]


class Transport(httpx.AsyncBaseTransport):
    """Like httpx.MockTransport, but hands back an unread stream so the proxy
    can read it the way it reads a real upstream."""

    def __init__(self, handler):
        self.handler = handler

    async def handle_async_request(self, request):
        await request.aread()
        resp = self.handler(request)
        return httpx.Response(resp.status_code, headers=resp.headers, stream=_Body(resp.content))


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("FAILOVER_PRIMARY_URL", "http://primary")
    monkeypatch.setenv("FAILOVER_SECONDARY_URL", "http://secondary")
    monkeypatch.setenv("FAILOVER_PRIMARY_MODEL", "qwen3.8-27b")
    monkeypatch.setenv("FAILOVER_SECONDARY_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("LLM_THINKING_MODE", "enable_thinking")


def build(env, state=None):
    ups = Upstreams()
    state = state or FailoverState(fail_after=2, recover_after=2, min_hold_s=0, budget=DailyBudget(limit=0))
    state.observe_secondary(True)
    app = main.create_app(state=state, transport=Transport(ups), start_probe=False)
    return TestClient(app), ups, state


CHAT = {"model": "qwen3.8-27b", "messages": [{"role": "user", "content": "x"}],
        "top_k": 20, "min_p": 0.0, "temperature": 0.6,
        "chat_template_kwargs": {"enable_thinking": False}}


def test_primary_serves_when_healthy(env):
    c, ups, state = build(env)
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 200
    assert r.headers["x-munin-llm-upstream"] == PRIMARY
    assert [h for h, _, _ in ups.seen] == ["primary"]
    assert ups.seen[0][2]["top_k"] == 20, "primary gets the body untouched"


def test_primary_down_falls_back_per_request_with_claude_safe_body(env):
    c, ups, state = build(env)
    ups.primary = "down"
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 200
    assert r.headers["x-munin-llm-upstream"] == SECONDARY
    host, _, body = ups.seen[-1]
    assert host == "secondary"
    assert body["model"] == "claude-haiku-5-5"
    assert "top_k" not in body and "min_p" not in body
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert state.route == PRIMARY, "one failure is not a failover"
    c.post("/v1/chat/completions", json=CHAT)
    assert state.route == SECONDARY
    # Now the secondary is asked first, the dead primary not at all.
    ups.seen.clear()
    c.post("/v1/chat/completions", json=CHAT)
    assert [h for h, _, _ in ups.seen] == ["secondary"]
    assert state.budget.used == 21


def test_client_error_is_not_retried(env):
    c, ups, state = build(env)
    ups.primary = "400"
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 400
    assert [h for h, _, _ in ups.seen] == ["primary"]
    assert state.primary.fail_streak == 0


def test_unknown_model_404_retries_but_does_not_count_toward_health(env):
    c, ups, state = build(env)
    ups.primary = "404"
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.headers["x-munin-llm-upstream"] == SECONDARY
    assert state.primary.fail_streak == 0


def test_503_counts_toward_health(env):
    c, ups, state = build(env)
    ups.primary = "503"
    c.post("/v1/chat/completions", json=CHAT)
    c.post("/v1/chat/completions", json=CHAT)
    assert state.route == SECONDARY


def test_stream_is_relayed_and_usage_counted(env):
    c, ups, state = build(env)
    ups.primary = "down"
    with c.stream("POST", "/v1/chat/completions", json={**CHAT, "stream": True}) as r:
        text = b"".join(r.iter_bytes()).decode()
    assert r.headers["x-munin-llm-upstream"] == SECONDARY
    assert '"content": "hi"' in text and text.rstrip().endswith("[DONE]")
    assert state.budget.used == 10


def test_both_down_returns_503(env):
    c, ups, state = build(env)
    ups.primary = ups.secondary = "down"
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "upstream_unavailable"


def test_spend_cap_402_blocks_secondary(env):
    c, ups, state = build(env)
    ups.primary = "down"
    ups.secondary = "402"
    r = c.post("/v1/chat/completions", json=CHAT)
    assert r.status_code == 402
    assert state.secondary_block_reason() == "spend_cap"
    ups.seen.clear()
    c.post("/v1/chat/completions", json=CHAT)
    assert [h for h, _, _ in ups.seen] == ["primary"], "secondary not asked while capped"


def test_models_reports_fallback(env):
    c, ups, state = build(env)
    r = c.get("/v1/models").json()
    assert r["data"][0]["id"] == "qwen3.8-27b"
    assert r["munin_llm"]["fallback_active"] is False
    ups.primary = "down"
    state.observe_primary(False)
    state.observe_primary(False)
    r = c.get("/v1/models").json()
    assert r["data"][0]["id"] == "qwen3.8-27b", "id stays stable for retrieval and API clients"
    assert r["munin_llm"]["fallback_active"] is True
    assert r["munin_llm"]["model"] == "claude-haiku-5-5"
    assert r["munin_llm"]["since"]


def test_models_503_when_nothing_available(env):
    c, ups, state = build(env)
    ups.primary = "down"
    state.block_secondary(60, "spend_cap")
    assert c.get("/v1/models").status_code == 503


def test_metrics_and_status(env):
    c, ups, state = build(env)
    ups.primary = "down"
    c.post("/v1/chat/completions", json=CHAT)
    m = c.get("/metrics").text
    assert 'munin_llm_failover_requests_total{upstream="secondary",outcome="fallback"} 1' in m
    assert "munin_llm_failover_active 0" in m
    s = c.get("/status").json()
    assert s["primary"]["fail_streak"] == 1


@pytest.mark.parametrize("kwargs,mode,expected", [
    ({"enable_thinking": False}, "enable_thinking", {"enable_thinking": False}),
    ({"reasoning_effort": "medium"}, "enable_thinking", None),
    ({"reasoning_effort": "low"}, "effort_low", {"enable_thinking": False}),
    ({"reasoning_effort": "low"}, "enable_thinking", None),
    (None, "enable_thinking", None),
])
def test_claude_safe_thinking_markers(kwargs, mode, expected):
    body = {"model": "x", "messages": [], "top_k": 1, "seed": 3}
    if kwargs is not None:
        body["chat_template_kwargs"] = kwargs
    out = main.claude_safe(body, "claude-haiku-5-5", mode)
    assert out.get("chat_template_kwargs") == expected
    assert out["model"] == "claude-haiku-5-5"
    assert "top_k" not in out and "seed" not in out
    assert body["top_k"] == 1, "input not mutated"


def test_sse_usage_across_chunk_boundaries():
    u = main.SSEUsage()
    line = b'data: {"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 6}}\n\n'
    for i in range(0, len(line), 7):
        u.feed(line[i:i + 7])
    assert u.tokens == 10
