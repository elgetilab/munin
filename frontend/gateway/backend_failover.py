"""Which backend the gateway sends /api/* and /v1/* to: the cluster (primary,
through the reverse SSH tunnel) or the standby Lite backend on this VPS.

Off unless BACKEND_STANDBY_URL is set; then the gateway behaves exactly as
before. No I/O in this module except the SQLite row that remembers a reduced
episode across gateway restarts, so the state machine is testable with a fake
clock (tests/test_backend_failover.py).

Rules (docs/ops/HAIKU-FALLBACK.md, layer 2):

- One owner of writes at a time: every request goes to the active backend.
  Nothing is written to both, and nothing is retried on the other backend,
  because a chat saved on the wrong side is worse than one failed request.
- Normal -> reduced after FAIL_AFTER consecutive failed probes of the cluster,
  and only if the standby answers its own probe.
- Reduced -> normal after RECOVER_AFTER consecutive good cluster probes AND
  MIN_HOLD_S in reduced mode. With BACKEND_FAILBACK=manual it waits for an
  admin instead (POST /api/backend-mode), for operators who want to run the
  merge before users see the cluster's history again.
- Each reduced episode is recorded (start, end). Its start is the `since`
  cutoff for merging the standby's chats back into the cluster.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

NORMAL = "normal"
REDUCED = "reduced"


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


@dataclass
class BackendFailover:
    fail_after: int = 3
    recover_after: int = 12
    min_hold_s: float = 300.0
    auto_failback: bool = True
    clock: Callable[[], float] = time.monotonic
    wall: Callable[[], float] = time.time

    mode: str = NORMAL
    since_wall: Optional[float] = None
    entered_mono: Optional[float] = None
    reason: Optional[str] = None
    primary_ok_streak: int = 0
    primary_fail_streak: int = 0
    primary_last_ok: Optional[bool] = None
    standby_last_ok: Optional[bool] = None
    primary_error: Optional[str] = None
    pinned: bool = False  # an admin forced the mode; probes do not move it
    events: list = field(default_factory=list)  # (kind, wall) for the caller to persist/log

    def observe_primary(self, ok: bool, error: Optional[str] = None) -> Optional[str]:
        if ok:
            self.primary_ok_streak += 1
            self.primary_fail_streak = 0
            self.primary_error = None
        else:
            self.primary_fail_streak += 1
            self.primary_ok_streak = 0
            self.primary_error = error
        self.primary_last_ok = ok
        return self._evaluate()

    def observe_standby(self, ok: bool) -> Optional[str]:
        self.standby_last_ok = ok
        return self._evaluate()

    def tick(self) -> Optional[str]:
        return self._evaluate()

    def _enter(self, mode: str, reason: str, since_wall: Optional[float] = None) -> str:
        self.mode = mode
        self.reason = reason
        self.entered_mono = self.clock()
        if mode == REDUCED:
            self.since_wall = since_wall if since_wall is not None else self.wall()
        self.events.append((mode, self.wall()))
        return mode

    def _evaluate(self) -> Optional[str]:
        if self.pinned:
            return None
        if self.mode == NORMAL:
            if self.primary_fail_streak >= self.fail_after and self.standby_last_ok:
                return self._enter(REDUCED, self.primary_error or "cluster unreachable")
            return None
        if not self.auto_failback:
            return None
        held = self.entered_mono is None or self.clock() - self.entered_mono >= self.min_hold_s
        if self.primary_ok_streak >= self.recover_after and held:
            return self._enter(NORMAL, "cluster reachable again")
        return None

    def force(self, mode: str, reason: str) -> Optional[str]:
        """Admin override. Pins the mode until `release()`."""
        self.pinned = True
        if mode == self.mode:
            return None
        return self._enter(mode, reason)

    def release(self) -> Optional[str]:
        self.pinned = False
        return self._evaluate()

    def restore(self, mode: str, since_wall: Optional[float], pinned: bool) -> None:
        """Resume a persisted state after a gateway restart. A reduced episode
        keeps its original start and must re-earn the hold before failing back,
        so a restart cannot cause a flap."""
        self.mode = mode
        self.since_wall = since_wall if mode == REDUCED else None
        self.entered_mono = self.clock()
        self.pinned = pinned
        self.reason = "restored after gateway restart"

    def use_standby(self) -> bool:
        return self.mode == REDUCED

    def status(self) -> dict:
        return {
            "mode": self.mode,
            "since": iso(self.since_wall) if self.mode == REDUCED else None,
            "reason": self.reason,
            "pinned": self.pinned,
            "auto_failback": self.auto_failback,
            "cluster": {"ok": self.primary_last_ok, "ok_streak": self.primary_ok_streak,
                        "fail_streak": self.primary_fail_streak, "last_error": self.primary_error},
            "standby": {"ok": self.standby_last_ok},
        }


# ── persistence (gateway.db) ─────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS backend_mode (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL,
    since REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backend_episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    reason TEXT
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def load(conn: sqlite3.Connection, state: BackendFailover) -> None:
    row = conn.execute("SELECT mode, since, pinned FROM backend_mode WHERE id = 1").fetchone()
    if row and row[0] in (NORMAL, REDUCED):
        state.restore(row[0], row[1], bool(row[2]))


def save(conn: sqlite3.Connection, state: BackendFailover) -> None:
    """Write the current mode and turn pending events into episode rows."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO backend_mode (id, mode, since, pinned, updated_at) VALUES (1, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET mode = excluded.mode, since = excluded.since, "
        "pinned = excluded.pinned, updated_at = excluded.updated_at",
        (state.mode, state.since_wall if state.mode == REDUCED else None, int(state.pinned), now),
    )
    for kind, wall in state.events:
        if kind == REDUCED:
            conn.execute("INSERT INTO backend_episodes (started_at, reason) VALUES (?, ?)",
                         (iso(wall), state.reason))
        else:
            conn.execute("UPDATE backend_episodes SET ended_at = ? WHERE ended_at IS NULL", (iso(wall),))
    state.events.clear()
    conn.commit()


def episodes(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    rows = conn.execute(
        "SELECT id, started_at, ended_at, reason FROM backend_episodes ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [{"id": r[0], "started_at": r[1], "ended_at": r[2], "reason": r[3]} for r in rows]
