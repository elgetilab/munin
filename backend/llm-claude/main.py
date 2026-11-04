"""OpenAI-compatible front for the Claude API, used when no local vLLM exists.

Retrieval sets LLM_BASE_URL=http://llm-claude:8000 and keeps speaking
chat-completions. The API key exists only in this container.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Optional

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

import translate

logger = logging.getLogger("llm-claude")
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

# Above this prompt size Haiku 5.5 bills on its dearer rate card.
EXPENSIVE_PROMPT_TOKENS = 100_000
# Thinking stays off, and the API refuses that combination above "high".
ALLOWED_EFFORT = ("low", "medium", "high")
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


def log_usage(usage: dict, stream: bool) -> None:
    prompt = usage["prompt_tokens"]
    logger.info(
        "usage prompt=%d cached=%d output=%d stream=%s",
        prompt, usage["prompt_tokens_details"]["cached_tokens"],
        usage["completion_tokens"], stream,
    )
    if prompt > EXPENSIVE_PROMPT_TOKENS:
        logger.warning("prompt of %d tokens billed on the higher rate card", prompt)


def create_app(client: Optional[anthropic.AsyncAnthropic] = None) -> FastAPI:
    model = os.environ.get("CLAUDE_MODEL", "claude-haiku-5-5")
    effort = os.environ.get("CLAUDE_EFFORT", "medium")
    max_output = int(os.environ.get("CLAUDE_MAX_OUTPUT_TOKENS", "16384"))

    if model not in translate.MODEL_PROFILES:
        raise SystemExit(f"CLAUDE_MODEL={model!r}: supported: {sorted(translate.MODEL_PROFILES)}")
    if effort not in ALLOWED_EFFORT:
        raise SystemExit(f"CLAUDE_EFFORT={effort!r}: expected one of {ALLOWED_EFFORT}")
    if client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise SystemExit("llm-claude: set ANTHROPIC_API_KEY")
        # One SDK retry. vllm_client already retries up to five times, and its
        # attempts would multiply with the SDK's default of two.
        client = anthropic.AsyncAnthropic(api_key=key, max_retries=1)

    app = FastAPI(title="llm-claude")

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
            params = translate.build_request(
                body, model=model, effort=effort, max_output_tokens=max_output
            )
        except (ValueError, TypeError, AttributeError) as exc:
            return _error(400, f"Malformed request: {exc}", "invalid_request_error")

        if not body.get("stream"):
            try:
                # Streams upstream so that a long generation cannot hit an HTTP
                # timeout, then answers in one piece.
                async with client.messages.stream(**params) as stream:
                    message = await stream.get_final_message()
            except anthropic.APIError as exc:
                return error_response(exc)
            completion = translate.completion_from_message(message, model)
            log_usage(completion["usage"], stream=False)
            return completion

        include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
        translator = translate.StreamTranslator(model, include_usage)
        try:
            upstream = await client.messages.create(stream=True, **params)
            # Wait for the first event, so an overload that arrives right after
            # the 200 is still a status retrieval can retry on.
            first = await upstream.__anext__()
        except anthropic.APIError as exc:
            return error_response(exc)

        async def events() -> AsyncIterator[str]:
            try:
                for chunk in translator.feed(first):
                    yield f"data: {json.dumps(chunk)}\n\n"
                async for event in upstream:
                    for chunk in translator.feed(event):
                        yield f"data: {json.dumps(chunk)}\n\n"
                if not translator.finished:
                    # Aborts the connection, which retrieval handles as a dropped stream.
                    raise RuntimeError("Claude stream ended before message_stop")
                yield "data: [DONE]\n\n"
            finally:
                await upstream.close()
                log_usage(translator.usage(), stream=True)

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def app() -> FastAPI:
    return create_app()
