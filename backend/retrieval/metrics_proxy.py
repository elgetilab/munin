"""
Admin-gated proxy from retrieval to Prometheus's HTTP query API.

Why this exists: the in-tree admin panel (webui AdminPanel ->
Metrics tab) needs to run PromQL queries to render the dashboards
that used to live in Grafana. Rather than expose Prometheus
publicly or run another auth layer, we route queries through the
retrieval service which already sits behind Caddy's forward-auth.

Surface:
    POST /api/admin/metrics/query        -> Prometheus /api/v1/query
    POST /api/admin/metrics/query_range  -> Prometheus /api/v1/query_range

Both take the same query body as Prometheus expects, plus a leading
admin-role check. The retrieval service verifies the caller is an
admin by calling auth.<domain>/admin/check-role with the shared
KB_GATE_TOKEN (the same lookup the tusd hook-service uses).

Role-check responses are cached per email for a few seconds so a
dashboard refresh that fires N queries doesn't make N round-trips
to auth.

PromQL is read-only by design; there is no proxy for /api/v1/write,
admin endpoints, or query mutators. The dashboard never needs them.
"""

from __future__ import annotations

import os
import time
from typing import Optional

import httpx

import site_config


# Unset: derived from MUNIN_DOMAIN. Set but empty: the role check (and so the
# Metrics tab) is off, which is what a single host without a reachable auth
# service wants.
_AUTH_CHECK_ROLE_URL = os.environ.get("AUTH_CHECK_ROLE_URL")
if _AUTH_CHECK_ROLE_URL is None:
    _AUTH_CHECK_ROLE_URL = site_config.auth_url("/admin/check-role")
_KB_GATE_TOKEN = os.getenv("KB_GATE_TOKEN", "").strip()
_PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")

# email (lowercased) -> (role, expires_at_monotonic). Per-email cache
# so a dashboard load that fans out to ~8 panels only round-trips
# once. 30s TTL is short enough that demoting an admin takes effect
# in under a minute without manual cache flushes.
_ROLE_CACHE: dict[str, tuple[str, float]] = {}
_ROLE_CACHE_TTL_S = 30.0


def is_configured() -> bool:
    """Whether the proxy has the auth token + a Prometheus URL set.
    Used by routes to decide whether to return 200 or 503-not-configured."""
    return bool(_KB_GATE_TOKEN) and bool(_PROMETHEUS_URL)


def _cache_get(email: str) -> Optional[str]:
    rec = _ROLE_CACHE.get(email)
    if not rec:
        return None
    role, expires_at = rec
    if time.monotonic() >= expires_at:
        _ROLE_CACHE.pop(email, None)
        return None
    return role


def _cache_set(email: str, role: str) -> None:
    _ROLE_CACHE[email] = (role, time.monotonic() + _ROLE_CACHE_TTL_S)


async def lookup_role(client: httpx.AsyncClient, email: str) -> Optional[str]:
    """Return the user's role string, or None if the email is unknown.

    Hits the auth service's /admin/check-role with the shared bearer
    token. Result is cached per email for `_ROLE_CACHE_TTL_S`. Raises
    on network/auth errors so the caller can map to the right HTTP
    status code -- the caller knows the request context."""
    email = email.strip().lower()
    cached = _cache_get(email)
    if cached is not None:
        return cached if cached else None

    resp = await client.get(
        _AUTH_CHECK_ROLE_URL,
        params={"email": email},
        headers={"Authorization": f"Bearer {_KB_GATE_TOKEN}"},
        timeout=5.0,
    )
    if resp.status_code == 404:
        # Cache the negative result too so a flood of queries from a
        # signed-out / unknown user doesn't hammer auth.
        _cache_set(email, "")
        return None
    resp.raise_for_status()
    body = resp.json()
    role = (body.get("role") or "").strip()
    _cache_set(email, role)
    return role or None


async def query_instant(client: httpx.AsyncClient, query: str,
                        time_param: Optional[str] = None) -> dict:
    """Prometheus /api/v1/query passthrough. Returns the parsed JSON
    body. Raises httpx.HTTPStatusError on non-2xx."""
    params = {"query": query}
    if time_param:
        params["time"] = time_param
    resp = await client.post(
        f"{_PROMETHEUS_URL}/api/v1/query",
        data=params,
        timeout=15.0,
    )
    resp.raise_for_status()
    return resp.json()


async def query_range(client: httpx.AsyncClient, query: str,
                      start: str, end: str, step: str) -> dict:
    """Prometheus /api/v1/query_range passthrough.

    `start` and `end` are RFC3339 strings or unix timestamps (the
    Prometheus API accepts both). `step` is a duration like '15s' or
    a float seconds. We don't validate -- Prometheus does, and its
    error responses are clearer than anything we'd construct."""
    params = {"query": query, "start": start, "end": end, "step": step}
    resp = await client.post(
        f"{_PROMETHEUS_URL}/api/v1/query_range",
        data=params,
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def _reset_cache_for_tests() -> None:
    """Test helper. Not invoked anywhere in production."""
    _ROLE_CACHE.clear()
