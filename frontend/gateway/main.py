"""
API Gateway — proxy, API key validation, rate limiting, usage logging.

Sits between Caddy and the cluster tunnel. Handles:
- Proxying requests to 127.0.0.1:18080 (cluster tunnel)
- API key validation (sk-munin-... Bearer tokens)
- Per-user monthly token quota (API-key usage only; browser chat is free)
- Usage logging (metadata only, never message content)
"""

import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import asyncio

import httpx
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

import backend_failover
from backend_failover import NORMAL, REDUCED
from models import CreateKeyRequest, CreateKeyResponse, KeyInfo

# ── Configuration ────────────────────────────────────────────────────────────

CLUSTER_TUNNEL = os.environ.get("CLUSTER_TUNNEL", "http://127.0.0.1:18080")
# Admin detection is now via X-Munin-Role header from munin-auth (whitelist.csv role column)
# ADMIN_EMAILS kept as fallback for API key auth (no forward-auth headers)
ADMIN_EMAILS = {e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",")
                if e.strip()}
DB_PATH = Path(os.environ.get("DB_PATH", "/data/gateway.db"))
QUOTAS_PATH = Path(os.environ.get("QUOTAS_PATH", "/data/quotas.yml"))

def is_admin(request: Request) -> bool:
    """Check admin status via X-Munin-Role header (from whitelist.csv) or ADMIN_EMAILS fallback."""
    if request.headers.get("X-Munin-Role") == "admin":
        return True
    email = request.headers.get("X-Munin-Email", "")
    return email in ADMIN_EMAILS


def header_safe(value: str) -> str:
    """Make a header value safe to hand to httpx.

    httpx normalises str header values with .encode("ascii"), so a single
    non-ASCII character raises UnicodeEncodeError. That exception used to
    surface as a blanket 502 "Backend unavailable." on EVERY authenticated
    /api/* request for any user whose display name carried an accent:
    a name like "Erika Mösermann" broke the whole session, /api/status included.

    Non-ASCII characters are dropped rather than transliterated: the only
    header this applies to is X-Munin-Name, which no upstream code reads
    (it's documented in backend/CLAUDE.md but has no consumer), so exact
    fidelity buys nothing and a transliteration table is a dependency and
    a wrong-guess risk for scripts we don't handle. If the name header
    ever grows a real consumer, revisit this.
    """
    return value.encode("ascii", "ignore").decode("ascii")


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("api-gateway")

app = FastAPI(docs_url=None, redoc_url=None)
# Shared secret the backend checks on every request that carries a forwarded
# identity header (backend/retrieval/gateway_token_guard.py). Unset: not sent.
MUNIN_GATEWAY_TOKEN = os.environ.get("MUNIN_GATEWAY_TOKEN", "").strip()
http_client = httpx.AsyncClient(
    base_url=CLUSTER_TUNNEL, timeout=300.0,
    headers={"X-Munin-Gateway-Token": MUNIN_GATEWAY_TOKEN} if MUNIN_GATEWAY_TOKEN else None,
)


