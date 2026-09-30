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
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from models import CreateKeyRequest, CreateKeyResponse, KeyInfo

# ── Configuration ────────────────────────────────────────────────────────────

CLUSTER_TUNNEL = os.environ.get("CLUSTER_TUNNEL", "http://127.0.0.1:18080")
# Admin detection is now via X-Munin-Role header from munin-auth (whitelist.csv role column)
# ADMIN_EMAILS kept as fallback for API key auth (no forward-auth headers)
ADMIN_EMAILS = set(os.environ.get("ADMIN_EMAILS", "admin@muninai.org").split(","))
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
    "Person115" broke his whole session, /api/status included.

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
http_client = httpx.AsyncClient(base_url=CLUSTER_TUNNEL, timeout=300.0)

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

def resolve_auth(request: Request) -> tuple[str | None, str, str | None]:
    """
    Resolve the user from the request.
    Returns (email, source, api_key_id) or (None, ..., ...) if unauthorized.
    """
    # A Bearer token decides identity whenever one is sent, valid or not, and
    # is checked first: api.muninai.org has no forward-auth, so an
    # X-Munin-Email there comes from the client, not from munin-auth. Caddy
    # strips it on that host; this keeps a spoofed header from winning if a
    # request ever reaches the gateway without that strip. Browsers never
    # send a Bearer token.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        key_info = validate_api_key(auth_header[7:].strip())
        if key_info:
            return key_info["email"], "api_key", key_info["id"]
        return None, "unknown", None

    # X-Munin-Email, set by munin-auth via forward-auth on the browser hosts
    email = request.headers.get("X-Munin-Email")
    if email:
        return email, "browser", None

    return None, "unknown", None


# ── Rate Limiting ────────────────────────────────────────────────────────────

def is_admin_email(email: str) -> bool:
    """Check if an email is an admin (for rate-limit bypass on API keys)."""
    if email in ADMIN_EMAILS:
        return True
    overrides = quotas_config.get("overrides", {}).get(email, {})
    return overrides.get("unlimited", False)


def check_rate_limits(email: str) -> dict | None:
    """Check the monthly token quota. Returns an error dict if exceeded, else None.

    Admin users (and anyone with `unlimited: true`) bypass it. Requests/minute
    and concurrent-request limits were removed 2026-08-25; this is now the only
    quota, and it is a COST control rather than a load-shedding one. Keeping the
    function name means every call site and the 429 envelope are unchanged.
    """
    if is_admin_email(email):
        return None

    limits = get_user_quotas(email)

    # Monthly token limit — only API key usage counts against quota (browser chat is free)
    token_limit = limits["tokens_per_month"]
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as total FROM usage_log WHERE user_email = ? AND timestamp >= ? AND source = 'api_key'",
        (email, month_start),
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

def log_usage(
    email: str, source: str, api_key_id: str | None, endpoint: str,
    tokens_input: int = 0, tokens_output: int = 0, tokens_total: int = 0,
    tools_invoked: list[str] | None = None, duration_ms: int = 0,
    status_code: int = 200, model: str | None = None, persona: str | None = None,
):
    conn = get_db()
    conn.execute(
        """INSERT INTO usage_log
           (id, user_email, timestamp, persona, source, api_key_id, endpoint,
            tokens_input, tokens_output, tokens_total, tools_invoked, duration_ms, status_code, model)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), email, datetime.now(timezone.utc).isoformat(),
            persona, source, api_key_id, endpoint,
            tokens_input, tokens_output, tokens_total,
            json.dumps(tools_invoked) if tools_invoked else None,
            duration_ms, status_code, model,
        ),
    )
    conn.commit()
    conn.close()


# ── Activity Tracking ────────────────────────────────────────────────────

def track_activity(email: str, source: str):
    """Record that a user was seen (upsert into user_activity)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO user_activity (email, last_seen_at, source) VALUES (?, ?, ?) "
        "ON CONFLICT(email) DO UPDATE SET last_seen_at = excluded.last_seen_at, source = excluded.source",
        (email, datetime.now(timezone.utc).isoformat(), source),
    )
    conn.commit()
    conn.close()


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    init_db()
    load_quotas()


# ── API Key CRUD ─────────────────────────────────────────────────────────────
# NOTE: These must be defined BEFORE the catch-all /api/{path:path} proxy route.

