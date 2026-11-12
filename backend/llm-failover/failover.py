"""Primary/secondary routing state for the LLM failover proxy. No I/O in here.

The primary is the cluster's vLLM, the secondary is llm-claude. Health comes
from two sources: an active probe every few seconds and passive observations
from real requests (a refused connection or a 502/503/504). Both feed the same
streaks, so a dead vLLM is noticed by the first failed request rather than at
the next probe.

Hysteresis, so a vLLM that is restarting or switching models does not make the
route flap:

- primary -> secondary after FAIL_AFTER consecutive primary failures;
- secondary -> primary only after RECOVER_AFTER consecutive primary successes
  AND at least MIN_HOLD_S on the secondary. A vLLM that comes back for one
  probe and dies again never takes traffic back.

Per-request fallback is separate from the route: while the route is still
primary, a request whose primary attempt fails before any byte reached the
caller is retried once on the secondary (when it is usable), without moving
the route. The route moves only on the streaks.

The secondary can be unusable for three reasons, each visible in `status()`:
it is switched off by configuration, its last probe failed, or it is blocked
(Anthropic's spend cap answered 402, or the proxy's own daily token budget
ran out). With no usable secondary every request goes to the primary, which
fails fast, and retrieval's status page shows the usual "sleeping" screen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

PRIMARY = "primary"
SECONDARY = "secondary"


@dataclass
class UpstreamHealth:
    ok_streak: int = 0
    fail_streak: int = 0
    last_ok: Optional[bool] = None  # None until the first observation
    last_change_wall: Optional[float] = None
    last_error: Optional[str] = None

    def observe(self, ok: bool, wall: float, error: Optional[str] = None) -> None:
        if ok:
            self.ok_streak += 1
            self.fail_streak = 0
            self.last_error = None
        else:
            self.fail_streak += 1
            self.ok_streak = 0
            self.last_error = error
        if self.last_ok is not ok:
            self.last_change_wall = wall
        self.last_ok = ok


@dataclass
class DailyBudget:
    """Tokens the secondary may spend per UTC day. 0 means unlimited.

    Counted from the `usage` the secondary returns (prompt + completion), which
    is the same number the gateway's quotas use. It is a ceiling on a runaway,
    not a bill: the Anthropic Console has the real spend, and the workspace
    spend limit (a 402) is the hard stop.
    """

    limit: int = 0
    day: str = ""
    used: int = 0
    wall: Callable[[], float] = time.time

    def _roll(self) -> None:
        today = datetime.fromtimestamp(self.wall(), timezone.utc).strftime("%Y-%m-%d")
        if today != self.day:
            self.day, self.used = today, 0

    def add(self, tokens: int) -> None:
        self._roll()
        self.used += max(0, int(tokens))

    def exhausted(self) -> bool:
        self._roll()
        return bool(self.limit) and self.used >= self.limit

    def seconds_to_reset(self) -> float:
        now = self.wall()
        tomorrow = (int(now) // 86400 + 1) * 86400
        return tomorrow - now


@dataclass
class FailoverState:
    fail_after: int = 2
    recover_after: int = 6
    min_hold_s: float = 60.0
    secondary_enabled: bool = True
    budget: DailyBudget = field(default_factory=DailyBudget)
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time

    route: str = PRIMARY
    switched_at: Optional[float] = None  # monotonic, for the hold
    switched_at_wall: Optional[float] = None  # for display
    switch_reason: Optional[str] = None
    blocked_until: float = 0.0  # monotonic; spend cap
    blocked_reason: Optional[str] = None
    transitions: dict = field(default_factory=lambda: {PRIMARY: 0, SECONDARY: 0})

    def __post_init__(self) -> None:
        if self.fail_after < 1 or self.recover_after < 1:
            raise ValueError("fail_after and recover_after must be >= 1")
        self.primary = UpstreamHealth()
        self.secondary = UpstreamHealth()

    # ── secondary usability ─────────────────────────────────────────────────

    def secondary_block_reason(self) -> Optional[str]:
        """Why the secondary cannot take a request now, or None if it can."""
        if not self.secondary_enabled:
            return "disabled"
        if self.clock() < self.blocked_until:
            return self.blocked_reason or "blocked"
        if self.budget.exhausted():
            return "daily_budget"
        if self.secondary.last_ok is False:
            return "unhealthy"
        return None

    def secondary_usable(self) -> bool:
        return self.secondary_block_reason() is None

    def block_secondary(self, seconds: float, reason: str) -> None:
        self.blocked_until = self.clock() + seconds
        self.blocked_reason = reason

    # ── observations ────────────────────────────────────────────────────────

    def observe_primary(self, ok: bool, error: Optional[str] = None) -> Optional[str]:
        """Record a primary probe or request outcome. Returns the new route
        when this observation moved it, else None."""
        self.primary.observe(ok, self.wall(), error)
        return self._evaluate()

    def observe_secondary(self, ok: bool, error: Optional[str] = None) -> Optional[str]:
        self.secondary.observe(ok, self.wall(), error)
        return self._evaluate()

    def _move(self, to: str, reason: str) -> str:
        self.route = to
        self.switched_at = self.clock()
        self.switched_at_wall = self.wall()
        self.switch_reason = reason
        self.transitions[to] += 1
        return to

    def _evaluate(self) -> Optional[str]:
        if self.route == PRIMARY:
            if self.primary.fail_streak >= self.fail_after and self.secondary_usable():
                return self._move(SECONDARY, self.primary.last_error or "primary failing")
            return None
        held = self.switched_at is None or self.clock() - self.switched_at >= self.min_hold_s
        if self.primary.ok_streak >= self.recover_after and held:
            return self._move(PRIMARY, "primary healthy again")
        return None

    def tick(self) -> Optional[str]:
        """Re-evaluate without a new observation (the hold may have expired)."""
        return self._evaluate()

    # ── routing ─────────────────────────────────────────────────────────────

    def plan(self) -> list[str]:
        """Upstreams to try for one request, in order. The second entry is
        used only when the first fails before any byte reached the caller."""
        if self.route == SECONDARY and self.secondary_usable():
            # Fall back to the primary only if it is answering probes again,
            # so a broken secondary does not hammer a dead vLLM.
            return [SECONDARY, PRIMARY] if self.primary.last_ok else [SECONDARY]
        if self.secondary_usable():
            return [PRIMARY, SECONDARY]
        return [PRIMARY]

    def fallback_active(self) -> bool:
        return self.route == SECONDARY and self.secondary_usable()

    def status(self) -> dict:
        def iso(ts: Optional[float]) -> Optional[str]:
            if ts is None:
                return None
            return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")

        return {
            "route": self.route,
            "fallback_active": self.fallback_active(),
            "since": iso(self.switched_at_wall),
            "reason": self.switch_reason,
            "secondary_block": self.secondary_block_reason(),
            "primary": {
                "ok": self.primary.last_ok,
                "ok_streak": self.primary.ok_streak,
                "fail_streak": self.primary.fail_streak,
                "last_error": self.primary.last_error,
                "since": iso(self.primary.last_change_wall),
            },
            "secondary": {
                "ok": self.secondary.last_ok,
                "last_error": self.secondary.last_error,
                "tokens_today": self.budget.used,
                "daily_token_budget": self.budget.limit or None,
            },
            "transitions": dict(self.transitions),
        }