def _env_num(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    return float(raw) if raw else default


# ── Standby backend (layer 2 of docs/ops/HAIKU-FALLBACK.md) ─────────────────
# Unset: no standby, no probe, every request goes to CLUSTER_TUNNEL as before.
# Set (the Lite backend on this VPS, e.g. http://127.0.0.1:18081, a different
# port from the tunnel's 18080): the gateway probes both and serves from the
# standby while the cluster is unreachable. See backend_failover.py.
BACKEND_STANDBY_URL = os.environ.get("BACKEND_STANDBY_URL", "").strip()
standby_client = httpx.AsyncClient(
    base_url=BACKEND_STANDBY_URL, timeout=300.0,
    headers={"X-Munin-Gateway-Token": MUNIN_GATEWAY_TOKEN} if MUNIN_GATEWAY_TOKEN else None,
) if BACKEND_STANDBY_URL else None
BACKEND_PROBE_INTERVAL_S = _env_num("BACKEND_PROBE_INTERVAL_S", 5)
backend_state = backend_failover.BackendFailover(
    fail_after=int(_env_num("BACKEND_FAIL_AFTER", 3)),
    recover_after=int(_env_num("BACKEND_RECOVER_AFTER", 12)),
    min_hold_s=_env_num("BACKEND_MIN_HOLD_S", 300),
    auto_failback=os.environ.get("BACKEND_FAILBACK", "auto").strip().lower() != "manual",
)
REDUCED_NOTE = os.environ.get(
    "BACKEND_REDUCED_NOTE",
    "Reduced mode: the main Munin server cannot be reached, so a standby is "
    "answering. Chats from before {since} are not shown and will be back when "
    "the main server returns. Paper search is off, and uploads wait in the "
    "queue until then.",
)


def backend_client() -> httpx.AsyncClient:
    """The client for the backend that owns requests right now."""
    if standby_client is not None and backend_state.use_standby():
        return standby_client
    return http_client


def reduced_block() -> dict | None:
    if standby_client is None or not backend_state.use_standby():
        return None
    since = backend_failover.iso(backend_state.since_wall)
    shown = since[:16].replace("T", " ") + " UTC" if since else "the outage"
    return {"mode": REDUCED, "since": since, "history_note": REDUCED_NOTE.format(since=shown)}


def with_backend_block(content: bytes) -> bytes:
    """/api/status from the standby, with the `backend` block the UI's
    reduced-mode banner reads. Unchanged if it is not a JSON object."""
    block = reduced_block()
    if block is None:
        return content
    try:
        payload = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content
    if not isinstance(payload, dict):
        return content
    payload["backend"] = block
    return json.dumps(payload).encode()


async def _probe(client: httpx.AsyncClient) -> tuple[bool, str | None]:
    try:
        r = await client.get("/health", timeout=3.0)
        return r.status_code == 200, (None if r.status_code == 200 else f"HTTP {r.status_code}")
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def _persist_backend_state(moved: str | None) -> None:
    if moved == REDUCED:
        log.warning("backend failover: REDUCED mode, serving from standby %s (reason: %s)",
                    BACKEND_STANDBY_URL, backend_state.reason)
    elif moved == NORMAL:
        log.warning("backend failover: NORMAL mode, cluster serving again. Merge the standby's "
                    "chats since %s into the cluster (docs/ops/HAIKU-FALLBACK.md)",
                    backend_failover.iso(backend_state.since_wall))
    if backend_state.events or moved:
        conn = get_db()
        try:
            backend_failover.save(conn, backend_state)
        finally:
            conn.close()


async def backend_probe_once() -> None:
    (p_ok, p_err), (s_ok, _) = await asyncio.gather(_probe(http_client), _probe(standby_client))
    moved = backend_state.observe_standby(s_ok)
    moved = backend_state.observe_primary(p_ok, p_err) or moved
    moved = backend_state.tick() or moved
    _persist_backend_state(moved)


async def backend_probe_loop() -> None:
    while True:
        try:
            await backend_probe_once()
        except Exception:
            log.exception("backend probe loop error")
        await asyncio.sleep(BACKEND_PROBE_INTERVAL_S)

# Requests-per-minute and concurrent-request limits were REMOVED 2026-08-25.
# They existed because vLLM served only --max-num-seqs 2, so a single scripted
# client could starve everyone else. The TP=2 profile serves 8 (910,222 KV
# tokens, 13.89x worst-case at 64k), which is enough that per-user gating is no
# longer the right place to shed load; vLLM's own scheduler queues instead.
# The MONTHLY TOKEN QUOTA is deliberately kept - it is a cost control, not a
# concurrency control, and nothing else enforces it.


# ── Quotas ───────────────────────────────────────────────────────────────────

DEFAULT_QUOTAS = {
    "tokens_per_month": 2_000_000,
}

quotas_config: dict = {"default": DEFAULT_QUOTAS, "overrides": {}}


def load_quotas():
    global quotas_config
    if QUOTAS_PATH.exists():
        with open(QUOTAS_PATH, encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        quotas_config = {
            "default": {**DEFAULT_QUOTAS, **(loaded.get("default") or {})},
            "overrides": loaded.get("overrides") or {},
        }
    log.info(f"Quotas loaded: default={quotas_config['default']}, overrides={len(quotas_config['overrides'])}")


def get_user_quotas(email: str) -> dict:
    overrides = quotas_config.get("overrides", {}).get(email, {})
    return {**quotas_config["default"], **overrides}


# ── Database ─────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL UNIQUE,
            key_prefix TEXT NOT NULL,
            user_email TEXT NOT NULL,
            name TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            revoked_at TEXT
        );

        CREATE TABLE IF NOT EXISTS usage_log (
            id TEXT PRIMARY KEY,
            user_email TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            persona TEXT,
            source TEXT NOT NULL,
            api_key_id TEXT,
            endpoint TEXT NOT NULL,
            tokens_input INTEGER,
            tokens_output INTEGER,
            tokens_total INTEGER,
            tools_invoked TEXT,
            duration_ms INTEGER,
            status_code INTEGER,
            model TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_usage_user_month ON usage_log(user_email, timestamp);
        CREATE INDEX IF NOT EXISTS idx_keys_hash ON api_keys(key_hash);

        CREATE TABLE IF NOT EXISTS announcements (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            message TEXT NOT NULL,
            level TEXT NOT NULL DEFAULT 'info',
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_activity (
            email TEXT PRIMARY KEY,
            last_seen_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'browser'
        );
    """)
    # user_activity.email (like usage_log.user_email and api_keys.user_email)
    # holds the user KEY; address is the last address seen for that user,
    # so the admin views can show a person rather than "u12".
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(user_activity)")]
    if "address" not in cols:
        conn.execute("ALTER TABLE user_activity ADD COLUMN address TEXT")
    # Added 2026-11 for the publication-usage summary: the caller's primary
    # group (X-Munin-Group) and the conversation a turn belonged to.
    ucols = [r["name"] for r in conn.execute("PRAGMA table_info(usage_log)")]
    for col in ("group_id", "conversation_id"):
        if col not in ucols:
            conn.execute(f"ALTER TABLE usage_log ADD COLUMN {col} TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_group_time ON usage_log(group_id, timestamp)")
    conn.commit()
    conn.close()


# ── API Key Helpers ──────────────────────────────────────────────────────────

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def validate_api_key(bearer_token: str) -> dict | None:
    """Validate a Bearer token. Returns {id, email, name} or None."""
    if not bearer_token.startswith("sk-munin-"):
        return None

    key_hash = hash_key(bearer_token)
    conn = get_db()
    row = conn.execute(
        "SELECT id, user_email, name FROM api_keys WHERE key_hash = ? AND revoked_at IS NULL",
        (key_hash,),
    ).fetchone()

    if row:
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                      (datetime.now(timezone.utc).isoformat(), row["id"]))
        conn.commit()
    conn.close()

    if not row:
        return None
    return {"id": row["id"], "email": row["user_email"], "name": row["name"]}


# ── Auth Resolution ──────────────────────────────────────────────────────────
#
# Who is calling, as two things: `key`, the user key every table here (and
# every backend table) stores per-user data under ("u12", X-Munin-User from
# munin-auth), and `email`, the user's primary address, used only where an
# address is the point: quotas.yml overrides, ADMIN_EMAILS, display. A caller
# without X-Munin-User (scripts, benchmarks, an API key made before user keys)
# is keyed by its address, as before; "u12" and an address cannot collide.

_USER_KEY_RE = re.compile(r"^u\d+$")
AUTH_CHECK_URL = os.environ.get("AUTH_CHECK_URL", "http://127.0.0.1:8090/admin/check-role")
KB_GATE_TOKEN = os.environ.get("KB_GATE_TOKEN", "")
OWNER_CACHE_TTL = 60.0
_owner_cache: dict[str, tuple[float, "Caller | None"]] = {}


@dataclass
class Caller:
    key: str
    email: str
    name: str
    role: str | None = None
    # Primary group from munin-auth (X-Munin-Group); browser sessions only.
    group: str | None = None


def browser_caller(request: Request) -> Caller | None:
    """The caller as munin-auth's forward-auth headers describe it."""
    email = (request.headers.get("X-Munin-Email") or "").strip()
    key = (request.headers.get("X-Munin-User") or "").strip() or email
    if not key:
        return None
    address = email or key
    return Caller(
        key=key,
        email=address,
        name=request.headers.get("X-Munin-Name") or address.split("@")[0],
        role=request.headers.get("X-Munin-Role") or None,
        group=(request.headers.get("X-Munin-Group") or "").strip() or None,
    )


async def _ask_auth(owner: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=5.0) as c:
        return await c.get(AUTH_CHECK_URL, params={"email": owner},
                           headers={"Authorization": f"Bearer {KB_GATE_TOKEN}"})


async def resolve_key_owner(owner: str) -> Caller | None:
    """An API key's owner as a Caller. Keys store the owner's user key; the
    address, name and role come from munin-auth (cached briefly), so quotas
    and ADMIN_EMAILS see the same address a browser session would. None when
    auth says the user no longer exists: their keys stop working. If auth
    cannot be asked, the key still works, keyed correctly, with the key
    standing in for the address."""
    if not _USER_KEY_RE.match(owner):
        return Caller(key=owner, email=owner, name=owner.split("@")[0])
    hit = _owner_cache.get(owner)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    try:
        r = await _ask_auth(owner)
    except httpx.HTTPError as e:
        log.warning("owner lookup for %s failed: %s: %s", owner, type(e).__name__, e)
        return Caller(key=owner, email=owner, name=owner)
    if r.status_code == 404:
        found = None
    elif r.status_code == 200:
        body = r.json()
        address = body.get("email") or owner
        found = Caller(key=owner, email=address,
                       name=body.get("name") or address.split("@")[0],
                       role=body.get("role"))
    else:
        log.warning("owner lookup for %s: HTTP %s (is KB_GATE_TOKEN set?)", owner, r.status_code)
        return Caller(key=owner, email=owner, name=owner)
    _owner_cache[owner] = (time.monotonic() + OWNER_CACHE_TTL, found)
    return found


async def resolve_auth(request: Request) -> tuple[Caller | None, str, str | None]:
    """
    Resolve the caller from the request.
    Returns (caller, source, api_key_id), caller None if unauthorized.
    """
    # A Bearer token decides identity whenever one is sent, valid or not, and
    # is checked first: api.<domain> has no forward-auth, so identity headers
    # there come from the client, not from munin-auth. Caddy strips them on
    # that host; this keeps a spoofed header from winning if a request ever
    # reaches the gateway without that strip. Browsers never send a Bearer token.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        key_info = validate_api_key(auth_header[7:].strip())
        if key_info:
            caller = await resolve_key_owner(key_info["email"])
            if caller:
                return caller, "api_key", key_info["id"]
        return None, "unknown", None

    # Set by munin-auth via forward-auth on the browser hosts.
    caller = browser_caller(request)
    if caller:
        return caller, "browser", None
    return None, "unknown", None


# ── Rate Limiting ────────────────────────────────────────────────────────────

def is_admin_email(email: str) -> bool:
    """Check if an email is an admin (for rate-limit bypass on API keys)."""
    if email in ADMIN_EMAILS:
        return True
    overrides = quotas_config.get("overrides", {}).get(email, {})
    return overrides.get("unlimited", False)


def check_rate_limits(caller: Caller) -> dict | None:
    """Check the monthly token quota. Returns an error dict if exceeded, else None.

    Admin users (and anyone with `unlimited: true`) bypass it. Requests/minute
    and concurrent-request limits were removed 2026-08-25; this is now the only
    quota, and it is a COST control rather than a load-shedding one. Keeping the
    function name means every call site and the 429 envelope are unchanged.
    """
    if caller.role == "admin" or is_admin_email(caller.email):
        return None

    limits = get_user_quotas(caller.email)

    # Monthly token limit — only API key usage counts against quota (browser chat is free)
    token_limit = limits["tokens_per_month"]
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as total FROM usage_log WHERE user_email = ? AND timestamp >= ? AND source = 'api_key'",
        (caller.key, month_start),
    ).fetchone()
    conn.close()
    tokens_used = row["total"] if row else 0

    if tokens_used >= token_limit:
        return {
            "code": "rate_limited",
            "message": "Monthly token limit exceeded.",
            "type": "tokens_per_month",
            "retry_after": 3600,
        }

    return None


# ── Usage Logging ────────────────────────────────────────────────────────────

class SseUsageScanner:
    """Reads a relayed SSE stream for what usage_log records.

    Buffers across chunks (an event split between two network reads used to
    lose its usage), tracks the `event:` name, and takes:
      done / any data with `usage`  -> prompt, completion and total tokens
      done.model, or a chunk `model` -> the model that answered
      conversation.id                -> conversation_id
      routing.profile                -> persona
      tool_call.name                 -> tools
    Works for retrieval's named events and for raw OpenAI chunk streams.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._event = ""
        self.tokens_input = 0
        self.tokens_output = 0
        self.tokens_total = 0
        self.model: str | None = None
        self.conversation_id: str | None = None
        self.persona: str | None = None
        self.tools: list[str] = []

    def feed(self, chunk: bytes) -> None:
        try:
            self._buf += chunk.decode("utf-8", "ignore")
        except Exception:
            return
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._line(line.rstrip("\r"))

    def _line(self, line: str) -> None:
        if not line:
            self._event = ""
            return
        if line.startswith("event:"):
            self._event = line[6:].strip()
            return
        if not line.startswith("data:"):
            return
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            return
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(data, dict):
            return
        self.take(data, self._event)

    def take(self, data: dict, event: str = "") -> None:
        usage = data.get("usage")
        if isinstance(usage, dict) and usage:
            self.tokens_input = int(usage.get("prompt_tokens") or 0)
            self.tokens_output = int(usage.get("completion_tokens") or 0)
            self.tokens_total = int(usage.get("total_tokens") or 0) or (
                self.tokens_input + self.tokens_output)
        if isinstance(data.get("model"), str) and data["model"]:
            self.model = data["model"]
        if event == "conversation" and isinstance(data.get("id"), str):
            self.conversation_id = data["id"]
        elif event == "routing" and isinstance(data.get("profile"), str):
            self.persona = data["profile"]
        elif event == "tool_call" and isinstance(data.get("name"), str):
            self.tools.append(data["name"])


def log_usage(
    key: str, source: str, api_key_id: str | None, endpoint: str,
    tokens_input: int = 0, tokens_output: int = 0, tokens_total: int = 0,
    tools_invoked: list[str] | None = None, duration_ms: int = 0,
    status_code: int = 200, model: str | None = None, persona: str | None = None,
    group_id: str | None = None, conversation_id: str | None = None,
):
    if tokens_total == 0 and (tokens_input or tokens_output):
        tokens_total = tokens_input + tokens_output
    conn = get_db()
    conn.execute(
        """INSERT INTO usage_log
           (id, user_email, timestamp, persona, source, api_key_id, endpoint,
            tokens_input, tokens_output, tokens_total, tools_invoked, duration_ms, status_code, model,
            group_id, conversation_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), key, datetime.now(timezone.utc).isoformat(),
            persona, source, api_key_id, endpoint,
            tokens_input, tokens_output, tokens_total,
            json.dumps(tools_invoked) if tools_invoked else None,
            duration_ms, status_code, model, group_id, conversation_id,
        ),
    )
    conn.commit()
    conn.close()