@app.post("/api/keys")
async def create_key(request: Request, body: CreateKeyRequest):
    email = request.headers.get("X-Munin-Email")
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    raw_key = f"sk-munin-{secrets.token_hex(16)}"
    key_id = f"key_{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    conn.execute(
        "INSERT INTO api_keys (id, key_hash, key_prefix, user_email, name, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (key_id, hash_key(raw_key), raw_key[:16], email, body.name, now),
    )
    conn.commit()
    conn.close()

    return CreateKeyResponse(id=key_id, key=raw_key, key_prefix=raw_key[:16], name=body.name, created_at=now)


@app.get("/api/keys")
async def list_keys(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    conn = get_db()
    rows = conn.execute(
        "SELECT id, key_prefix, name, created_at, last_used_at, revoked_at FROM api_keys WHERE user_email = ? ORDER BY created_at DESC",
        (email,),
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
    email = request.headers.get("X-Munin-Email")
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    conn = get_db()
    result = conn.execute(
        "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND user_email = ? AND revoked_at IS NULL",
        (datetime.now(timezone.utc).isoformat(), key_id, email),
    )
    conn.commit()
    conn.close()

    if result.rowcount == 0:
        return JSONResponse(status_code=404, content={"error": {"code": "not_found", "message": "Key not found."}})
    return {"deleted": True}


# ── Usage Stats ──────────────────────────────────────────────────────────────

@app.get("/api/usage/me")
async def usage_me(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Authentication required."}})

    limits = get_user_quotas(email)
    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    conn = get_db()

    # Monthly totals — split by source
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as tokens, COUNT(*) as requests FROM usage_log WHERE user_email = ? AND timestamp >= ?",
        (email, month_start),
    ).fetchone()
    api_row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total), 0) as tokens, COUNT(*) as requests FROM usage_log WHERE user_email = ? AND timestamp >= ? AND source = 'api_key'",
        (email, month_start),
    ).fetchone()

    # Tool breakdown
    tool_rows = conn.execute(
        "SELECT tools_invoked FROM usage_log WHERE user_email = ? AND timestamp >= ? AND tools_invoked IS NOT NULL",
        (email, month_start),
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
        (month_start, email),
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
                "email": r["user_email"],
                "tokens": r["tokens"],
                "requests": r["requests"],
                "by_source": source_breakdown.get(r["user_email"], {}),
            }
            for r in top_users
        ],
        "tools_usage": tools_usage,
    }


# ── Admin Activity ───────────────────────────────────────────────────────

@app.get("/api/usage/admin/activity")
async def admin_activity(request: Request):
    email = request.headers.get("X-Munin-Email")
    if not email or not is_admin(request):
        return JSONResponse(status_code=403, content={"error": {"code": "forbidden", "message": "Admin access required."}})

    conn = get_db()
    rows = conn.execute(
        "SELECT email, last_seen_at, source FROM user_activity ORDER BY last_seen_at DESC"
    ).fetchall()
    conn.close()

    now = datetime.now(timezone.utc)
    online = []  # last 15 min
    recent = []  # last 24h
    all_users = []

    for r in rows:
        user = {"email": r["email"], "last_seen_at": r["last_seen_at"], "source": r["source"]}
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


# ── Public Proxy (no auth) ───────────────────────────────────────────────────
# Endpoints that are public on the cluster and don't need user authentication.

PUBLIC_API_PREFIXES = ("tags", "embedding_map")

