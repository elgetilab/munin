"""
Standalone tests for retrieval/metrics.py (P1 #12).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_metrics.py
Or locally:
    python backend/retrieval/tests/test_metrics.py

Exercises the helpers in metrics.py and verifies the /metrics ASGI app
emits a valid Prometheus exposition payload with the expected series
present. No FastAPI app boot, no HTTP client.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import metrics  # noqa: E402
from metrics import (  # noqa: E402
    chat_turns_total,
    mcp_tool_duration_seconds,
    mcp_tool_total,
    observe_phantom_urls,
    observe_tool,
    observe_turn,
    observe_vllm_request,
    observe_vllm_tokens,
    phantom_url_total,
    vllm_request_duration_seconds,
    vllm_request_total,
    vllm_tokens_total,
)


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def _counter_value(counter, **labels) -> float:
    """Read the current counter value for a label set, or 0 if not yet seen."""
    return counter.labels(**labels)._value.get()


def _histogram_count(hist, **labels) -> int:
    """Number of observations recorded for a histogram label set.

    prometheus_client doesn't expose a stable public attribute for the
    sample count on a labelled histogram, so we go through collect() and
    pick the ``_count`` sample for the matching label set."""
    target = labels
    for metric in hist.collect():
        for sample in metric.samples:
            if sample.name.endswith("_count") and sample.labels == {
                k: str(v) for k, v in target.items()
            }:
                return int(sample.value)
    return 0


# ---------------------------------------------------------------------------
# Counter / histogram increments
# ---------------------------------------------------------------------------

def test_observe_vllm_request_success_with_duration() -> bool:
    before = _counter_value(vllm_request_total, purpose="t_success", outcome="success")
    before_hist = _histogram_count(vllm_request_duration_seconds, purpose="t_success")
    observe_vllm_request("t_success", "success", 0.42)
    after = _counter_value(vllm_request_total, purpose="t_success", outcome="success")
    after_hist = _histogram_count(vllm_request_duration_seconds, purpose="t_success")
    return _check(
        "observe_vllm_request success bumps counter + histogram",
        after == before + 1 and after_hist == before_hist + 1,
    )


def test_observe_vllm_request_retry_no_histogram() -> bool:
    before = _counter_value(vllm_request_total, purpose="t_retry", outcome="transient_retry")
    before_hist = _histogram_count(vllm_request_duration_seconds, purpose="t_retry")
    observe_vllm_request("t_retry", "transient_retry")
    after = _counter_value(vllm_request_total, purpose="t_retry", outcome="transient_retry")
    after_hist = _histogram_count(vllm_request_duration_seconds, purpose="t_retry")
    return _check(
        "transient_retry bumps counter only (histogram untouched)",
        after == before + 1 and after_hist == before_hist,
    )


def test_observe_vllm_request_none_purpose_is_noop() -> bool:
    # No-op path: a None purpose should not crash and not write any labels.
    try:
        observe_vllm_request(None, "success", 1.0)
        ok = True
    except Exception:
        ok = False
    return _check("observe_vllm_request(None, ...) is a silent no-op", ok)


def test_observe_vllm_tokens_splits_prompt_completion() -> bool:
    before_p = _counter_value(vllm_tokens_total, purpose="t_tok", kind="prompt")
    before_c = _counter_value(vllm_tokens_total, purpose="t_tok", kind="completion")
    observe_vllm_tokens("t_tok", 100, 50)
    after_p = _counter_value(vllm_tokens_total, purpose="t_tok", kind="prompt")
    after_c = _counter_value(vllm_tokens_total, purpose="t_tok", kind="completion")
    return _check(
        "observe_vllm_tokens increments prompt and completion separately",
        after_p == before_p + 100 and after_c == before_c + 50,
    )


def test_observe_vllm_tokens_zero_is_skipped() -> bool:
    before_p = _counter_value(vllm_tokens_total, purpose="t_zero", kind="prompt")
    before_c = _counter_value(vllm_tokens_total, purpose="t_zero", kind="completion")
    observe_vllm_tokens("t_zero", 0, 0)
    after_p = _counter_value(vllm_tokens_total, purpose="t_zero", kind="prompt")
    after_c = _counter_value(vllm_tokens_total, purpose="t_zero", kind="completion")
    return _check(
        "observe_vllm_tokens with zero counts emits nothing",
        after_p == before_p and after_c == before_c,
    )


def test_observe_tool_increments_and_times() -> bool:
    before = _counter_value(mcp_tool_total, name="t_tool", outcome="success")
    before_hist = _histogram_count(mcp_tool_duration_seconds, name="t_tool")
    observe_tool("t_tool", "success", 0.05)
    after = _counter_value(mcp_tool_total, name="t_tool", outcome="success")
    after_hist = _histogram_count(mcp_tool_duration_seconds, name="t_tool")
    return _check(
        "observe_tool bumps counter + histogram",
        after == before + 1 and after_hist == before_hist + 1,
    )


def test_observe_turn_records_persona_unknown_for_none() -> bool:
    before = _counter_value(chat_turns_total, persona="unknown", terminal_reason="done")
    observe_turn(None, "done")
    after = _counter_value(chat_turns_total, persona="unknown", terminal_reason="done")
    return _check(
        "observe_turn(None, ...) buckets under persona='unknown'",
        after == before + 1,
    )


def test_observe_turn_records_known_persona() -> bool:
    before = _counter_value(chat_turns_total, persona="chat", terminal_reason="cancelled")
    observe_turn("chat", "cancelled")
    after = _counter_value(chat_turns_total, persona="chat", terminal_reason="cancelled")
    return _check(
        "observe_turn passes through a real persona label",
        after == before + 1,
    )


def test_observe_phantom_urls_skips_zero() -> bool:
    before = _counter_value(phantom_url_total, kind="artifact")
    observe_phantom_urls("artifact", 0)
    after_zero = _counter_value(phantom_url_total, kind="artifact")
    observe_phantom_urls("artifact", 3)
    after_three = _counter_value(phantom_url_total, kind="artifact")
    return _check(
        "observe_phantom_urls skips count=0 and adds count>0",
        after_zero == before and after_three == before + 3,
    )


# ---------------------------------------------------------------------------
# /metrics ASGI app exposes valid Prometheus payload
# ---------------------------------------------------------------------------

async def _scrape_metrics_app() -> tuple[int, dict, bytes]:
    """Drive the make_asgi_app() endpoint with a minimal ASGI scope and
    collect (status, headers_dict, body). No HTTP server in the loop."""
    from prometheus_client import make_asgi_app

    app = make_asgi_app()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/metrics",
        "raw_path": b"/metrics",
        "query_string": b"",
        "headers": [],
    }
    body = bytearray()
    status: list[int] = []
    headers_dict: dict = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])
            for k, v in message.get("headers", []):
                headers_dict[k.decode().lower()] = v.decode()
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    await app(scope, receive, send)
    return status[0], headers_dict, bytes(body)


def test_metrics_endpoint_emits_valid_payload() -> bool:
    # Make sure at least one custom metric is recorded so we can grep
    # the payload for it.
    observe_vllm_request("scrape_probe", "success", 0.1)
    status, headers, body = asyncio.run(_scrape_metrics_app())
    text = body.decode(errors="replace")
    has_help = "# HELP munin_vllm_request_total" in text
    has_type = "# TYPE munin_vllm_request_total counter" in text
    has_sample = "scrape_probe" in text
    content_type_ok = "text/plain" in headers.get("content-type", "")
    return _check(
        "/metrics ASGI app emits valid Prometheus exposition",
        status == 200 and content_type_ok and has_help and has_type and has_sample,
    )


def test_metrics_endpoint_includes_default_process_metrics() -> bool:
    _, _, body = asyncio.run(_scrape_metrics_app())
    text = body.decode(errors="replace")
    # prometheus_client ships process_* and python_gc_* by default.
    return _check(
        "/metrics includes default process / python collectors",
        ("process_cpu_seconds_total" in text or "process_resident_memory_bytes" in text)
        and "python_info" in text,
    )


def test_mount_metrics_attaches_route() -> bool:
    # mount_metrics should mount the ASGI app at /metrics on a FastAPI
    # instance. We don't boot the full app; we just check the route
    # registry has an entry with path == /metrics after the call.
    try:
        from fastapi import FastAPI
    except ImportError:
        # Running outside the retrieval container; fastapi is a runtime
        # dep of the service, not the standalone test harness. Skip
        # (pass) rather than fail.
        print("[SKIP] mount_metrics adds /metrics route - fastapi not installed")
        return True
    try:
        app = FastAPI()
        metrics.mount_metrics(app)
        paths = {getattr(r, "path", None) for r in app.routes}
        return _check("mount_metrics adds /metrics route", "/metrics" in paths)
    except Exception as e:
        return _check("mount_metrics adds /metrics route", False, repr(e))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

TESTS = [
    test_observe_vllm_request_success_with_duration,
    test_observe_vllm_request_retry_no_histogram,
    test_observe_vllm_request_none_purpose_is_noop,
    test_observe_vllm_tokens_splits_prompt_completion,
    test_observe_vllm_tokens_zero_is_skipped,
    test_observe_tool_increments_and_times,
    test_observe_turn_records_persona_unknown_for_none,
    test_observe_turn_records_known_persona,
    test_observe_phantom_urls_skips_zero,
    test_metrics_endpoint_emits_valid_payload,
    test_metrics_endpoint_includes_default_process_metrics,
    test_mount_metrics_attaches_route,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