# ── Activity Tracking ────────────────────────────────────────────────────

def track_activity(caller: Caller, source: str):
    """Record that a user was seen (upsert into user_activity)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO user_activity (email, last_seen_at, source, address) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(email) DO UPDATE SET last_seen_at = excluded.last_seen_at, "
        "source = excluded.source, address = excluded.address",
        (caller.key, datetime.now(timezone.utc).isoformat(), source, caller.email),
    )
    conn.commit()
    conn.close()


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    load_quotas()
    if standby_client is not None:
        conn = get_db()
        try:
            backend_failover.init_schema(conn)
            backend_failover.load(conn, backend_state)
        finally:
            conn.close()
        log.info("backend failover on: cluster %s, standby %s, mode %s",
                 CLUSTER_TUNNEL, BACKEND_STANDBY_URL, backend_state.mode)
        if os.environ.get("BACKEND_PROBE_DISABLED") != "1":  # tests drive probes by hand
            app.state.backend_probe = asyncio.create_task(backend_probe_loop())


# ── API Key CRUD ─────────────────────────────────────────────────────────────
# NOTE: These must be defined BEFORE the catch-all /api/{path:path} proxy route.

@app.post("/api/keys")
async def create_key(request: Request, body: CreateKeyRequest):
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    raw_key = f"sk-munin-{secrets.token_hex(16)}"
    key_id = f"key_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, key_prefix, user_email, name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key_id, hash_key(raw_key), raw_key[:16], caller.key, body.name, now),
    )
    conn.commit()
    conn.close()

    return CreateKeyResponse(id=key_id, key=raw_key, key_prefix=raw_key[:16], name=body.name, created_at=now)


@app.get("/api/keys")
async def list_keys(request: Request):
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, key_prefix, name, created_at, last_used_at, revoked_at FROM api_keys WHERE user_email = ? ORDER BY created_at DESC",
        (caller.key,),
    ).fetchall()
    conn.close()

    keys = [
        KeyInfo(
            id=r["id"], key_prefix=r["key_prefix"], name=r["name"] or "",
            created_at=r["created_at"], last_used_at=r["last_used_at"],
            revoked=r["revoked_at"] is not None,
        ).model_dump()
        for r in rows
    ]
    return {"keys": keys}


@app.delete("/api/keys/{key_id}")
async def revoke_key(request: Request, key_id: str):
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    conn = get_db()
    result = conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND user_email = ? AND revoked_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), key_id, caller.key),
    )
    conn.commit()
    conn.close()

    if result.rowcount == 0:
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Key not found."}})
    return {"deleted": True}


# ── Usage Stats ──────────────────────────────────────────────────────────────

@app.get("/api/usage/me")
async def usage_me(request: Request):
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    limits = get_user_quotas(caller.email)
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    conn = get_db()

    # Monthly totals — split by source
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as tokens, COUNT(*) as requests FROM usage_log WHERE user_email = ? AND timestamp >= ?",
        (caller.key, month_start),
    ).fetchone()
    api_row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as tokens, COUNT(*) as requests FROM usage_log WHERE user_email = ? AND timestamp >= ? AND source = 'api_key'",
        (caller.key, month_start),
    ).fetchone()

    # Tool breakdown
    tool_rows = conn.execute(
        "SELECT tools_invoked FROM usage_log WHERE user_email = ? AND timestamp >= ? AND tools_invoked IS NOT NULL",
        (caller.key, month_start),
    ).fetchall()
    tools_used: dict[str, int] = {}
    for tr in tool_rows:
        for tool in json.loads(tr["tools_invoked"]):
            tools_used[tool] = tools_used.get(tool, 0) + 1

    # Per-key stats
    key_rows = conn.execute(
        """SELECT k.key_prefix, k.name, k.last_used_at, COALESCE(SUM(u.tokens_total), 0) as tokens
           FROM api_keys k LEFT JOIN usage_log u ON u.api_key_id = k.id AND u.timestamp >= ?
           WHERE k.user_email = ? AND k.revoked_at IS NULL
           GROUP BY k.id""",
        (month_start, caller.key),
    ).fetchall()
    conn.close()

    api_tokens = api_row["tokens"]
    return {
        "current_month": {
            "tokens_used": api_tokens,
            "tokens_limit": limits["tokens_per_month"],
            "tokens_remaining": max(0, limits["tokens_per_month"] - api_tokens),
            "requests": row["requests"],
            "api_requests": api_row["requests"],
            "chat_tokens": row["tokens"] - api_tokens,
            "tools_used": tools_used,
        },
        "api_keys": [
            {
                "key_prefix": kr["key_prefix"],
                "name": kr["name"] or "",
                "tokens_this_month": kr["tokens"],
                "last_used": kr["last_used_at"],
            }
            for kr in key_rows
        ],
        "is_admin": is_admin(request),
    }


@app.get("/api/usage/admin")
async def usage_admin(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    period = datetime.now(timezone.utc).strftime("%Y-%m")

    conn = get_db()

    totals = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as tokens, COUNT(*) as requests, COUNT(DISTINCT user_email) as users FROM usage_log WHERE timestamp >= ?",
        (month_start,),
    ).fetchone()

    top_users = conn.execute(
        "SELECT user_email, SUM(tokens_total) as tokens, COUNT(*) as requests FROM usage_log WHERE timestamp >= ? GROUP BY user_email ORDER BY tokens DESC",
        (month_start,),
    ).fetchall()

    # Per-user breakdown by source
    per_user_source = conn.execute(
        "SELECT user_email, source, SUM(tokens_total) as tokens, COUNT(*) as requests "
        "FROM usage_log WHERE timestamp >= ? GROUP BY user_email, source ORDER BY tokens DESC",
        (month_start,),
    ).fetchall()

    tool_rows = conn.execute(
        "SELECT tools_invoked FROM usage_log WHERE timestamp >= ? AND tools_invoked IS NOT NULL",
        (month_start,),
    ).fetchall()
    addresses = _known_addresses(conn)
    conn.close()

    tools_usage: dict[str, int] = {}
    for tr in tool_rows:
        for tool in json.loads(tr["tools_invoked"]):
            tools_usage[tool] = tools_usage.get(tool, 0) + 1

    # Build per-user source breakdown
    source_breakdown: dict[str, dict] = {}
    for r in per_user_source:
        email_key = r["user_email"]
        if email_key not in source_breakdown:
            source_breakdown[email_key] = {}
        source_breakdown[email_key][r["source"]] = {"tokens": r["tokens"], "requests": r["requests"]}

    return {
        "period": period,
        "total_tokens": totals["tokens"],
        "total_requests": totals["requests"],
        "active_users": totals["users"],
        "top_users": [
            {
                "email": addresses.get(r["user_email"], r["user_email"]),
                "user": r["user_email"],
                "tokens": r["tokens"],
                "requests": r["requests"],
                "by_source": source_breakdown.get(r["user_email"], {}),
            }
            for r in top_users
        ],
        "tools_usage": tools_usage,
    }


# ── System facts (public) ────────────────────────────────────────────────────
# What the deployment runs, from the backend's /api/system (active model
# profile, embeddings, hosted fallback, model history, references). Public and
# unauthenticated: the docs pages render it through Caddy (`/_facts.json` ->
# here), so nothing in the docs names a model by hand. Cached for a minute and
# kept on disk, so the docs keep the last known facts while the cluster is
# unreachable. Always answers 200: a template include of a failing URL
# would fail the whole docs page.

SYSTEM_FACTS_TTL_S = _env_num("SYSTEM_FACTS_TTL_S", 60)
SYSTEM_FACTS_PATH = Path(os.environ.get("SYSTEM_FACTS_PATH", str(DB_PATH.parent / "system-facts.json")))
_system_cache: dict = {}


def _load_cached_facts() -> dict | None:
    try:
        return json.loads(SYSTEM_FACTS_PATH.read_text())
    except (OSError, ValueError):
        return None


async def get_system_facts() -> dict:
    now = time.monotonic()
    facts = _system_cache.get("facts")
    if facts and now - _system_cache.get("at", 0) < SYSTEM_FACTS_TTL_S:
        return facts
    try:
        r = await backend_client().get("/api/system", timeout=5.0)
        if r.status_code == 200:
            fresh = r.json()
            if isinstance(fresh, dict) and fresh.get("backbone") is not None:
                fresh["available"] = True
                fresh["stale"] = False
                _system_cache.update(facts=fresh, at=now)
                try:
                    tmp = SYSTEM_FACTS_PATH.with_suffix(".tmp")
                    tmp.write_text(json.dumps(fresh))
                    tmp.replace(SYSTEM_FACTS_PATH)
                except OSError as e:
                    log.warning("could not persist system facts: %s", e)
                return fresh
    except Exception as e:
        log.info("system facts fetch failed: %s: %s", type(e).__name__, e)
    old = facts or _load_cached_facts()
    if old:
        old = {**old, "stale": True}
        # Retry the backend at most once a TTL, not on every docs request.
        _system_cache.update(facts=old, at=now)
        return old
    return {"available": False, "stale": True}


def template_safe_json(obj) -> str:
    """JSON with no literal `{{`. Caddy's httpInclude runs the included body
    as a template, so a BibTeX title like `{{Qwen3.8-Max}...` failed the
    whole docs page. `{{` can only occur inside strings in compact JSON (an
    object cannot open with an object), and `{\\u007b` decodes to the same text."""
    return json.dumps(obj, separators=(",", ":")).replace("{{", "{\\u007b")


@app.get("/api/system")
async def api_system():
    facts = await get_system_facts()
    return Response(content=template_safe_json(facts), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=60"})


# ── Publication usage summary ───────────────────────────────────────────────
# "How do I credit Munin in a paper?" needs which model(s) answered, when,
# and roughly how much. usage_log rows carry `model` since 2026-11; older
# rows get the model that history says was serving at their timestamp,
# flagged `inferred`. Deep Research tokens come from the backend (its jobs
# run outside chat turns). Metadata only: no chat content is read.

MUNIN_HOST_LINE = os.environ.get(
    "MUNIN_HOST_LINE",
    "the research assistant hosted by the Elgeti Lab, Leipzig University Medical Faculty")
MUNIN_ACK_LINE = os.environ.get(
    "MUNIN_ACK_LINE",
    "We thank the Elgeti Lab (Leipzig University Medical Faculty) for providing the "
    "Munin research assistant and the compute it runs on.")


def _parse_day(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _ts(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _history_model_at(history: list[dict], ts: str) -> str | None:
    hit = None
    try:
        t = _ts(ts)
        for h in history:
            if _ts(h["from"]) <= t:
                hit = h.get("slug")
            else:
                break
    except (KeyError, ValueError, TypeError):
        return None
    return hit


def is_llm_row(row) -> bool:
    return bool(row["tokens_total"]) or "chat/completions" in (row["endpoint"] or "")


def summarize_usage(rows, dr_jobs: list[dict], facts: dict) -> list[dict]:
    """Per-model usage: requests, conversations, tokens, first/last use."""
    history = sorted(facts.get("history") or [], key=lambda h: h.get("from") or "")
    by: dict[str, dict] = {}

    def slot(model: str) -> dict:
        return by.setdefault(model, {
            "model": model, "requests": 0, "inferred_requests": 0, "conversations": set(),
            "deep_research_jobs": 0, "tokens_input": 0, "tokens_output": 0,
            "tokens_total": 0, "first_use": None, "last_use": None})

    def touch(sl: dict, ts: str) -> None:
        if sl["first_use"] is None or ts < sl["first_use"]:
            sl["first_use"] = ts
        if sl["last_use"] is None or ts > sl["last_use"]:
            sl["last_use"] = ts

    for r in rows:
        if not is_llm_row(r):
            continue
        model, inferred = r["model"], False
        if not model:
            model, inferred = _history_model_at(history, r["timestamp"]) or "unknown", True
        sl = slot(model)
        sl["requests"] += 1
        sl["inferred_requests"] += int(inferred)
        if r["conversation_id"]:
            sl["conversations"].add(r["conversation_id"])
        sl["tokens_input"] += r["tokens_input"] or 0
        sl["tokens_output"] += r["tokens_output"] or 0
        sl["tokens_total"] += r["tokens_total"] or 0
        touch(sl, r["timestamp"])
    for j in dr_jobs:
        try:
            ts = datetime.fromtimestamp(float(j["created_at"]), timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError):
            continue
        model = j.get("model") or _history_model_at(history, ts) or "unknown"
        sl = slot(model)
        sl["deep_research_jobs"] += 1
        sl["tokens_input"] += j.get("tokens_prompt") or 0
        sl["tokens_output"] += j.get("tokens_completion") or 0
        sl["tokens_total"] += j.get("tokens_total") or 0
        touch(sl, ts)
    catalog = facts.get("catalog") or {}
    hist_by_slug = {h.get("slug"): h for h in history}
    out = []
    for model, sl in sorted(by.items(), key=lambda kv: kv[1]["first_use"] or ""):
        info = catalog.get(model) or {}
        h = hist_by_slug.get(model) or {}
        sl["conversations"] = len(sl["conversations"])
        sl.update({
            "display": info.get("display") or h.get("display") or model,
            "developer": info.get("developer"),
            "checkpoint": info.get("checkpoint") or h.get("model_id"),
            "revision": info.get("revision") or h.get("revision"),
            "quantization": info.get("quantization"),
            "vllm_version": info.get("vllm_version") or h.get("vllm"),
            "hosted_by": info.get("hosted_by") or h.get("hosted"),
            "reference": info.get("reference") or None,
        })
        out.append(sl)
    return out


def _day(ts: str | None) -> str:
    return (ts or "")[:10]


def _span(m: dict) -> str:
    a, b = _day(m["first_use"]), _day(m["last_use"])
    return f"on {a}" if a == b else f"between {a} and {b}"


def build_statement(models: list[dict], facts: dict) -> dict:
    """Disclosure text for Methods, a writing-only declaration, an
    acknowledgment, and references. [PURPOSE] and [HOW CHECKED] are left
    for the authors: only they know them."""
    used = [m for m in models if m["model"] != "unknown"]
    if not used:
        return {}
    first = min(_day(m["first_use"]) for m in used)
    last = max(_day(m["last_use"]) for m in used)
    local = [m for m in used if not m.get("hosted_by")]
    hosted = [m for m in used if m.get("hosted_by")]
    refs = (facts.get("references") or {})
    names = ", ".join(m["display"] for m in used)
    sentences = []
    for m in local:
        s = m["display"]
        ref = m.get("reference") or {}
        if ref.get("author"):
            s += f" ({ref['author']}{', ' + ref['year'] if ref.get('year') else ''})"
        ck = m.get("checkpoint")
        if ck:
            s += f", as the {m['quantization']} checkpoint {ck}" if m.get("quantization") else f", checkpoint {ck}"
            if m.get("revision"):
                s += f" at revision {m['revision'][:12]}"
        cite = "" if sentences else " (Kwon et al., 2023)"
        s += f", served locally with vLLM{' ' + m['vllm_version'] if m.get('vllm_version') else ''}{cite}"
        s += ", " + _span(m)
        sentences.append(s)
    methods = (f"We used Munin, {MUNIN_HOST_LINE} (Fischer et al., 2026), between {first} and {last} "
               f"to [PURPOSE]. Munin answered with the following language models: "
               + "; ".join(sentences) + ("." if sentences else ""))
    for m in hosted:
        n = m["requests"] + m["deep_research_jobs"]
        methods += (f" During a cluster outage, {n} request{'' if n == 1 else 's'} "
                    f"({_span(m)}) {'was' if n == 1 else 'were'} answered by {m['display']}, "
                    f"accessed through the {m['hosted_by']}.")
    methods += (" All outputs were checked by [WHO / HOW]; the authors take full responsibility "
                "for the content of this work.")
    writing = (f"During the preparation of this work the authors used Munin ({names}) to [REASON]. "
               "After using this tool, the authors reviewed and edited the content as needed and "
               "take full responsibility for the content of the publication.")
    references = []
    for key in ("munin",):
        if refs.get(key):
            references.append(refs[key])
    for m in used:
        if m.get("reference"):
            references.append(m["reference"])
    if local and refs.get("vllm"):
        references.append(refs["vllm"])
    return {
        "methods": methods,
        "writing_only": writing,
        "acknowledgment": MUNIN_ACK_LINE,
        "references_apa": [r.get("apa") for r in references if r.get("apa")],
        "bibtex": "\n\n".join(r.get("bibtex") for r in references if r.get("bibtex")),
    }


def _summary_window(request: Request) -> tuple[str | None, str | None, str | None]:
    q = request.query_params
    start, end = _parse_day(q.get("from")), _parse_day(q.get("to"))
    if (q.get("from") and not start) or (q.get("to") and not end):
        return None, None, "from/to must be YYYY-MM-DD"
    lo = start.isoformat() if start else "0000"
    hi = (end + timedelta(days=1)).isoformat() if end else "9999"
    return lo, hi, None


def _csv_response(models: list[dict], name: str) -> Response:
    import csv
    import io
    buf = io.StringIO()
    cols = ["model", "display", "checkpoint", "revision", "vllm_version", "hosted_by", "first_use",
            "last_use", "requests", "inferred_requests", "conversations", "deep_research_jobs",
            "tokens_input", "tokens_output", "tokens_total"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for m in models:
        w.writerow(m)
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def _dr_usage(caller: Caller, lo: str, hi: str) -> list[dict]:
    """The caller's Deep Research jobs in the window, from the backend."""
    try:
        since = _ts(lo).timestamp() if lo != "0000" else 0.0
        until = _ts(hi).timestamp() if hi != "9999" else 1e11
        r = await backend_client().get(
            "/api/usage/research", params={"since": since, "until": until}, timeout=10.0,
            headers={"X-Munin-User": header_safe(caller.key),
                     "X-Munin-Email": header_safe(caller.email)})
        if r.status_code == 200:
            return r.json().get("jobs") or []
    except Exception as e:
        log.info("deep research usage fetch failed: %s: %s", type(e).__name__, e)
    return []


