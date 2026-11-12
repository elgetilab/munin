"""The routing state machine: thresholds, hysteresis, blocks, budget."""
import pytest

from failover import PRIMARY, SECONDARY, DailyBudget, FailoverState


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def make(**kw):
    clock = Clock()
    wall = Clock(1_800_000_000.0)
    budget = kw.pop("budget", None) or DailyBudget(wall=wall)
    s = FailoverState(clock=clock, wall=wall, budget=budget, **kw)
    s.observe_secondary(True)
    return s, clock, wall


def test_starts_on_primary_and_plans_fallback():
    s, _, _ = make()
    assert s.route == PRIMARY
    assert s.plan() == [PRIMARY, SECONDARY]
    assert not s.fallback_active()


def test_single_failure_does_not_move_route():
    s, _, _ = make(fail_after=2)
    assert s.observe_primary(False, "ConnectError") is None
    assert s.route == PRIMARY


def test_fail_after_consecutive_failures_moves_to_secondary():
    s, _, _ = make(fail_after=2)
    s.observe_primary(False)
    assert s.observe_primary(False, "ConnectError") == SECONDARY
    assert s.fallback_active()
    assert s.status()["reason"] == "ConnectError"
    assert s.transitions[SECONDARY] == 1


def test_success_between_failures_resets_streak():
    s, _, _ = make(fail_after=2)
    s.observe_primary(False)
    s.observe_primary(True)
    s.observe_primary(False)
    assert s.route == PRIMARY


def test_no_failover_when_secondary_unusable():
    s, _, _ = make(fail_after=1)
    s.observe_secondary(False, "probe ConnectError")
    s.observe_primary(False)
    assert s.route == PRIMARY
    assert s.plan() == [PRIMARY]
    # Secondary comes back: the standing primary failure streak now moves it.
    assert s.observe_secondary(True) == SECONDARY


def test_recovery_needs_streak_and_hold():
    s, clock, _ = make(fail_after=1, recover_after=3, min_hold_s=60)
    s.observe_primary(False)
    assert s.route == SECONDARY
    for _ in range(5):
        s.observe_primary(True)
    assert s.route == SECONDARY, "hold not yet expired"
    clock.t += 61
    assert s.tick() == PRIMARY
    assert s.transitions == {PRIMARY: 1, SECONDARY: 1}


def test_flapping_primary_never_takes_traffic_back():
    s, clock, _ = make(fail_after=1, recover_after=3, min_hold_s=0)
    s.observe_primary(False)
    for _ in range(10):
        s.observe_primary(True)
        s.observe_primary(True)
        s.observe_primary(False)
        clock.t += 30
    assert s.route == SECONDARY
    assert s.transitions[PRIMARY] == 0


def test_on_secondary_plan_tries_primary_only_if_probe_ok():
    s, _, _ = make(fail_after=1)
    s.observe_primary(False)
    assert s.plan() == [SECONDARY]
    s.observe_primary(True)
    assert s.plan() == [SECONDARY, PRIMARY]


def test_spend_cap_block_expires():
    s, clock, _ = make()
    s.block_secondary(3600, "spend_cap")
    assert s.secondary_block_reason() == "spend_cap"
    assert s.plan() == [PRIMARY]
    clock.t += 3601
    assert s.secondary_usable()


def test_block_while_on_secondary_sends_everything_to_primary():
    s, _, _ = make(fail_after=1)
    s.observe_primary(False)
    s.block_secondary(60, "spend_cap")
    assert s.route == SECONDARY
    assert not s.fallback_active()
    assert s.plan() == [PRIMARY]


def test_disabled_secondary():
    s, _, _ = make(secondary_enabled=False, fail_after=1)
    s.observe_primary(False)
    assert s.route == PRIMARY
    assert s.secondary_block_reason() == "disabled"


def test_daily_budget_rolls_at_utc_midnight():
    wall = Clock(1_800_000_000.0)  # 2027-01-15 08:00 UTC
    b = DailyBudget(limit=100, wall=wall)
    b.add(60)
    assert not b.exhausted()
    b.add(50)
    assert b.exhausted()
    wall.t += b.seconds_to_reset() + 1
    assert not b.exhausted()
    assert b.used == 0


def test_budget_exhaustion_blocks_secondary():
    s, _, _ = make(budget=DailyBudget(limit=10))
    s.budget.add(10)
    assert s.secondary_block_reason() == "daily_budget"


def test_status_shape():
    s, _, _ = make(fail_after=1)
    s.observe_primary(False, "HTTP 503")
    st = s.status()
    assert st["route"] == SECONDARY and st["fallback_active"] is True
    assert st["since"].endswith("+00:00")
    assert st["primary"]["last_error"] == "HTTP 503"


def test_rejects_bad_thresholds():
    with pytest.raises(ValueError):
        FailoverState(fail_after=0)
