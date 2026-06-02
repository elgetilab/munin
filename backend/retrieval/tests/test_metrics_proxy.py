"""
Tests for metrics_proxy.py (Bug, 2026-06-02 in-house dashboards).

Covers:
  - `is_configured()` reflects env presence
  - `lookup_role` round-trips to /admin/check-role with the bearer
    token, caches the result, and returns None on 404
  - `query_instant` / `query_range` forward to Prometheus and return
    its JSON body

The auth + Prometheus endpoints are stubbed via httpx.MockTransport so
the tests have no network dependency.
"""

from __future__ import annotations

import asyncio
import sys
import traceback

sys.path.insert(0, "/app")

import httpx  # noqa: E402

import metrics_proxy  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    suffix = f" --- {detail}" if detail and not ok else ""
    print(f"[{label}] {name}{suffix}")
    return ok


def _run(coro):
    return asyncio.run(coro)


# ── is_configured() ────────────────────────────────────────────────────────


def test_is_configured_true_when_env_set() -> bool:
    # The test module is run with KB_GATE_TOKEN already set via the
    # auth_env equivalent for retrieval -- but to be hermetic we patch
    # the module-level constants directly.
    saved_token = metrics_proxy._KB_GATE_TOKEN
    saved_url = metrics_proxy._PROMETHEUS_URL
    try:
        metrics_proxy._KB_GATE_TOKEN = "abc"
        metrics_proxy._PROMETHEUS_URL = "http://prom:9090"
        ok = metrics_proxy.is_configured() is True
    finally:
        metrics_proxy._KB_GATE_TOKEN = saved_token
        metrics_proxy._PROMETHEUS_URL = saved_url
    return _check("is_configured true when env set", ok)


def test_is_configured_false_when_token_missing() -> bool:
    saved = metrics_proxy._KB_GATE_TOKEN
    try:
        metrics_proxy._KB_GATE_TOKEN = ""
        ok = metrics_proxy.is_configured() is False
    finally:
        metrics_proxy._KB_GATE_TOKEN = saved
    return _check("is_configured false when token missing", ok)


# ── lookup_role ────────────────────────────────────────────────────────────


def _client_with_handler(handler):
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


def test_lookup_role_sends_bearer_and_returns_role() -> bool:
    saved_token = metrics_proxy._KB_GATE_TOKEN
    saved_url = metrics_proxy._AUTH_CHECK_ROLE_URL
    metrics_proxy._reset_cache_for_tests()
    seen_authz = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authz.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json={"email": "a@x.org", "role": "admin"})

    try:
        metrics_proxy._KB_GATE_TOKEN = "test-token"
        metrics_proxy._AUTH_CHECK_ROLE_URL = "http://auth.test/admin/check-role"
        async def go():
            async with _client_with_handler(handler) as c:
                return await metrics_proxy.lookup_role(c, "a@x.org")
        role = _run(go())
    finally:
        metrics_proxy._KB_GATE_TOKEN = saved_token
        metrics_proxy._AUTH_CHECK_ROLE_URL = saved_url

    if role != "admin":
        return _check("lookup_role returns role", False, f"got {role!r}")
    if seen_authz != ["Bearer test-token"]:
        return _check(
            "lookup_role sends bearer header",
            False,
            f"seen_authz={seen_authz!r}",
        )
    return _check("lookup_role returns role + sends bearer", True)


def test_lookup_role_caches_result() -> bool:
    saved_token = metrics_proxy._KB_GATE_TOKEN
    saved_url = metrics_proxy._AUTH_CHECK_ROLE_URL
    metrics_proxy._reset_cache_for_tests()
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"email": "a@x.org", "role": "user"})

    try:
        metrics_proxy._KB_GATE_TOKEN = "tok"
        metrics_proxy._AUTH_CHECK_ROLE_URL = "http://auth.test/admin/check-role"
        async def go():
            async with _client_with_handler(handler) as c:
                r1 = await metrics_proxy.lookup_role(c, "a@x.org")
                r2 = await metrics_proxy.lookup_role(c, "a@x.org")
                r3 = await metrics_proxy.lookup_role(c, "a@x.org")
                return (r1, r2, r3)
        r1, r2, r3 = _run(go())
    finally:
        metrics_proxy._KB_GATE_TOKEN = saved_token
        metrics_proxy._AUTH_CHECK_ROLE_URL = saved_url

    if call_count != 1:
        return _check(
            "lookup_role caches after first call",
            False,
            f"expected 1 round-trip, got {call_count}",
        )
    if not (r1 == r2 == r3 == "user"):
        return _check(
            "lookup_role returns cached value consistently",
            False,
            f"r1={r1!r} r2={r2!r} r3={r3!r}",
        )
    return _check("lookup_role caches after first call", True)