@app.get("/api/usage/me/summary")
async def usage_me_summary(request: Request):
    """The caller's usage per model in a date range, with a ready-to-edit
    disclosure statement and references. ?from=YYYY-MM-DD&to=YYYY-MM-DD
    (inclusive, both optional), ?format=csv for a spreadsheet."""
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})
    lo, hi, err = _summary_window(request)
    if err:
        return JSONResponse(status_code=400, content={"error": {"code": "bad_date", "message": err}})
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, endpoint, model, tokens_input, tokens_output, tokens_total, conversation_id "
        "FROM usage_log WHERE user_email = ? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (caller.key, lo, hi)).fetchall()
    conn.close()
    facts = await get_system_facts()
    models = summarize_usage(rows, await _dr_usage(caller, lo, hi), facts)
    if request.query_params.get("format") == "csv":
        return _csv_response(models, "munin-usage.csv")
    return {
        "from": request.query_params.get("from"), "to": request.query_params.get("to"),
        "models": models,
        "totals": {k: sum(m[k] for m in models) for k in
                   ("requests", "conversations", "deep_research_jobs", "tokens_total")},
        "statement": build_statement(models, facts),
        "notes": [
            "Rows before 2026-11-21 did not record the answering model; their model is the "
            "one the deployment history says was serving at the time (inferred_requests).",
            "Dates of history entries marked approximate are known to the day, not the hour.",
        ],
    }


