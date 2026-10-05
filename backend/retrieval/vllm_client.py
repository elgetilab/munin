"""
Centralised vLLM HTTP client with transport-level retry.

All vLLM calls in the retrieval service go through here. The retry policy
gives users a recoverable response when vLLM hiccups (slot eviction during
contention, a tunnel blip, a model-load gap on the SLURM cron boundary,
transient 5xx) rather than aborting the turn after one failure.

Tunables (hardcoded on purpose, fewer env knobs to forget):

    MAX_ATTEMPTS_FOREGROUND   user-facing calls (default 5)
    MAX_ATTEMPTS_BACKGROUND   title/summary calls (default 2; bail fast)
    BASE_DELAY_S              first backoff slot (default 0.5)
    MAX_RETRY_AFTER_S         clamp on honoured ``Retry-After`` headers
    RETRYABLE_STATUS          status codes that trigger a retry

Worst-case wall time for the foreground path with defaults is
    sum(BASE_DELAY_S * 2**i for i in range(4)) = 7.5s of backoff
plus up to one per-attempt request latency.

To change behaviour: edit these constants and redeploy retrieval. A
breadcrumb pointing here lives in backend/README.md.

Each retry attempt emits a ``retrying`` SSE event via the
``current_sse_emitter`` ContextVar so the frontend can render a
"reconnecting" indicator. If no emitter is bound (background callers),
the event is a no-op.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator, Optional

import httpx

from metrics import observe_vllm_request

logger = logging.getLogger(__name__)

from database import VLLM_URL, LLM_HEADERS
from mcp.context import current_sse_emitter

# ------------------------------------------------------------------
# Retry tunables.
# ------------------------------------------------------------------
MAX_ATTEMPTS_FOREGROUND = 5
MAX_ATTEMPTS_BACKGROUND = 2
BASE_DELAY_S = 0.5
MAX_RETRY_AFTER_S = 30.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 529})


class VLLMRequestError(RuntimeError):
    """Raised when a vLLM request fails after exhausting retries (transient)
    or immediately on a non-retryable status (permanent). Callers translate
    this into an ``error`` SSE event or a ``None`` return as appropriate."""


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    """RFC 7231 ``Retry-After``: integer seconds OR HTTP-date. Returns the
    delay in seconds, or None if the header is absent / unparseable."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (dt - datetime.now(tz=timezone.utc)).total_seconds())


def _compute_delay(attempt: int, retry_after: Optional[str]) -> float:
    """Delay before the next attempt (0-indexed). Honours ``Retry-After``
    if parseable, otherwise expo backoff with +/-25% jitter. Both forms
    are clamped at ``MAX_RETRY_AFTER_S`` so a hostile or misconfigured
    upstream can't park us for an hour."""
    ra = _parse_retry_after(retry_after)
    if ra is not None:
        return min(ra, MAX_RETRY_AFTER_S)
    expo = BASE_DELAY_S * (2 ** attempt)
    jitter = expo * 0.25 * (2 * random.random() - 1)
    return min(MAX_RETRY_AFTER_S, max(0.0, expo + jitter))


def _emit_retrying(
    attempt: int, max_attempts: int, delay_s: float, reason: str
) -> None:
    """Push a ``retrying`` SSE event via the active emitter (no-op if unset).
    Catalogued in shared/docs/BACKEND-API.md §5."""
    emit = current_sse_emitter.get()
    if emit is None:
        return
    try:
        emit(
            "retrying",
            {
                "attempt": attempt + 1,  # 1-indexed for human display
                "max_attempts": max_attempts,
                "delay_s": round(delay_s, 2),
                "reason": reason,
            },
        )
    except Exception:
        pass


async def _sleep_with_event(
    attempt: int,
    max_attempts: int,
    retry_after: Optional[str],
    reason: str,
) -> None:
    delay = _compute_delay(attempt, retry_after)
    _emit_retrying(attempt, max_attempts, delay, reason)
    logger.warning(
        "vLLM retrying (%s) in %.2fs (attempt %d/%d)",
        reason, delay, attempt + 1, max_attempts,
    )
    await asyncio.sleep(delay)


