"""The app against the real anthropic SDK, with only the HTTP transport faked,
so these exercise the SDK's own request building and SSE parsing."""

import json

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

import main

USER = {"role": "user", "content": "hi"}
USAGE = {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 100,
         "cache_creation_input_tokens": 20}


def sse(*events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def message_start():
    return {"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
        "content": [], "stop_reason": None, "stop_sequence": None, "usage": USAGE}}


def text_events(text, stop_reason="end_turn", **delta):
    return [
        message_start(),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None, **delta},
         "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]


def tool_events(fragments):
    block = {"type": "tool_use", "id": "toolu_1", "name": "search", "input": {}}
    deltas = [{"type": "content_block_delta", "index": 0,
               "delta": {"type": "input_json_delta", "partial_json": f}} for f in fragments]
    return [
        message_start(),
        {"type": "content_block_start", "index": 0, "content_block": block},
        *deltas,
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use", "stop_sequence": None},
         "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    ]


class Upstream:
    """Answers with each response in turn, repeating the last."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]

    @property
    def sent(self):
        return json.loads(self.requests[-1].content)


def client_for(*responses):
    upstream = Upstream(*responses)
    sdk = anthropic.AsyncAnthropic(
        api_key="test-key", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(upstream)),
    )
    return TestClient(main.create_app(sdk), raise_server_exceptions=False), upstream


def ok_stream(events):
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=sse(*events))


def api_error(status, kind, message, headers=None, **extra):
    body = {"type": "error", "error": {"type": kind, "message": message, **extra}}
    return httpx2.Response(status, json=body, headers=headers)


def chunks(response):
    lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    return [json.loads(line) for line in lines[:-1]]


def accumulate(parsed):
    """The fields chat_service._StreamAccumulator reads from each chunk."""
    state = {"content": "", "calls": {}, "finish": None, "usage": None}
    for chunk in parsed:
        state["usage"] = chunk.get("usage") or state["usage"]
        for choice in chunk["choices"][:1]:
            state["content"] += choice["delta"].get("content") or ""
            for call in choice["delta"].get("tool_calls") or []:
                slot = state["calls"].setdefault(call["index"], {"arguments": ""})
                slot["id"] = call.get("id") or slot.get("id")
                fn = call.get("function") or {}
                slot["name"] = fn.get("name") or slot.get("name")
                slot["arguments"] += fn.get("arguments") or ""
            state["finish"] = choice["finish_reason"] or state["finish"]
    return state


def refusal_stream(category="bio"):
    return ok_stream(text_events("", stop_reason="refusal", stop_details={
        "type": "refusal", "category": category, "explanation": None}))


def thinking_tool_events():
    block = {"type": "thinking", "thinking": "", "signature": ""}
    shifted = [{**e, "index": 1} if "index" in e else e for e in tool_events(['{"q": "x"}'])]
    return [
        shifted[0],
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "let me"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
        {"type": "content_block_stop", "index": 0},
        *shifted[1:],
    ]


def test_thinking_reaches_the_client_and_returns_with_the_tool_result():
    client, upstream = client_for(ok_stream(thinking_tool_events()), ok_stream(text_events("done")))
    tool = {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}
    base = {"messages": [USER], "stream": True, "tools": [tool]}
    parsed = chunks(client.post("/v1/chat/completions", json=base))
    assert "".join(c["choices"][0]["delta"].get("reasoning") or "" for c in parsed) == "let me"

    call = {"id": "toolu_1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}
    chunks(client.post("/v1/chat/completions", json={**base, "messages": [
        USER,
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "result"},
    ]}))
    assert upstream.sent["messages"][1]["content"][0] == {
        "type": "thinking", "thinking": "let me", "signature": "SIG"}


def test_rejected_thinking_block_is_stripped_and_retried():
    client, upstream = client_for(
        ok_stream(thinking_tool_events()),
        api_error(400, "invalid_request_error", "messages.1.content.0: Invalid `signature` in `thinking` block."),
        ok_stream(text_events("done")),
    )
    tool = {"type": "function", "function": {"name": "search", "parameters": {"type": "object"}}}
    base = {"messages": [USER], "stream": True, "tools": [tool]}
    chunks(client.post("/v1/chat/completions", json=base))
    call = {"id": "toolu_1", "type": "function", "function": {"name": "search", "arguments": '{"q": "x"}'}}
    state = accumulate(chunks(client.post("/v1/chat/completions", json={**base, "messages": [
        USER,
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "toolu_1", "content": "result"},
    ]})))
    assert state["content"] == "done"
    assert all(b["type"] != "thinking" for b in upstream.sent["messages"][1]["content"])


def test_other_400s_are_not_retried():
    client, upstream = client_for(api_error(400, "invalid_request_error", "bad tool schema"))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": True})
    assert response.status_code == 400
    assert len(upstream.requests) == 1


@pytest.mark.parametrize("stream", [True, False])
def test_refusal_is_answered_by_the_fallback_model(stream):
    client, upstream = client_for(refusal_stream(), ok_stream(text_events("answer")))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": stream})
    if stream:
        state = accumulate(chunks(response))
        content, finish = state["content"], state["finish"]
    else:
        choice = response.json()["choices"][0]
        content, finish = choice["message"]["content"], choice["finish_reason"]
    assert content == "answer" and finish == "stop"
    assert [json.loads(r.content)["model"] for r in upstream.requests] == ["claude-haiku-5-5", "claude-sonnet-5"]


def test_conversation_stays_on_the_fallback_and_others_do_not():
    client, upstream = client_for(refusal_stream(), ok_stream(text_events("answer")))
    client.post("/v1/chat/completions", json={"messages": [USER]})
    client.post("/v1/chat/completions", json={"messages": [
        USER, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "more"}]})
    assert upstream.sent["model"] == "claude-sonnet-5"
    client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "new chat"}]})
    assert upstream.sent["model"] == "claude-haiku-5-5"


def test_refusal_after_visible_output_is_not_retried():
    client, upstream = client_for(ok_stream(text_events("partial", stop_reason="refusal", stop_details={
        "type": "refusal", "category": "bio", "explanation": None})))
    state = accumulate(chunks(client.post(
        "/v1/chat/completions", json={"messages": [USER], "stream": True})))
    assert len(upstream.requests) == 1
    assert state["content"].startswith("partial") and "declined" in state["content"]


def test_fallback_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("CLAUDE_FALLBACK_MODEL", "")
    client, upstream = client_for(refusal_stream())
    body = client.post("/v1/chat/completions", json={"messages": [USER]}).json()
    assert len(upstream.requests) == 1
    assert "declined" in body["choices"][0]["message"]["content"]


def test_models_answers_without_calling_the_api():
    client, upstream = client_for(ok_stream([]))
    response = client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "claude-haiku-5-5"
    assert upstream.requests == []


def test_stream_text_and_usage():
    client, upstream = client_for(ok_stream(text_events("hello")))
    response = client.post("/v1/chat/completions", json={
        "messages": [USER], "stream": True, "stream_options": {"include_usage": True},
        "temperature": 0.1, "top_p": 0.9,
    })
    state = accumulate(chunks(response))
    assert state["content"] == "hello"
    assert state["finish"] == "stop"
    assert state["usage"]["prompt_tokens"] == 130
    assert state["usage"]["completion_tokens"] == 7
    assert "temperature" not in upstream.sent and "top_p" not in upstream.sent
    assert upstream.sent["model"] == "claude-haiku-5-5"
    assert upstream.sent["output_config"] == {"effort": "medium"}


def test_stream_omits_usage_chunk_unless_asked():
    client, _ = client_for(ok_stream(text_events("hello")))
    parsed = chunks(client.post("/v1/chat/completions", json={"messages": [USER], "stream": True}))
    assert all(chunk["choices"] for chunk in parsed)


def test_stream_tool_call():
    client, _ = client_for(ok_stream(tool_events(['{"q":', ' "x"}'])))
    state = accumulate(chunks(client.post(
        "/v1/chat/completions", json={"messages": [USER], "stream": True})))
    assert state["finish"] == "tool_calls"
    assert state["calls"][0] == {"id": "toolu_1", "name": "search", "arguments": '{"q": "x"}'}


def test_stream_tool_call_without_arguments_is_valid_json():
    client, _ = client_for(ok_stream(tool_events([])))
    state = accumulate(chunks(client.post(
        "/v1/chat/completions", json={"messages": [USER], "stream": True})))
    assert state["calls"][0]["arguments"] == "{}"


def test_non_stream_returns_one_completion():
    client, upstream = client_for(ok_stream(text_events("hello")))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "max_tokens": 40})
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["prompt_tokens"] == 130
    assert upstream.sent["max_tokens"] == 40


def test_non_stream_tool_call():
    client, _ = client_for(ok_stream(tool_events(['{"q": "x"}'])))
    message = client.post("/v1/chat/completions", json={"messages": [USER]}).json()["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "search"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"q": "x"}


@pytest.mark.parametrize("stream", [True, False])
def test_refusal_is_visible_to_the_user_when_the_fallback_also_declines(stream):
    client, _ = client_for(refusal_stream())
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": stream})
    if stream:
        state = accumulate(chunks(response))
        content, finish = state["content"], state["finish"]
    else:
        choice = response.json()["choices"][0]
        content, finish = choice["message"]["content"], choice["finish_reason"]
    assert "declined" in content and "bio" in content
    assert finish == "content_filter"


@pytest.mark.parametrize("stream", [True, False])
def test_rate_limit_keeps_status_and_retry_after(stream):
    client, _ = client_for(api_error(429, "rate_limit_error", "slow down", {"retry-after": "12"}))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": stream})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "12"


def test_spend_cap_is_not_retryable():
    client, _ = client_for(api_error(
        429, "rate_limit_error", "You have reached your API usage limits",
        details={"error_code": "enforced_spend_limit_reached"}))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": True})
    assert response.status_code == 402
    assert "usage limits" in response.json()["error"]["message"]


def test_prompt_too_long_uses_the_wording_retrieval_matches():
    client, _ = client_for(api_error(
        400, "invalid_request_error", "prompt is too long: 1200000 tokens > 1000000 maximum"))
    response = client.post("/v1/chat/completions", json={"messages": [USER]})
    assert response.status_code == 400
    assert "maximum context length" in response.json()["error"]["message"].lower()


@pytest.mark.parametrize("stream", [True, False])
def test_error_event_before_any_output_is_retryable(stream):
    error = {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
    client, _ = client_for(ok_stream([error]))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": stream})
    assert response.status_code == 529
    assert response.json()["error"]["message"] == "Overloaded"


def test_truncated_stream_never_sends_done():
    events = text_events("partial")[:3]
    client, _ = client_for(ok_stream(events))
    response = client.post("/v1/chat/completions", json={"messages": [USER], "stream": True})
    assert "[DONE]" not in response.text


def test_malformed_body_is_a_400():
    client, _ = client_for(ok_stream([]))
    response = client.post("/v1/chat/completions", content=b"not json")
    assert response.status_code == 400


def test_unsupported_model_fails_at_startup(monkeypatch):
    monkeypatch.setenv("CLAUDE_MODEL", "claude-opus-5-5")
    with pytest.raises(SystemExit):
        main.create_app()


def test_missing_key_fails_at_startup(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        main.create_app()