@app.get("/api/usage/group/summary")
async def usage_group_summary(request: Request):
    """Group totals per model for a group leader (their primary group) or an
    admin (?group=<id>). Totals only, no per-member breakdown. Rows carry a
    group since 2026-11-21; earlier use is not attributed to a group."""
    caller = browser_caller(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})
    admin = is_admin(request)
    if not admin and caller.role != "group_leader":
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Group leaders and admins only."}})
    group = (request.query_params.get("group") if admin else None) or caller.group
    if not group:
        return JSONResponse(status_code=400, content={"error": {"code": "no_group", "message": "No group given or known for this account."}})
    lo, hi, err = _summary_window(request)
    if err:
        return JSONResponse(status_code=400, content={"error": {"code": "bad_date", "message": err}})
    conn = get_db()
    rows = conn.execute(
        "SELECT timestamp, endpoint, model, tokens_input, tokens_output, tokens_total, conversation_id, user_email "
        "FROM usage_log WHERE group_id = ? AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
        (group, lo, hi)).fetchall()
    conn.close()
    facts = await get_system_facts()
    models = summarize_usage(rows, [], facts)
    if request.query_params.get("format") == "csv":
        return _csv_response(models, f"munin-usage-{re.sub(r'[^A-Za-z0-9_-]', '_', group)}.csv")
    return {
        "group": group, "from": request.query_params.get("from"), "to": request.query_params.get("to"),
        "members_active": len({r["user_email"] for r in rows if is_llm_row(r)}),
        "models": models,
        "totals": {k: sum(m[k] for m in models) for k in ("requests", "conversations", "tokens_total")},
        "statement": build_statement(models, facts),
        "notes": ["Group attribution starts 2026-11-21; Deep Research jobs are counted per user, not per group."],
    }