# Root-level cluster routes (no /api/ prefix) reachable as /api/<route>, all
# called by static/search. Anything else answers the cluster's /api/ 404.
ROOT_FALLBACK_PREFIXES = ("search/", "retrieve", "author/", "sources")


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_api(request: Request, path: str):
    # Allow public endpoints without auth
    if request.method == "GET" and any(path == p or path.startswith(p + "/") for p in PUBLIC_API_PREFIXES):
        try:
            query_string = f"?{request.url.query}" if request.url.query else ""
            upstream_resp = await http_client.request(request.method, f"/api/{path}{query_string}")
            return Response(content=upstream_resp.content, status_code=upstream_resp.status_code, headers=dict(upstream_resp.headers))
        except Exception as e:
            log.error(
                f"Public proxy error: {type(e).__name__}: {e} "
                f"[{request.method} /api/{path}]",
                exc_info=True,
            )
            return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})

    email, source, api_key_id = resolve_auth(request)
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Valid authentication required."}})

    track_activity(email, source)

    # Endpoints that bypass the quota check entirely:
    # - Lightweight read-only GET endpoints
    # - Chat completions (forward-auth protected, not the public API)
    EXEMPT_PREFIXES = ("status", "personas", "sources", "deepresearch/queue", "announcement", "chats", "profile", "artifacts", "projects")
    is_exempt = (
        (request.method == "GET" and any(path.startswith(p) for p in EXEMPT_PREFIXES))
        or path.startswith("chat/completions")
    )
    # chat/completions skips rate limiting but should still be logged
    should_log = not is_exempt or path.startswith("chat/completions")

    if not is_exempt:
        # Monthly token quota
        rate_error = check_rate_limits(email)
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
            "X-Munin-Email": header_safe(email),
            "X-Munin-Name": header_safe(
                request.headers.get("X-Munin-Name", email.split("@")[0])
            ),
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

        upstream_req = http_client.build_request(
            method=request.method,
            url=url,
            headers=headers,
            content=body if body else None,
        )

        if is_streaming:
            upstream_resp = await http_client.send(upstream_req, stream=True)

            async def stream_and_log():
                tokens_total = 0
                model = None
                tools = []
                try:
                    async for chunk in upstream_resp.aiter_bytes():
                        yield chunk
                        # Try to extract usage from SSE done event
                        try:
                            text = chunk.decode()
                            for line in text.split("\n"):
                                if line.startswith("data: ") and '"usage"' in line:
                                    data = json.loads(line[6:])
                                    usage = data.get("usage", {})
                                    tokens_total = usage.get("total_tokens", 0) or (
                                        usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                                    )
                                if line.startswith("data: ") and '"name"' in line and "tool_call" in text:
                                    data = json.loads(line[6:])
                                    if "name" in data:
                                        tools.append(data["name"])
                                if line.startswith("data: ") and '"model"' in line:
                                    data = json.loads(line[6:])
                                    model = data.get("model", model)
                        except Exception:
                            pass
                finally:
                    await upstream_resp.aclose()
                    duration_ms = int((time.time() - start_time) * 1000)
                    if should_log:
                        log_usage(
                            email, source, api_key_id, f"/api/{path}",
                            tokens_total=tokens_total, tools_invoked=tools or None,
                            duration_ms=duration_ms, status_code=upstream_resp.status_code,
                            model=model,
                        )

            return StreamingResponse(
                stream_and_log(),
                status_code=upstream_resp.status_code,
                media_type=upstream_resp.headers.get("content-type", "text/event-stream"),
            )
        else:
            upstream_resp = await http_client.send(upstream_req)

            # Fallback: if /api/{path} returns 404, retry as /{path}, only for
            # the root-level routes the search page calls. Unrestricted, it
            # exposed every cluster route to any logged-in user, including
            # the legacy /deepresearch/* reports and /mcp/call.
            if upstream_resp.status_code == 404 and path.startswith(ROOT_FALLBACK_PREFIXES):
                fallback_url = f"/{path}{query_string}"
                fallback_req = http_client.build_request(
                    method=request.method, url=fallback_url,
                    headers=headers, content=body if body else None,
                )
                upstream_resp = await http_client.send(fallback_req)

            duration_ms = int((time.time() - start_time) * 1000)

            # Extract usage from response body
            tokens_total = 0
            model = None
            try:
                resp_json = upstream_resp.json()
                usage = resp_json.get("usage", {})
                tokens_total = usage.get("total_tokens", 0) or (
                    usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
                )
                model = resp_json.get("model")
            except Exception:
                pass

            if should_log:
                log_usage(
                    email, source, api_key_id, f"/api/{path}",
                    tokens_total=tokens_total, duration_ms=duration_ms,
                    status_code=upstream_resp.status_code, model=model,
                )

            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=dict(upstream_resp.headers),
            )
    except Exception as e:
        log.error(
            f"Proxy error: {type(e).__name__}: {e} "
            f"[{request.method} /api/{path} user={email}]",
            exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})



# ── OpenAI-compatible /v1 endpoints (for api.muninai.org) ────────────────────