async def vllm_post_json(
    body: dict,
    *,
    timeout: float = 60.0,
    foreground: bool = True,
    purpose: Optional[str] = None,
) -> dict:
    """Non-streaming POST to ``/v1/chat/completions``. Returns parsed JSON
    on success. Raises ``VLLMRequestError`` on permanent failure or after
    exhausting retries.

    ``foreground=False`` shortens the retry budget for callers where the
    user isn't waiting (title generation, history summarisation) so we
    don't cascade load when vLLM is already wedged.

    ``purpose`` (P1 #12) tags the call for ``munin_vllm_request_total`` /
    ``munin_vllm_request_duration_seconds``. None silently skips the
    metric so non-instrumented callers don't emit unlabelled data."""
    max_attempts = MAX_ATTEMPTS_FOREGROUND if foreground else MAX_ATTEMPTS_BACKGROUND
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(max_attempts):
            try:
                r = await client.post(
                    f"{VLLM_URL}/v1/chat/completions",
                    headers=LLM_HEADERS,
                    json=body,
                )
            except httpx.RequestError as e:
                if attempt + 1 < max_attempts:
                    observe_vllm_request(purpose, "transient_retry")
                    await _sleep_with_event(
                        attempt,
                        max_attempts,
                        None,
                        reason=f"vllm {type(e).__name__}",
                    )
                    continue
                observe_vllm_request(purpose, "transport_error")
                raise VLLMRequestError(
                    f"vLLM unreachable after {max_attempts} attempts: {e}"
                )
            if r.status_code in RETRYABLE_STATUS:
                if attempt + 1 < max_attempts:
                    observe_vllm_request(purpose, "transient_retry")
                    await _sleep_with_event(
                        attempt,
                        max_attempts,
                        r.headers.get("retry-after"),
                        reason=f"vllm {r.status_code}",
                    )
                    continue
                observe_vllm_request(purpose, "transport_error")
                raise VLLMRequestError(
                    f"vLLM returned {r.status_code} after {max_attempts} "
                    f"attempts: {r.text[:300]}"
                )
            if r.status_code != 200:
                observe_vllm_request(purpose, "permanent")
                raise VLLMRequestError(
                    f"vLLM returned {r.status_code}: {r.text[:300]}"
                )
            try:
                data = r.json()
            except Exception as e:
                observe_vllm_request(purpose, "permanent")
                raise VLLMRequestError(f"vLLM returned non-JSON: {e}")
            observe_vllm_request(purpose, "success", time.monotonic() - t0)
            return data
        # Loop body always either returns, raises, or continues; guard
        # against future refactors that break that invariant.
        raise VLLMRequestError("vllm_post_json: retries exhausted (unreachable)")


@asynccontextmanager
async def vllm_post_stream(
    body: dict, *, purpose: Optional[str] = None
) -> AsyncIterator[AsyncIterator[str]]:
    """Streaming POST to ``/v1/chat/completions``. Yields an async iterator
    of raw SSE lines; the caller parses the ``data:`` prefix, JSON, and
    ``[DONE]`` sentinel.

    Retries on transport errors and retryable status codes BEFORE the
    first data line reaches the caller. Once a line is yielded the stream
    is committed and subsequent failures propagate to the caller, which
    must emit its own ``error`` SSE event using whatever it has already
    accumulated. Retrying after partial state would double-emit on the
    wire.

    ``purpose`` (P1 #12) tags the call for Prometheus. The duration we
    observe is *time to first byte* — the post-commit body can take
    minutes to drain on a long generation, which is not really
    "request duration" in the histogram sense."""
    max_attempts = MAX_ATTEMPTS_FOREGROUND
    t0 = time.monotonic()
    # Client lifetime spans the entire context so the response iterator
    # stays valid while the caller consumes it.
    async with httpx.AsyncClient(timeout=None) as client:
        for attempt in range(max_attempts):
            try:
                stream_cm = client.stream(
                    "POST",
                    f"{VLLM_URL}/v1/chat/completions",
                    json=body,
                    headers={**LLM_HEADERS, "Accept": "text/event-stream"},
                )
                async with stream_cm as response:
                    if response.status_code in RETRYABLE_STATUS:
                        body_text = (await response.aread()).decode(
                            errors="ignore"
                        )[:300]
                        if attempt + 1 < max_attempts:
                            observe_vllm_request(purpose, "transient_retry")
                            await _sleep_with_event(
                                attempt,
                                max_attempts,
                                response.headers.get("retry-after"),
                                reason=f"vllm {response.status_code}",
                            )
                            continue
                        observe_vllm_request(purpose, "transport_error")
                        raise VLLMRequestError(
                            f"vLLM returned {response.status_code} after "
                            f"{max_attempts} attempts: {body_text}"
                        )
                    if response.status_code != 200:
                        text = (await response.aread()).decode(errors="ignore")[:300]
                        observe_vllm_request(purpose, "permanent")
                        raise VLLMRequestError(
                            f"vLLM returned {response.status_code}: {text}"
                        )

                    # Peek the first line so a transport drop between
                    # the status code and the first data byte is still
                    # retryable. Once first_line is bound we're committed.
                    line_iter = response.aiter_lines()
                    try:
                        first_line = await line_iter.__anext__()
                    except StopAsyncIteration:
                        first_line = None
                    except httpx.RequestError as e:
                        if attempt + 1 < max_attempts:
                            observe_vllm_request(purpose, "transient_retry")
                            await _sleep_with_event(
                                attempt,
                                max_attempts,
                                None,
                                reason=f"vllm {type(e).__name__}",
                            )
                            continue
                        observe_vllm_request(purpose, "transport_error")
                        raise VLLMRequestError(
                            f"vLLM stream dropped before first byte: {e}"
                        )

                    async def _replay() -> AsyncIterator[str]:
                        if first_line is not None:
                            yield first_line
                        async for line in line_iter:
                            yield line

                    observe_vllm_request(
                        purpose, "success", time.monotonic() - t0
                    )
                    yield _replay()
                    return
            except httpx.RequestError as e:
                # Connection-level failure before a status code arrived.
                if attempt + 1 < max_attempts:
                    observe_vllm_request(purpose, "transient_retry")
                    await _sleep_with_event(
                        attempt,
                        max_attempts,
                        None,
                        reason=f"vllm {type(e).__name__}",
                    )
                    continue
                observe_vllm_request(purpose, "transport_error")
                raise VLLMRequestError(
                    f"vLLM unreachable after {max_attempts} attempts: {e}"
                )
        raise VLLMRequestError(
            "vllm_post_stream: retries exhausted (unreachable)"
        )