def _known_addresses(conn: sqlite3.Connection) -> dict[str, str]:
    """User key -> last address seen, for showing people instead of keys."""
    return {
        r["email"]: r["address"]
        for r in conn.execute("SELECT email, address FROM user_activity WHERE address IS NOT NULL")
    }


# ── Admin Activity ───────────────────────────────────────────────────────

@app.get("/api/usage/admin/activity")
async def admin_activity(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})

    conn = get_db()
    rows = conn.execute(
        "SELECT email, address, last_seen_at, source FROM user_activity ORDER BY last_seen_at DESC"
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    online = []  # last 15 min
    recent = []  # last 24h
    all_users = []

    for r in rows:
        user = {"email": r["address"] or r["email"], "user": r["email"],
                "last_seen_at": r["last_seen_at"], "source": r["source"]}
        all_users.append(user)
        try:
            seen = datetime.fromisoformat(r["last_seen_at"].replace("Z", "+00:00"))
            delta = (now - seen).total_seconds()
            if delta < 900:  # 15 minutes
                online.append(user)
            elif delta < 86400:  # 24 hours
                recent.append(user)
        except (ValueError, TypeError):
            pass

    return {
        "online": online,
        "recent": recent,
        "all_users": all_users,
        "total": len(all_users),
    }


# ── Announcements ────────────────────────────────────────────────────────────

@app.get("/api/announcement")
async def get_announcement(request: Request):
    conn = get_db()
    row = conn.execute("SELECT message, level, updated_at FROM announcements WHERE id = 1").fetchone()
    conn.close()
    if not row:
        return {"announcement": None}
    return {"announcement": {"message": row["message"], "level": row["level"], "updated_at": row["updated_at"]}}


@app.put("/api/announcement")
async def set_announcement(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})

    body = await request.json()
    message = body.get("message", "").strip()
    level = body.get("level", "info")
    if level not in ("info", "warning", "error"):
        level = "info"

    conn = get_db()
    if not message:
        conn.execute("DELETE FROM announcements WHERE id = 1")
    else:
        conn.execute(
            "INSERT INTO announcements (id, message, level, updated_at, updated_by) VALUES (1, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET message = ?, level = ?, updated_at = ?, updated_by = ?",
            (message, level, datetime.now(timezone.utc).isoformat(), email,
             message, level, datetime.now(timezone.utc).isoformat(), email),
        )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/announcement")
async def clear_announcement(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})

    conn = get_db()
    conn.execute("DELETE FROM announcements WHERE id = 1")
    conn.commit()
    conn.close()
    return {"ok": True}


# ── Backend mode (layer 2 failover) ──────────────────────────────────────────