def test_lookup_role_returns_none_on_404() -> bool:
    saved_token = metrics_proxy._KB_GATE_TOKEN
    saved_url = metrics_proxy._AUTH_CHECK_ROLE_URL
    metrics_proxy._reset_cache_for_tests()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"role": None})

    try:
        metrics_proxy._KB_GATE_TOKEN = "tok"
        metrics_proxy._AUTH_CHECK_ROLE_URL = "http://auth.test/admin/check-role"
        async def go():
            async with _client_with_handler(handler) as c:
                return await metrics_proxy.lookup_role(c, "noone@x.org")
        result = _run(go())
    finally:
        metrics_proxy._KB_GATE_TOKEN = saved_token
        metrics_proxy._AUTH_CHECK_ROLE_URL = saved_url

    return _check(
        "lookup_role returns None on 404",
        result is None,
        f"got {result!r}",
    )


# ── query_instant / query_range ────────────────────────────────────────────


def test_query_instant_forwards_to_prometheus() -> bool:
    saved_url = metrics_proxy._PROMETHEUS_URL
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"status": "success", "data": {"resultType": "vector", "result": []}},
        )

    try:
        metrics_proxy._PROMETHEUS_URL = "http://prom.test:9090"
        async def go():
            async with _client_with_handler(handler) as c:
                return await metrics_proxy.query_instant(c, "up")
        result = _run(go())
    finally:
        metrics_proxy._PROMETHEUS_URL = saved_url

    if "prom.test:9090/api/v1/query" not in captured["url"]:
        return _check(
            "query_instant POSTs to /api/v1/query",
            False,
            f"url={captured['url']!r}",
        )
    if "query=up" not in captured["body"]:
        return _check(
            "query_instant includes query in form body",
            False,
            f"body={captured['body']!r}",
        )
    if result.get("status") != "success":
        return _check(
            "query_instant returns prometheus body",
            False,
            f"result={result!r}",
        )
    return _check("query_instant forwards to prometheus", True)


def test_query_range_forwards_all_params() -> bool:
    saved_url = metrics_proxy._PROMETHEUS_URL
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content.decode()
        return httpx.Response(200, json={"status": "success"})

    try:
        metrics_proxy._PROMETHEUS_URL = "http://prom.test:9090"
        async def go():
            async with _client_with_handler(handler) as c:
                return await metrics_proxy.query_range(
                    c, "rate(munin_vllm_request_total[5m])",
                    "2026-06-02T00:00:00Z", "2026-06-02T01:00:00Z", "60s",
                )
        _run(go())
    finally:
        metrics_proxy._PROMETHEUS_URL = saved_url

    body = captured["body"]
    for required in ("query=", "start=", "end=", "step="):
        if required not in body:
            return _check(
                "query_range includes start/end/step",
                False,
                f"missing {required!r} in {body!r}",
            )
    return _check("query_range forwards all params", True)


def main() -> int:
    tests = [
        test_is_configured_true_when_env_set,
        test_is_configured_false_when_token_missing,
        test_lookup_role_sends_bearer_and_returns_role,
        test_lookup_role_caches_result,
        test_lookup_role_returns_none_on_404,
        test_query_instant_forwards_to_prometheus,
        test_query_range_forwards_all_params,
    ]
    results: list[bool] = []
    for t in tests:
        try:
            results.append(t())
        except Exception:
            traceback.print_exc()
            results.append(False)
    failed = sum(1 for r in results if not r)
    print(
        f"\n{len(results) - failed}/{len(results)} passed; {failed} failed."
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
