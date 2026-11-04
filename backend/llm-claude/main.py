"""OpenAI-compatible front for the Claude API, used when no local vLLM exists.

Retrieval sets LLM_BASE_URL=http://llm-claude:8000 and keeps speaking
chat-completions. The API key exists only in this container.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import OrderedDict
from typing import Any, AsyncIterator, Optional

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

import translate

logger = logging.getLogger("llm-claude")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

# Above this prompt size Haiku 5.5 bills on its dearer rate card.
EXPENSIVE_PROMPT_TOKENS = 100_000
# Sub-tasks turn thinking off, and the API refuses that above "high".
ALLOWED_EFFORT = ("low", "medium", "high")
MAX_FALLBACK_CONVERSATIONS = 2000
# An error event inside a stream arrives on an HTTP 200, and the SDK raises it
# with that status. These restore the status retrieval's retry logic needs.
STREAM_ERROR_STATUS = {"overloaded_error": 529, "rate_limit_error": 429, "api_error": 500}


def _error(status: int, message: str, kind: str, headers: Optional[dict] = None) -> JSONResponse:
    payload = {"error": {"message": message, "type": kind, "code": kind}}
    return JSONResponse(payload, status_code=status, headers=headers)


def error_response(exc: anthropic.APIError) -> JSONResponse:
    """Keep the status retrieval's retry logic keys on (429, 5xx retry; other
    4xx fail at once) and say what happened in words it can show or match."""
    if isinstance(exc, anthropic.APITimeoutError):
        return _error(504, "Claude API timed out", "upstream_timeout")
    if isinstance(exc, anthropic.APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error") or {}
        message = error.get("message") or exc.message
        status = exc.status_code
        if status < 400:
            status = STREAM_ERROR_STATUS.get(error.get("type"), 502)
        details = error.get("details") or {}
        headers = {}
        if details.get("error_code") == "enforced_spend_limit_reached":
            # A 429 that no retry can fix: access returns next month.
            status = 402
        elif exc.response.headers.get("retry-after"):
            headers["retry-after"] = exc.response.headers["retry-after"]
        if status == 400 and "prompt is too long" in message.lower():
            # retrieval's refit logic looks for vLLM's wording.
            message = f"maximum context length exceeded: {message}"
        return _error(status, message, "upstream_error", headers)
    return _error(502, "Could not reach the Claude API", "upstream_unavailable")


def log_usage(usage: dict, model: str, stream: bool) -> None:
    prompt = usage["prompt_tokens"]
    logger.info(
        "usage model=%s prompt=%d cached=%d output=%d stream=%s",
        model, prompt, usage["prompt_tokens_details"]["cached_tokens"],
        usage["completion_tokens"], stream,
    )
    if prompt > EXPENSIVE_PROMPT_TOKENS:
        logger.warning("prompt of %d tokens billed on the higher rate card", prompt)


def is_thinking_rejection(exc: Exception) -> bool:
    return isinstance(exc, anthropic.BadRequestError) and "signature" in exc.message.lower()


def create_app(client: Optional[anthropic.AsyncAnthropic] = None) -> FastAPI:
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    fallback = os.environ.get("CLAUDE_FALLBACK_MODEL", "claude-sonnet-5").strip()
    effort = os.environ.get("CLAUDE_EFFORT", "medium")
    thinking = os.environ.get("CLAUDE_THINKING", "on")
    max_output = int(os.environ.get("CLAUDE_MAX_OUTPUT_TOKENS", "16384"))

    for name in filter(None, (model, fallback)):
        if name not in translate.MODEL_PROFILES:
            raise SystemExit(f"model {name!r}: supported: {sorted(translate.MODEL_PROFILES)}")
    if fallback == model:
        fallback = ""
    if effort not in ALLOWED_EFFORT:
        raise SystemExit(f"CLAUDE_EFFORT={effort!r}: expected one of {ALLOWED_EFFORT}")
    if thinking not in ("on", "off"):
        raise SystemExit(f"CLAUDE_THINKING={thinking!r}: expected on or off")
    if client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise SystemExit("llm-claude: set ANTHROPIC_API_KEY")
        # One SDK retry. vllm_client already retries up to five times, and its
        # attempts would multiply with the SDK's default of two.
        client = anthropic.AsyncAnthropic(api_key=key, max_retries=1)

    app = FastAPI(title="llm-claude")
    store = translate.ThinkingStore()
    # Anthropic's advice is to stay on the fallback for the rest of a conversation.
    on_fallback: OrderedDict[str, None] = OrderedDict()

    def conversation_key(body: dict) -> str:
        first = next((m for m in body.get("messages") or [] if m.get("role") == "user"), None)
        return hashlib.sha256(json.dumps(first, sort_keys=True).encode()).hexdigest()

    def move_to_fallback(key: str) -> None:
        on_fallback[key] = None
        while len(on_fallback) > MAX_FALLBACK_CONVERSATIONS:
            on_fallback.popitem(last=False)

    def prepare(body: dict, name: str) -> tuple[dict, str]:
        params = translate.build_request(
            body, model=name, effort=effort, max_output_tokens=max_output,
            thinking=thinking == "on",
        )
        return params, translate.attach_thinking(params, store)

    def is_refused(stop_reason: Optional[str], details: Any, name: str) -> bool:
        if stop_reason != "refusal":
            return False
        logger.warning("refusal model=%s category=%s", name, getattr(details, "category", None))
        return True

    async def complete(params: dict) -> Any:
        async with client.messages.stream(**params) as stream:
            return await stream.get_final_message()

    async def open_stream(params: dict) -> tuple[Any, Any]:
        upstream = await client.messages.create(stream=True, **params)
        # Wait for the first event, so an overload that arrives right after the
        # 200 is still a status retrieval can retry on.
        return upstream, await upstream.__anext__()

    async def with_thinking_recovery(call: Any, params: dict) -> Any:
        try:
            return await call(params)
        except anthropic.BadRequestError as exc:
            if not is_thinking_rejection(exc):
                raise
            logger.warning("thinking block rejected; retrying without replayed blocks")
            translate.strip_thinking(params)
            return await call(params)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "model": model}

    @app.get("/v1/models")
    async def models() -> dict:
        # Answered locally: retrieval polls this for its status page, and the
        # UI shows the "sleeping" screen unless it gets a 200.
        return {"object": "list", "data": [{"id": model, "object": "model", "owned_by": "anthropic"}]}

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Any:
        try:
            body = await request.json()
            key = conversation_key(body)
            name = fallback if key in on_fallback else model
            params, prefix = prepare(body, name)
        except (ValueError, TypeError, AttributeError, KeyError) as exc:
            return _error(400, f"Malformed request: {exc}", "invalid_request_error")

        if not body.get("stream"):
            try:
                message = await with_thinking_recovery(complete, params)
                if is_refused(message.stop_reason, message.stop_details, name) and fallback and name != fallback:
                    move_to_fallback(key)
                    name = fallback
                    params, prefix = prepare(body, name)
                    message = await with_thinking_recovery(complete, params)
                    is_refused(message.stop_reason, message.stop_details, name)
            except anthropic.APIError as exc:
                return error_response(exc)
            translate.remember_message(message, prefix, store)
            completion = translate.completion_from_message(message, name)
            log_usage(completion["usage"], name, stream=False)
            return completion

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        try:
            upstream, first = await with_thinking_recovery(open_stream, params)
        except anthropic.APIError as exc:
            return error_response(exc)

        async def events() -> AsyncIterator[str]:
            nonlocal upstream, first, name, params, prefix
            while True:
                translator = translate.StreamTranslator(name, include_usage)
                retry = False
                try:
                    for chunk in translator.feed(first):
                        yield f"data: {json.dumps(chunk)}\n\n"
                    async for event in upstream:
                        chunks = translator.feed(event)
                        if (event.type == "message_delta"
                                and is_refused(translator.stop_reason, translator.stop_details, name)
                                and fallback and name != fallback and not translator.sent_output):
                            retry = True
                            break
                        for chunk in chunks:
                            yield f"data: {json.dumps(chunk)}\n\n"
                    if retry:
                        continue
                    if not translator.finished:
                        # Aborts the connection, which retrieval handles as a dropped stream.
                        raise RuntimeError("Claude stream ended before message_stop")
                    if translator.first_tool_id:
                        store.put(translator.first_tool_id, prefix, translator.thinking_blocks)
                    yield "data: [DONE]\n\n"
                    return
                finally:
                    await upstream.close()
                    log_usage(translator.usage(), name, stream=True)
                    if retry:
                        move_to_fallback(key)
                        name = fallback
                        params, prefix = prepare(body, name)
                        upstream, first = await with_thinking_recovery(open_stream, params)

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def app() -> FastAPI:
    return create_app()