@app.get("/api/backend-mode")
async def get_backend_mode(request: Request):
    """Which backend serves this user. Admins also get the probe state and the
    reduced episodes, whose start times are the merge cutoffs."""
    caller, _source, _key = await resolve_auth(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})
    if standby_client is None:
        return {"mode": NORMAL, "standby_configured": False}
    out = {"standby_configured": True, **(reduced_block() or {"mode": NORMAL})}
    if is_admin(request):
        conn = get_db()
        try:
            out["detail"] = backend_state.status()
            out["episodes"] = backend_failover.episodes(conn)
        finally:
            conn.close()
    return out


@app.put("/api/backend-mode")
async def set_backend_mode(request: Request):
    """Admin override: {"mode": "reduced" | "normal"} pins the mode, {"mode":
    "auto"} hands it back to the probes. With BACKEND_FAILBACK=manual this is
    how the gateway returns to the cluster, after the merge."""
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})
    if standby_client is None:
        return JSONResponse(status_code=409, content={"error": {"code": "no_standby", "message": "BACKEND_STANDBY_URL is not set."}})
    try:
        mode = (await request.json()).get("mode")
    except (ValueError, AttributeError):
        mode = None
    if mode == "auto":
        moved = backend_state.release()
    elif mode in (NORMAL, REDUCED):
        moved = backend_state.force(mode, f"set by admin {email}")
    else:
        return JSONResponse(status_code=400, content={"error": {"code": "bad_mode", "message": "mode must be normal, reduced or auto."}})
    log.warning("backend mode %s requested by %s, now %s", mode, email, backend_state.mode)
    _persist_backend_state(moved)
    return {"ok": True, **backend_state.status()}


# ── Public Proxy (no auth) ───────────────────────────────────────────────────
# Endpoints that are public on the cluster and don't need user authentication.

PUBLIC_API_PREFIXES = ("tags", "embedding_map")

# Root-level cluster routes (no /api/ prefix) reachable as /api/<route>, all
# called by static/search. Anything else answers the cluster's /api/ 404.
ROOT_FALLBACK_PREFIXES = ("search/", "retrieve", "author/", "sources")


# Sandbox artifact and bundle downloads: streamed through instead of read into
# memory, so a large file or analysis bundle neither fills the VPS's RAM nor
# runs into the client timeout before the first byte reaches the browser.
DOWNLOAD_PREFIXES = ("artifacts/", "bundles/")
_DOWNLOAD_HEADERS = ("content-type", "content-length", "content-encoding", "content-disposition",
                     "cache-control", "etag", "x-munin-sha256")


def is_download_path(path: str) -> bool:
    # /api/artifacts/{cid}/{aid} and /api/bundles/{cid}; not the JSON listing
    # /api/artifacts/{cid}, which is small and goes the normal way.
    if path.startswith("bundles/"):
        return True
    return path.startswith("artifacts/") and path.count("/") >= 2


async def stream_download(client: httpx.AsyncClient, upstream_req: httpx.Request) -> Response:
    upstream_resp = await client.send(upstream_req, stream=True)

    async def body():
        try:
            async for chunk in upstream_resp.aiter_raw():
                yield chunk
        finally:
            await upstream_resp.aclose()

    headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() in _DOWNLOAD_HEADERS}
    return StreamingResponse(body(), status_code=upstream_resp.status_code, headers=headers)


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(request: Request, path: str):
    # One backend per request, chosen once: a stream keeps the backend it
    # started on even if the mode changes while it runs.
    client = backend_client()
    # Allow public endpoints without auth
    if request.method == "GET" and any(path == p or path.startswith(p + "/") for p in PUBLIC_API_PREFIXES):
        try:
            query_string = f"?{request.url.query}" if request.url.query else ""
            upstream_resp = await client.request(request.method, f"/api/{path}{query_string}")
            return Response(content=upstream_resp.content, status_code=upstream_resp.status_code, headers=dict(upstream_resp.headers))
        except Exception as e:
            log.error(
                f"Public proxy error: {type(e).__name__}: {e} "
                f"[{request.method} /api/{path}]",
                exc_info=True,
            )
            return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})

    caller, source, api_key_id = await resolve_auth(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Valid authentication required."}})

    track_activity(caller, source)

    # Endpoints that bypass the quota check entirely:
    # - Lightweight read-only GET endpoints
    # - Chat completions (forward-auth protected, not the public API)
    EXEMPT_PREFIXES = ("status", "personas", "sources", "deepresearch/queue", "announcement", "chats", "profile", "artifacts", "bundles", "projects")
    is_exempt = (
        (request.method == "GET" and any(path.startswith(p) for p in EXEMPT_PREFIXES))
        or path.startswith("chat/completions")
    )
    # chat/completions skips rate limiting but should still be logged
    should_log = not is_exempt or path.startswith("chat/completions")

    if not is_exempt:
        # Monthly token quota
        rate_error = check_rate_limits(caller)
        if rate_error:
            retry_after = rate_error.pop("retry_after", 30)
            return JSONResponse(
                status_code=429,
                content={"error": rate_error},
                headers={"Retry-After": str(retry_after)},
            )

    start_time = time.time()

    try:
        # Build upstream request — try /api/{path} first, fall back to /{path}
        # (cluster has both /api/* routes and root-level routes like /sources, /search/*)
        query_string = f"?{request.url.query}" if request.url.query else ""
        url = f"/api/{path}{query_string}"

        headers = {
            "X-Munin-User": header_safe(caller.key),
            "X-Munin-Email": header_safe(caller.email),
            "X-Munin-Name": header_safe(caller.name),
        }
        content_type = request.headers.get("Content-Type")
        if content_type:
            headers["Content-Type"] = content_type

        body = await request.body()

        # Check if this is a streaming request: either the JSON body
        # carries stream:true (the chat POST), or the client asks for
        # SSE outright via Accept: text/event-stream — the resume GET
        # (background turns Phase B) has no body, and without this
        # check it fell into the buffered branch below, which blocks
        # until the upstream stream ends and defeats a live re-attach.
        is_streaming = "text/event-stream" in (
            request.headers.get("Accept") or ""
        ).lower()
        if not is_streaming and body and content_type and "json" in content_type:
            try:
                req_json = json.loads(body)
                is_streaming = req_json.get("stream", False)
            except (json.JSONDecodeError, AttributeError):
                pass

        upstream_req = client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if body else None,
        )

        if request.method == "GET" and is_download_path(path):
            return await stream_download(client, upstream_req)

        if is_streaming:
            upstream_resp = await client.send(upstream_req, stream=True)

            async def stream_and_log():
                scan = SseUsageScanner()
                try:
                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk
                        scan.feed(chunk)
                finally:
                    await upstream_resp.aclose()
                    duration_ms = int((time.time() - start_time) * 1000)
                    if should_log:
                        log_usage(
                            caller.key, source, api_key_id, f"/api/{path}",
                            tokens_input=scan.tokens_input, tokens_output=scan.tokens_output,
                            tokens_total=scan.tokens_total, tools_invoked=scan.tools or None,
                            duration_ms=duration_ms, status_code=upstream_resp.status_code,
                            model=scan.model, persona=scan.persona,
                            group_id=caller.group, conversation_id=scan.conversation_id,
                        )

            return StreamingResponse(
                stream_and_log(),
                status_code=upstream_resp.status_code,
                media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
            )
        else:
            upstream_resp = await client.send(upstream_req)

            # Fallback: if /api/{path} returns 404, retry as /{path}, only for
            # the root-level routes the search page calls. Unrestricted, it
            # exposed every cluster route to any logged-in user, including
            # the legacy /deepresearch/* reports and /mcp/call.
            if upstream_resp.status_code == 404 and path.startswith(ROOT_FALLBACK_PREFIXES):
                fallback_url = f"/{path}{query_string}"
                fallback_req = client.build_request(
                    method=request.method, url=fallback_url,
                    headers=headers, content=body if body else None,
                )
                upstream_resp = await client.send(fallback_req)

            duration_ms = int((time.time() - start_time) * 1000)

            # Extract usage from response body
            scan = SseUsageScanner()
            try:
                resp_json = upstream_resp.json()
                if isinstance(resp_json, dict):
                    scan.take(resp_json)
            except Exception:
                pass

            if should_log:
                log_usage(
                    caller.key, source, api_key_id, f"/api/{path}",
                    tokens_input=scan.tokens_input, tokens_output=scan.tokens_output,
                    tokens_total=scan.tokens_total, duration_ms=duration_ms,
                    status_code=upstream_resp.status_code, model=scan.model,
                    group_id=caller.group,
                )

            if (path == "status" and request.method == "GET"
                    and upstream_resp.status_code == 200 and reduced_block() is not None):
                headers_out = {k: v for k, v in upstream_resp.headers.items()
                               if k.lower() not in ("content-length", "content-encoding", "transfer-encoding")}
                return Response(content=with_backend_block(upstream_resp.content),
                                status_code=200, headers=headers_out)
            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=dict(upstream_resp.headers),
            )
    except Exception as e:
        log.error(
            f"Proxy error: {type(e).__name__}: {e} "
            f"[{request.method} /api/{path} user={caller.key}]",
            exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})