@app.api_route("/v1/{path:path}", methods=["GET", "POST"])
async def proxy_v1(request: Request, path: str):
    """Proxy /v1/* requests, mapping to /api/* on the cluster."""
    email, source, api_key_id = resolve_auth(request)
    if not email:
        return JSONResponse(status_code=401, content={"error": {"code": "unauthorized", "message": "Invalid API key."}})

    track_activity(email, source)

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
            r = await http_client.get("/api/models", timeout=5.0)
            if r.status_code == 200:
                d = r.json()
                data = [{"id": m.get("id"), "object": "model",
                         "created": m.get("created", 0), "owned_by": "munin"}
                        for m in d.get("data", []) if m.get("id")]
                if data:
                    return JSONResponse(content={"object": "list", "data": data})
        except Exception:
            pass
        model_id = os.environ.get("VLLM_MODEL_NAME", "qwen3.8-27b")
        return JSONResponse(content={
            "object": "list",
            "data": [{
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "munin",
            }],
        })

    rate_error = check_rate_limits(email)
    if rate_error:
        retry_after = rate_error.pop("retry_after", 30)
        return JSONResponse(status_code=429, content={"error": rate_error}, headers={"Retry-After": str(retry_after)})

    start_time = time.time()

    # Map /v1/chat/completions → /api/chat/completions, /v1/models → /api/models
    upstream_path = f"/api/{path}"
    if request.url.query:
        upstream_path += f"?{request.url.query}"

    headers = {
        "X-Munin-Email": header_safe(email),
        "X-Munin-Name": header_safe(email.split("@")[0]),
        "X-Munin-Ephemeral": "true",  # API requests should not be saved as conversations
    }
    content_type = request.headers.get("Content-Type")
    if content_type:
        headers["Content-Type"] = content_type

    body = await request.body()

    try:
        upstream_req = http_client.build_request(method=request.method, url=upstream_path, headers=headers, content=body if body else None)

        if request.method == "POST":
            # Chat completions may return SSE (with persona) or JSON (raw/no persona).
            # Use streaming to handle both; accumulate body to parse JSON if not SSE.
            upstream_resp = await http_client.send(upstream_req, stream=True)
            content_type = upstream_resp.headers.get("content-type", "")
            is_sse = "text/event-stream" in content_type

            if is_sse:
                async def stream_v1():
                    tokens_total = 0
                    try:
                        async for chunk in upstream_resp.aiter_bytes():
                            yield chunk
                            try:
                                text = chunk.decode()
                                for line in text.split("\n"):
                                    if line.startswith("data: ") and '"usage"' in line:
                                        u = json.loads(line[6:]).get("usage", {})
                                        tokens_total = u.get("total_tokens", 0) or (
                                            u.get("prompt_tokens", 0) + u.get("completion_tokens", 0)
                                        )
                            except Exception:
                                pass
                    finally:
                        await upstream_resp.aclose()
                        log_usage(email, source, api_key_id, f"/v1/{path}", tokens_total=tokens_total,
                                  duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)

                return StreamingResponse(stream_v1(), status_code=upstream_resp.status_code,
                                         media_type="text/event-stream")
            else:
                # JSON response (raw model, no persona) — read fully
                resp_body = await upstream_resp.aread()
                await upstream_resp.aclose()
                tokens_total = 0
                try:
                    u = json.loads(resp_body).get("usage", {})
                    tokens_total = u.get("total_tokens", 0) or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
                except Exception:
                    pass
                log_usage(email, source, api_key_id, f"/v1/{path}", tokens_total=tokens_total,
                          duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)
                return Response(content=resp_body, status_code=upstream_resp.status_code,
                                headers={k: v for k, v in upstream_resp.headers.items() if k.lower() != "transfer-encoding"})
        else:
            upstream_resp = await http_client.send(upstream_req)
            tokens_total = 0
            try:
                u = upstream_resp.json().get("usage", {})
                tokens_total = u.get("total_tokens", 0) or (u.get("prompt_tokens", 0) + u.get("completion_tokens", 0))
            except Exception:
                pass
            log_usage(email, source, api_key_id, f"/v1/{path}", tokens_total=tokens_total,
                      duration_ms=int((time.time() - start_time) * 1000), status_code=upstream_resp.status_code)
            return Response(content=upstream_resp.content, status_code=upstream_resp.status_code, headers=dict(upstream_resp.headers))
    except Exception as e:
        log.error(
            f"V1 proxy error: {type(e).__name__}: {e} "
            f"[{request.method} /v1/{path} user={email}]",
            exc_info=True,
        )
        return JSONResponse(status_code=502, content={"error": {"code": "proxy_error", "message": "Backend unavailable."}})


# ── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}
