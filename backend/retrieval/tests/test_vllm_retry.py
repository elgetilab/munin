"""
Standalone tests for vllm_client.py retry behaviour.

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_vllm_retry.py
Or locally from the repo root (httpx is the only required dep):
    python backend/retrieval/tests/test_vllm_retry.py

Pure-function tests using httpx.MockTransport — no live vLLM required.
asyncio.sleep is replaced with an instant no-op so tests run in ms.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

# Make `vllm_client` importable from /app (container) and from the local
# repo checkout (developer machine).
sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import vllm_client  # noqa: E402
from vllm_client import (  # noqa: E402
    VLLMRequestError,
    _compute_delay,
    _parse_retry_after,
    MAX_ATTEMPTS_BACKGROUND,
    MAX_ATTEMPTS_FOREGROUND,
    MAX_RETRY_AFTER_S,
)
from mcp.context import current_sse_emitter  # noqa: E402


# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------

sleep_calls: list[float] = []
_real_sleep = asyncio.sleep


async def _instant_sleep(delay: float) -> None:
    sleep_calls.append(delay)


asyncio.sleep = _instant_sleep  # type: ignore[assignment]

_RealClient = httpx.AsyncClient


class _MockClient:
    """Stand-in for httpx.AsyncClient that injects a MockTransport."""

    def __init__(self, handler):
        self._handler = handler

    def __call__(self, **kwargs):
        kwargs.pop("transport", None)
        return _RealClient(transport=httpx.MockTransport(self._handler), **kwargs)


def _install_mock(handler):
    vllm_client.httpx.AsyncClient = _MockClient(handler)


def _uninstall_mock():
    vllm_client.httpx.AsyncClient = _RealClient


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# vllm_post_json tests
# ---------------------------------------------------------------------------

def test_post_json_transient_then_ok() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="upstream busy")
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    _install_mock(handler)
    try:
        data = asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
    finally:
        _uninstall_mock()
    return _check(
        "post_json: 503 then 200 succeeds after one retry",
        data["choices"][0]["message"]["content"] == "ok"
        and len(attempts) == 2
        and len(sleep_calls) == 1,
        f"attempts={len(attempts)}, sleeps={len(sleep_calls)}",
    )


def test_post_json_exhausts_retries() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503, text="still busy")

    _install_mock(handler)
    raised: Exception | None = None
    try:
        try:
            asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
        except VLLMRequestError as e:
            raised = e
    finally:
        _uninstall_mock()
    return _check(
        "post_json: sustained 503 raises VLLMRequestError after MAX_ATTEMPTS_FOREGROUND",
        raised is not None
        and len(attempts) == MAX_ATTEMPTS_FOREGROUND
        and len(sleep_calls) == MAX_ATTEMPTS_FOREGROUND - 1,
        f"attempts={len(attempts)}, sleeps={len(sleep_calls)}, raised={raised}",
    )


def test_post_json_400_no_retry() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad request")

    _install_mock(handler)
    raised: Exception | None = None
    try:
        try:
            asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
        except VLLMRequestError as e:
            raised = e
    finally:
        _uninstall_mock()
    return _check(
        "post_json: 400 raises immediately with no retry, no sleep",
        raised is not None and len(attempts) == 1 and len(sleep_calls) == 0,
        f"attempts={len(attempts)}, sleeps={len(sleep_calls)}",
    )


def test_post_json_connect_error_retried() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("kaboom")
        return httpx.Response(200, json={"choices": []})

    _install_mock(handler)
    try:
        data = asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
    finally:
        _uninstall_mock()
    return _check(
        "post_json: ConnectError is retried, recovers on third attempt",
        data == {"choices": []} and len(attempts) == 3 and len(sleep_calls) == 2,
        f"attempts={len(attempts)}, sleeps={len(sleep_calls)}",
    )


def test_post_json_background_bails_fast() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    _install_mock(handler)
    raised: Exception | None = None
    try:
        try:
            asyncio.run(
                vllm_client.vllm_post_json({"model": "x"}, foreground=False)
            )
        except VLLMRequestError as e:
            raised = e
    finally:
        _uninstall_mock()
    return _check(
        "post_json: foreground=False caps at MAX_ATTEMPTS_BACKGROUND",
        raised is not None and len(attempts) == MAX_ATTEMPTS_BACKGROUND,
        f"attempts={len(attempts)} (expected {MAX_ATTEMPTS_BACKGROUND})",
    )


def test_post_json_retry_after_honored() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"choices": []})

    _install_mock(handler)
    try:
        asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
    finally:
        _uninstall_mock()
    return _check(
        "post_json: 429 with Retry-After: 7 sleeps exactly 7s",
        sleep_calls == [7.0],
        f"sleep_calls={sleep_calls}",
    )


def test_post_json_retry_after_clamped() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, headers={"Retry-After": "3600"})
        return httpx.Response(200, json={"choices": []})

    _install_mock(handler)
    try:
        asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
    finally:
        _uninstall_mock()
    return _check(
        "post_json: oversized Retry-After clamped to MAX_RETRY_AFTER_S",
        sleep_calls == [MAX_RETRY_AFTER_S],
        f"sleep_calls={sleep_calls}",
    )


def test_post_json_emits_retrying_event() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []
    events: list[tuple[str, dict]] = []

    def emit(name: str, data: dict) -> None:
        events.append((name, data))

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(502)
        return httpx.Response(200, json={"choices": []})

    _install_mock(handler)
    token = current_sse_emitter.set(emit)
    try:
        asyncio.run(vllm_client.vllm_post_json({"model": "x"}))
    finally:
        current_sse_emitter.reset(token)
        _uninstall_mock()
    ok = (
        len(events) == 1
        and events[0][0] == "retrying"
        and events[0][1]["attempt"] == 1
        and events[0][1]["max_attempts"] == MAX_ATTEMPTS_FOREGROUND
        and "502" in events[0][1]["reason"]
    )
    return _check(
        "post_json: retry path emits a 'retrying' SSE event with attempt/reason",
        ok,
        f"events={events}",
    )


# ---------------------------------------------------------------------------
# vllm_post_stream tests
# ---------------------------------------------------------------------------

def _sse_body(lines: list[str]) -> bytes:
    """Build a fake SSE body from a list of payload strings."""
    out = []
    for line in lines:
        out.append(f"data: {line}\n\n")
    return "".join(out).encode("utf-8")


def test_post_stream_success_first_try() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body(['{"choices":[{"delta":{"content":"hi"}}]}', "[DONE]"]),
        )

    async def collect() -> list[str]:
        async with vllm_client.vllm_post_stream({"model": "x"}) as line_iter:
            return [line async for line in line_iter]

    _install_mock(handler)
    try:
        lines = asyncio.run(collect())
    finally:
        _uninstall_mock()
    data_lines = [l for l in lines if l.startswith("data:")]
    return _check(
        "post_stream: succeeds first try, yields all data lines",
        len(attempts) == 1
        and len(sleep_calls) == 0
        and len(data_lines) == 2,
        f"attempts={len(attempts)}, lines={lines}",
    )


def test_post_stream_retries_on_5xx_before_first_byte() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse_body(["[DONE]"]),
        )

    async def collect() -> list[str]:
        async with vllm_client.vllm_post_stream({"model": "x"}) as line_iter:
            return [line async for line in line_iter]

    _install_mock(handler)
    try:
        lines = asyncio.run(collect())
    finally:
        _uninstall_mock()
    return _check(
        "post_stream: 503 then 200 retries open, yields stream on second try",
        len(attempts) == 2
        and len(sleep_calls) == 1
        and any(l.startswith("data:") for l in lines),
        f"attempts={len(attempts)}, sleeps={len(sleep_calls)}, lines={lines}",
    )


def test_post_stream_400_no_retry() -> bool:
    sleep_calls.clear()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(400, text="bad request")

    async def collect() -> list[str]:
        async with vllm_client.vllm_post_stream({"model": "x"}) as line_iter:
            return [line async for line in line_iter]

    _install_mock(handler)
    raised: Exception | None = None
    try:
        try:
            asyncio.run(collect())
        except VLLMRequestError as e:
            raised = e
    finally:
        _uninstall_mock()
    return _check(
        "post_stream: 400 raises immediately, no retry",
        raised is not None and len(attempts) == 1 and len(sleep_calls) == 0,
        f"attempts={len(attempts)}, raised={raised}",
    )


# ---------------------------------------------------------------------------
# _parse_retry_after / _compute_delay unit tests
# ---------------------------------------------------------------------------

def test_parse_retry_after_seconds() -> bool:
    return _check(
        "_parse_retry_after('12') == 12.0",
        _parse_retry_after("12") == 12.0,
    )


def test_parse_retry_after_http_date_future() -> bool:
    # Date in the future; parse should return a positive delay.
    val = _parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    return _check(
        "_parse_retry_after future HTTP-date returns positive delay",
        val is not None and val > 0,
        f"val={val}",
    )


def test_parse_retry_after_unparseable() -> bool:
    return _check(
        "_parse_retry_after garbage returns None",
        _parse_retry_after("not-a-date") is None
        and _parse_retry_after(None) is None
        and _parse_retry_after("") is None,
    )


def test_compute_delay_no_retry_after_uses_expo() -> bool:
    # Attempt 0: base * 1, ± 25% jitter. So delay ∈ [0.375, 0.625].
    delays = [_compute_delay(0, None) for _ in range(50)]
    ok = all(0.375 - 1e-9 <= d <= 0.625 + 1e-9 for d in delays)
    return _check(
        "_compute_delay(0) expo backoff sits within jitter envelope",
        ok,
        f"min={min(delays):.3f}, max={max(delays):.3f}",
    )


def test_compute_delay_clamps_huge() -> bool:
    # Big attempt + valid Retry-After well above cap; both paths clamp.
    return _check(
        "_compute_delay clamps oversize Retry-After at MAX_RETRY_AFTER_S",
        _compute_delay(0, "9999") == MAX_RETRY_AFTER_S,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_post_json_transient_then_ok,
    test_post_json_exhausts_retries,
    test_post_json_400_no_retry,
    test_post_json_connect_error_retried,
    test_post_json_background_bails_fast,
    test_post_json_retry_after_honored,
    test_post_json_retry_after_clamped,
    test_post_json_emits_retrying_event,
    test_post_stream_success_first_try,
    test_post_stream_retries_on_5xx_before_first_byte,
    test_post_stream_400_no_retry,
    test_parse_retry_after_seconds,
    test_parse_retry_after_http_date_future,
    test_parse_retry_after_unparseable,
    test_compute_delay_no_retry_after_uses_expo,
    test_compute_delay_clamps_huge,
]


def main() -> int:
    passed = 0
    failed = 0
    for test in TESTS:
        try:
            ok = test()
        except Exception:
            ok = False
            print(f"[FAIL] {test.__name__} - exception:")
            traceback.print_exc()
        if ok:
            passed += 1
        else:
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