# ── OpenAI-compatible /v1 endpoints (for api.<domain>) ───────────────────────

@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(request: Request, path: str):
    """Proxy /v1/* requests, mapping to /api/* on the cluster."""
    client = backend_client()
    caller, source, api_key_id = await resolve_auth(request)
    if not caller:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Invalid API key."}})

    track_activity(caller, source)

    # GET /v1/models — OpenAI-compatible model list. Clients (Positron,
    # Cursor, the OpenAI SDK) probe this on connect to validate the
    # provider and populate the model picker. Proxied to the cluster's
    # /api/models (added 2026-09-15), which reports what the production
    # backend actually serves; until then this list was synthesised from a
    # VLLM_MODEL_NAME env that no VPS deploy ever set, so a cluster-side
    # backbone change left the VPS reporting the old name. Falls back to the
    # env/literal only when the cluster does not answer, so the probe still
    # succeeds while the tunnel is down. Does not count against rate/quota.
    if request.method == "GET" and path == "models":
        try:
            r = await client.get("/api/models", timeout=5.0)
            if r.status_code == 200:
                d = r.json()
                data = [{"id": m.get("id"), "object": "model",
                         "created": m.get("created", 0), "owned_by": "munin"}
                        for m in d.get("data", []) if m.get("id")]
                if data:
                    # The stable alias: a client configured with "munin"
                    # keeps working across backbone changes (raw mode maps
                    # it to the served model).
                    data.append({"id": "munin", "object": "model",
                                 "created": data[0]["created"], "owned_by": "munin"})
                    return JSONResponse(content={"object": "list", "data": data})
        except Exception:
            pass
        # Cluster unreachable: the name it last reported (/api/system cache),
        # else VLLM_MODEL_NAME, else "munin". Never a hardcoded model name,
        # which is how this went stale before.
        cached = _system_cache.get("facts") or {}
        model_id = ((cached.get("backbone") or {}).get("served_name")
                    or os.environ.get("VLLM_MODEL_NAME") or "munin")
        return JSONResponse(content={
            "object": "list",
            "data": [{
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "munin",
            }],
        })

    rate_error = check_rate_limits(caller)
    if rate_error:
        retry_after = rate_error.pop("retry_after", 30)
        return JSONResponse(status_code=429, content={"error": rate_error}, headers={"Retry-After": str(retry_after)})

    start_time = time.time()

    # Map /v1/chat/completions → /api/chat/completions, /v1/models → /api/models
    upstream_path = f"/api/{path}"
    if request.url.query:
        upstream_path += f"?{request.url.query}"

    headers = {
        "X-Munin-User": header_safe(caller.key),
        "X-Munin-Email": header_safe(caller.email),
        "X-Munin-Name": header_safe(caller.name),
        "X-Munin-Ephemeral": "true",  # API requests should not be saved as conversations
    }
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    body = await request.body()

    try:
        upstream_req = client.build_request(method=request.method, url=upstream_path, headers=headers, content=body if body else None)

        if request.method == "POST":
            # Chat completions may return SSE (with persona) or JSON (raw/no persona).
            # Use streaming to handle both; accumulate body to parse JSON if not SSE.
            upstream_resp = await client.send(upstream_req, stream=True)
            content_type = upstream_resp.headers.get("content-type", "")
            is_sse = "text/event-stream" in content_type

            if is_sse:
                async def stream_v1():
                    scan = SseUsageScanner()
                    try:
                        async for chunk in upstream_resp.aiter_bytes():
                            yield chunk
                            scan.feed(chunk)
                    finally:
                        await upstream_resp.aclose()
                        log_usage(caller.key, source, api_key_id, f"/v1/{path}",
                                  tokens_input=scan.tokens_input, tokens_output=scan.tokens_output,
                                  tokens_total=scan.tokens_total, model=scan.model, persona=scan.persona,
                                  tools_invoked=scan.tools or None,
                                  duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)

                return StreamingResponse(stream_v1(), status_code=upstream_resp.status_code,
                                         media_type="text/event-stream")
            else:
                # JSON response (raw model, no persona) — read fully
                resp_body = await upstream_resp.aread()
                await upstream_resp.aclose()
                scan = SseUsageScanner()
                try:
                    resp_json = json.loads(resp_body)
                    if isinstance(resp_json, dict):
                        scan.take(resp_json)
                except Exception:
                    pass
                log_usage(caller.key, source, api_key_id, f"/v1/{path}",
                          tokens_input=scan.tokens_input, tokens_output=scan.tokens_output,
                          tokens_total=scan.tokens_total, model=scan.model,
                          duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)
                return Response(content=resp_body, status_code=upstream_resp.status_code,
                                headers={k: v for k, v in upstream_resp.headers.items() if k.lower() != "transfer-encoding"})
        else:
            upstream_resp = await client.send(upstream_req)
            tokens_total = 0
            try:
                u = upstream_resp.json().get("usage", {})
                tokens_total = u.get("total_tokens", 0) or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
            except Exception:
                pass
            log_usage(caller.key, source, api_key_id, f"/v1/{path}", tokens_total=tokens_total,
                      duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)
            return Response(content=upstream_resp.content, status_code=upstream_resp.status_code, headers=dict(upstream_resp.headers))
    except Exception as e:
        log.error(
            f"V1 proxy error: {type(e).__name__}: {e} "
            f"[{request.method} /v1/{path} user={caller.key}]",
            exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
