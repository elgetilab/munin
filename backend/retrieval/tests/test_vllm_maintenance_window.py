"""
Tests for the nightly vLLM maintenance window: the 503 + Retry-After that
replaces an unhandled 500, and the schedule arithmetic behind it.

TWO BUGS, one visible and one not.

1. `/v1/chat/completions` returned **500 Internal Server Error** for the four
   hours the cluster deliberately stops vLLM. `_raw_chat_proxy` never caught
   the `httpx.ConnectError` from a refused connection, so FastAPI turned it
   into a 500. Measured on the live gateway: ~105 of these per night from a
   single API key, every night, in a tight band that ends exactly when vLLM
   comes back. A scheduled outage is a 503 with `Retry-After`, not a 500;
   a 500 tells a client "this is broken, and retrying is pointless".

2. `_next_vllm_start_iso()` computed `hour=6` in CONTAINER-LOCAL time. The
   retrieval container runs UTC while the cron that starts vLLM
   (`schedule-vllm.sh start`, 06:00) runs in cluster-local time, so the
   answer was 06:00 UTC = 08:00 CEST: two hours after vLLM was actually
   back, and wrong by a different amount in winter. That value is also what
   the frontend shows users as "next start", so the fix is user-visible
   beyond this endpoint.

   The live gateway log is the ground truth: requests fail through 03:xx UTC
   and succeed from 04:00 UTC, i.e. 02:00-06:00 Europe/Berlin in summer.

Run standalone or under pytest:
    python backend/retrieval/tests/test_vllm_maintenance_window.py
    docker exec munin-retrieval python /app/tests/test_vllm_maintenance_window.py
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from zoneinfo import ZoneInfo  # noqa: E402

import main  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' --- ' + detail) if detail and not ok else ''}")
    return ok


# ---------------------------------------------------------------------------
# Window arithmetic
# ---------------------------------------------------------------------------

def test_window_covers_the_measured_outage() -> bool:
    """02:00-06:00 Europe/Berlin, checked at the boundaries. 06:00 itself is
    OUTSIDE: that is the instant vLLM comes back."""
    cases = {
        1: False,   # 01:59 still serving
        2: True,    # stop fires
        3: True,
        5: True,
        6: False,   # start fires
        7: False,
        23: False,
    }
    ok = True
    for hour, expected in cases.items():
        now = datetime(2026, 9, 10, hour, 30, tzinfo=BERLIN)
        got = main._in_maintenance_window(now)
        if got != expected:
            ok = False
            print(f"    hour {hour:02d} Berlin: expected {expected}, got {got}")
    return _check("maintenance window is 02:00-06:00 cluster-local", ok)


def test_window_is_evaluated_in_cluster_time_not_container_time() -> bool:
    """The container runs UTC. 03:30 UTC is 05:30 Berlin (inside the window)
    and 23:30 UTC is 01:30 Berlin (outside it). Evaluating the hour in
    container-local time would get both wrong."""
    inside = main._in_maintenance_window(datetime(2026, 9, 10, 3, 30, tzinfo=UTC))
    outside = main._in_maintenance_window(datetime(2026, 9, 10, 23, 30, tzinfo=UTC))
    return _check(
        "window is computed in the schedule's zone, not the container's",
        inside is True and outside is False,
        f"03:30Z inside={inside} (want True), 23:30Z outside={outside} (want False)",
    )


def test_next_start_is_the_real_start_not_two_hours_late() -> bool:
    """The regression. In summer the next start after 03:00 UTC must be
    04:00 UTC (06:00 CEST). The old `hour=6` in container-local UTC produced
    06:00 UTC, which is 08:00 CEST."""
    nxt = main._next_vllm_start(datetime(2026, 9, 10, 3, 0, tzinfo=UTC))
    as_utc = nxt.astimezone(UTC)
    return _check(
        "next start is 04:00Z in summer (06:00 CEST), not 06:00Z",
        (as_utc.hour, as_utc.minute) == (4, 0),
        f"got {as_utc.isoformat()}",
    )


def test_next_start_follows_dst() -> bool:
    """In winter Berlin is UTC+1, so the same 06:00 local start is 05:00Z.
    A hardcoded UTC hour would be right for only half the year."""
    nxt = main._next_vllm_start(datetime(2026, 1, 15, 3, 0, tzinfo=UTC))
    as_utc = nxt.astimezone(UTC)
    return _check(
        "next start is 05:00Z in winter (06:00 CET)",
        (as_utc.hour, as_utc.minute) == (5, 0),
        f"got {as_utc.isoformat()}",
    )


def test_next_start_rolls_to_tomorrow_once_past() -> bool:
    """Asked at 10:00 Berlin, the next start is tomorrow, not today."""
    now = datetime(2026, 9, 10, 10, 0, tzinfo=BERLIN)
    nxt = main._next_vllm_start(now).astimezone(BERLIN)
    return _check(
        "after the start hour, next start rolls to tomorrow",
        (nxt.day, nxt.hour) == (11, 6),
        f"got {nxt.isoformat()}",
    )


def test_next_start_iso_is_timezone_aware() -> bool:
    """The frontend renders this in the viewer's zone, so a naive string
    would be interpreted as local and silently shift."""
    iso = main._next_vllm_start_iso()
    parsed = datetime.fromisoformat(iso)
    return _check(
        "_next_vllm_start_iso stays tz-aware",
        parsed.tzinfo is not None,
        f"got {iso!r}",
    )


# ---------------------------------------------------------------------------
# The response itself
# ---------------------------------------------------------------------------

def _body(resp) -> dict:
    import json
    return json.loads(bytes(resp.body).decode("utf-8"))


def test_planned_outage_is_503_with_retry_after_to_the_start() -> bool:
    now = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)          # 05:00 Berlin
    resp = main._vllm_unavailable_response(ConnectionRefusedError("boom"), now=now)
    retry = resp.headers.get("retry-after")
    ok = (
        resp.status_code == 503
        and retry is not None
        and retry.isdigit()
        # 05:00 -> 06:00 Berlin is one hour
        and abs(int(retry) - 3600) <= 2
    )
    return _check(
        "planned outage -> 503 + Retry-After counting down to the start",
        ok,
        f"status={resp.status_code} retry-after={retry!r}",
    )


def test_planned_outage_says_it_is_scheduled() -> bool:
    """The message is what an operator reads at 3am. It has to distinguish
    'we turned it off' from 'it crashed'."""
    now = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    body = _body(main._vllm_unavailable_response(ConnectionRefusedError("x"), now=now))
    msg = (body.get("error") or {}).get("message", "")
    return _check(
        "planned-outage body names the schedule and the return time",
        "scheduled" in msg.lower() and body["error"].get("next_start"),
        f"got {msg!r}",
    )


def test_unplanned_outage_gets_a_short_retry() -> bool:
    """vLLM down at 14:00 is a crash or a requeue, not the window. Handing
    back 16 hours of Retry-After would park a client until tomorrow for what
    may clear in a minute."""
    now = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)         # 14:00 Berlin
    resp = main._vllm_unavailable_response(ConnectionRefusedError("x"), now=now)
    retry = int(resp.headers["retry-after"])
    body = _body(resp)
    return _check(
        "unplanned outage -> 503 with a short retry, not the schedule",
        resp.status_code == 503 and retry <= 300
        and "scheduled" not in body["error"]["message"].lower(),
        f"retry-after={retry}",
    )


def test_error_body_is_openai_shaped() -> bool:
    """Positron / Cursor / the OpenAI SDK parse `error.message`; a bare string
    or a Munin-only shape surfaces to the user as 'error making request'."""
    now = datetime(2026, 9, 10, 3, 0, tzinfo=UTC)
    body = _body(main._vllm_unavailable_response(ConnectionRefusedError("x"), now=now))
    err = body.get("error")
    return _check(
        "error envelope keeps the OpenAI shape",
        isinstance(err, dict) and isinstance(err.get("message"), str)
        and err.get("type") == "service_unavailable",
        f"got {body!r}",
    )


# ---------------------------------------------------------------------------
# The call site: a refused connection must not become a 500
# ---------------------------------------------------------------------------

def _raw_proxy_with_refused_connection(stream: bool) -> object:
    """Drive _raw_chat_proxy with an httpx client whose every request raises
    ConnectError, which is exactly what a stopped vLLM produces."""
    import asyncio
    import httpx
    from unittest.mock import patch

    class _RefusingClient:
        def __init__(self, *a, **kw):
            pass

        def build_request(self, *a, **kw):
            return object()

        async def post(self, *a, **kw):
            raise httpx.ConnectError("[Errno 111] Connection refused")

        async def send(self, *a, **kw):
            raise httpx.ConnectError("[Errno 111] Connection refused")

        async def aclose(self):
            return None

    body = {
        "messages": [{"role": "user", "content": "hi"}],
        "stream": stream,
    }

    class _Req:
        headers: dict = {}

    with patch.object(httpx, "AsyncClient", _RefusingClient):
        return asyncio.run(main._raw_chat_proxy(_Req(), body, "u@x"))


def test_refused_connection_is_503_not_500_nonstreaming() -> bool:
    resp = _raw_proxy_with_refused_connection(stream=False)
    return _check(
        "raw proxy, stream=False: refused connection -> 503 (was an uncaught 500)",
        resp.status_code == 503 and "retry-after" in
        {k.lower() for k in resp.headers.keys()},
        f"status={resp.status_code} headers={dict(resp.headers)}",
    )


def test_refused_connection_is_503_not_500_streaming() -> bool:
    """The streaming half opened the upstream with no guard at all, and also
    leaked the httpx client when that raised."""
    resp = _raw_proxy_with_refused_connection(stream=True)
    return _check(
        "raw proxy, stream=True: refused connection -> 503, not a 200 SSE",
        resp.status_code == 503,
        f"status={resp.status_code}",
    )


TESTS = [
    test_window_covers_the_measured_outage,
    test_window_is_evaluated_in_cluster_time_not_container_time,
    test_next_start_is_the_real_start_not_two_hours_late,
    test_next_start_follows_dst,
    test_next_start_rolls_to_tomorrow_once_past,
    test_next_start_iso_is_timezone_aware,
    test_planned_outage_is_503_with_retry_after_to_the_start,
    test_planned_outage_says_it_is_scheduled,
    test_unplanned_outage_gets_a_short_retry,
    test_error_body_is_openai_shaped,
    test_refused_connection_is_503_not_500_nonstreaming,
    test_refused_connection_is_503_not_500_streaming,
]


def main_() -> int:
    failures = 0
    for t in TESTS:
        try:
            if not t():
                failures += 1
        except Exception:
            failures += 1
            print(f"[FAIL] {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failures}/{len(TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main_())
