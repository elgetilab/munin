# API Gateway, API Keys & Usage Tracking

## Overview

An API gateway on the VPS sits between Caddy and the cluster tunnel. It handles three concerns that don't belong in either the auth service or the retrieval service: API key validation for CLI tools, per-user rate limiting, and usage tracking.

## Why a Separate Service

- **munin-auth** handles browser sessions (OTP login, cookies). It shouldn't know about API keys.
- **The retrieval service** on the cluster handles LLM and RAG logic. It shouldn't handle billing/quotas.
- **The API gateway** is the policy layer: "is this request allowed, and should I track it?"

## Architecture

```
Browser users:
  Caddy → forward_auth(munin-auth) → API gateway → tunnel → cluster

CLI tools (OpenCode, Cursor, aider):
  Caddy → API gateway (validates API key) → tunnel → cluster
```

```
┌─────────────────────────────────────────────────┐
│ VPS                                             │
│                                                 │
│  Caddy (:443)                                   │
│    │                                            │
│    ├── Browser requests (with session cookie)    │
│    │     → munin-auth (:8090) validates session  │
│    │     → sets X-Munin-Email header             │
│    │     → API gateway (:8070)                   │
│    │                                            │
│    ├── API requests (with Bearer token)          │
│    │     → API gateway (:8070) validates key     │
│    │     → sets X-Munin-Email header             │
│    │                                            │
│    └── API gateway (:8070)                       │
│          → rate limit check                     │
│          → log usage                            │
│          → proxy to tunnel (:18080)              │
│                                                 │
└─────────────────────────────────────────────────┘
```

## API Gateway Service

**Stack:** Python 3.12, FastAPI, uvicorn. Runs as a Docker container on the VPS.

**Port:** 127.0.0.1:8070

### Request Flow

1. Request arrives from Caddy with either:
   - `X-Munin-Email` header (browser user, already authenticated by munin-auth), or
   - `Authorization: Bearer sk-munin-...` header (CLI tool)
2. If Bearer token: validate against the API keys database. If invalid → 401. If valid → set `X-Munin-Email` from the key's associated email.
3. Rate limit check: has this user exceeded their quota? If yes → 429 with `Retry-After`.
4. Log the request metadata (not content).
5. Proxy the request to `127.0.0.1:18080` (cluster tunnel), forwarding `X-Munin-Email` and `X-Munin-Name`.
6. Stream the response back to the client.
7. After response completes: log usage (tokens consumed, duration, tools used).

### Caddyfile Update

Caddy routes API traffic through the gateway:

```
chat.muninai.org {
    import auth

    # All API calls go through the gateway
    handle /api/* {
        reverse_proxy 127.0.0.1:8070
    }

    # Static frontend assets served directly
    handle {
        root * /srv/static/frontend
        file_server
    }
}

# OpenAI-compatible API endpoint for CLI tools
api.muninai.org {
    # No forward_auth — gateway handles API key validation
    reverse_proxy 127.0.0.1:8070
}
```

The new subdomain `api.muninai.org` provides the OpenAI-compatible endpoint that CLI tools connect to. No browser auth on this subdomain — the gateway validates the Bearer token directly.

## API Key System

### Key Format

```
sk-munin-{32 hex characters}
```

Example: `sk-munin-a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4`

### Storage

SQLite database at `/data/gateway.db` on the VPS (Docker volume).

```sql
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,              -- UUID
    key_hash TEXT NOT NULL UNIQUE,    -- SHA-256 hash of the full key
    key_prefix TEXT NOT NULL,         -- "sk-munin-a1b2c3d4" (first 16 chars, for display)
    user_email TEXT NOT NULL,         -- owner
    name TEXT,                        -- user-given label ("my laptop", "cursor")
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT                   -- null = active, set = revoked
);
```

Keys are stored as SHA-256 hashes. The plaintext key is shown to the user exactly once (on creation) and never stored.

### Endpoints (on the gateway)

#### `POST /api/keys`

Create a new API key. Requires browser auth (X-Munin-Email header from forward-auth).

**Request:**
```json
{"name": "my laptop"}
```

**Response:**
```json
{
  "id": "key_abc123",
  "key": "sk-munin-a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
  "key_prefix": "sk-munin-a1b2c3d4",
  "name": "my laptop",
  "created_at": "2026-04-11T15:00:00Z",
  "warning": "Save this key now. It cannot be shown again."
}
```

#### `GET /api/keys`

List the user's API keys (prefix only, never the full key).

**Response:**
```json
{
  "keys": [
    {
      "id": "key_abc123",
      "key_prefix": "sk-munin-a1b2c3d4",
      "name": "my laptop",
      "created_at": "2026-04-11T15:00:00Z",
      "last_used_at": "2026-04-11T18:30:00Z",
      "revoked": false
    }
  ]
}
```

#### `DELETE /api/keys/{id}`

Revoke an API key. Sets `revoked_at` timestamp; the key stops working immediately.

### OpenAI Compatibility

CLI tools connect like this:

```python
from openai import OpenAI

client = OpenAI(
    api_key="sk-munin-a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4",
    base_url="https://api.muninai.org/v1"
)

response = client.chat.completions.create(
    model="qwen3.5-35b-a3b",
    messages=[{"role": "user", "content": "Hello"}]
)
```

The gateway receives the request, validates the key, and proxies to the cluster's vLLM endpoint. The response format is already OpenAI-compatible (vLLM provides this).

**Endpoint mapping:**

| Client calls | Gateway proxies to |
|-------------|-------------------|
| `POST /v1/chat/completions` | `tunnel:18080/api/chat/completions` (via retrieval service with persona injection) |
| `GET /v1/models` | `tunnel:18080/api/models` |

For CLI tools that don't need personas or RAG, the gateway can also proxy directly to vLLM (bypassing the retrieval service):

| Client calls | Gateway proxies to |
|-------------|-------------------|
| `POST /v1/chat/completions` (with `X-Direct: true` or via `api.muninai.org/v1/direct/`) | `tunnel:18000` → vLLM directly |

This lets power users skip the retrieval layer when they just want raw model access.

## Rate Limiting

### Quotas

| Limit | Default | Purpose |
|-------|---------|---------|
| Tokens per month | 2,000,000 | Prevent one user from consuming all resources |
| Requests per minute | 10 | Prevent accidental loops |
| Concurrent requests | 3 | vLLM has limited capacity |

Quotas are per user (by email), not per API key. A user with 3 keys shares one quota.

### Configuration

Quotas are configured in a file (`config/quotas.yml`) or env vars:

```yaml
default:
  tokens_per_month: 2000000
  requests_per_minute: 10
  concurrent_requests: 3

overrides:
  admin@muninai.org:
    tokens_per_month: 10000000
    requests_per_minute: 30
```

### Rate Limit Response

```
HTTP/1.1 429 Too Many Requests
Retry-After: 30
X-RateLimit-Limit: 10
X-RateLimit-Remaining: 0
X-RateLimit-Reset: 1712858400

{
  "error": {
    "code": "rate_limited",
    "message": "Rate limit exceeded. Please wait 30 seconds.",
    "type": "requests_per_minute"
  }
}
```

### Token Counting

After proxying a response from the cluster, the gateway reads the `usage` field from the response:

```json
{
  "usage": {
    "prompt_tokens": 1250,
    "completion_tokens": 480,
    "total_tokens": 1730
  }
}
```

This is added to the user's monthly total. The gateway stores running totals in SQLite and resets monthly.

## Usage Tracking

### What Is Logged

Every request through the gateway creates a usage record:

```sql
CREATE TABLE usage_log (
    id TEXT PRIMARY KEY,
    user_email TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    persona TEXT,                    -- which persona was used (null for direct API)
    source TEXT NOT NULL,            -- "browser" or "api_key"
    api_key_id TEXT,                 -- which API key (null for browser)
    endpoint TEXT NOT NULL,          -- "/api/chat/completions", "/v1/models", etc.
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_total INTEGER,
    tools_invoked TEXT,              -- JSON array: ["paper_search", "web_search"]
    duration_ms INTEGER,            -- total request duration
    status_code INTEGER,            -- 200, 429, 500, etc.
    model TEXT                      -- which model was used
);

CREATE INDEX idx_usage_user_month ON usage_log(user_email, timestamp);
```

### What Is NOT Logged

- Message content (user questions, model responses)
- Thinking/reasoning content
- Tool call arguments or results
- Uploaded document content

Privacy by design. The log tells you *who* used *what feature* for *how long*, not *what they asked*.

### Aggregation Queries

The gateway exposes usage stats via API:

#### `GET /api/usage/me`

Current user's stats. Requires auth.

**Response:**
```json
{
  "current_month": {
    "tokens_used": 345000,
    "tokens_limit": 2000000,
    "tokens_remaining": 1655000,
    "requests": 127,
    "tools_used": {
      "paper_search": 34,
      "web_search": 18,
      "get_citations": 12
    }
  },
  "api_keys": [
    {
      "key_prefix": "sk-munin-a1b2c3d4",
      "name": "my laptop",
      "tokens_this_month": 45000,
      "last_used": "2026-04-11T18:30:00Z"
    }
  ]
}
```

#### `GET /api/usage/admin` (admin only)

Aggregate stats for all users. Protected by an admin email check.

**Response:**
```json
{
  "period": "2026-04",
  "total_tokens": 12500000,
  "total_requests": 3400,
  "active_users": 15,
  "top_users": [
    {"email": "researcher@uni.de", "tokens": 2100000, "requests": 580}
  ],
  "tools_usage": {
    "paper_search": 890,
    "web_search": 340
  },
  "personas_usage": {
    "chat": 1800,
    "code": 900,
    "research": 700
  }
}
```

## User Dashboard

In the chat frontend, a settings/account page shows:

- Current month token usage (bar chart or progress bar)
- API keys: list, create, revoke
- Per-key usage breakdown
- Tool usage breakdown

This page calls `/api/usage/me` and `/api/keys`.

## Docker Compose Addition

```yaml
  api-gateway:
    build: ./gateway
    restart: unless-stopped
    ports:
      - "127.0.0.1:8070:8070"
    volumes:
      - gateway_data:/data
      - ./config/quotas.yml:/data/quotas.yml:ro
    environment:
      SECRET_KEY: ${AUTH_SECRET_KEY}  # same key for verifying session cookies
      CLUSTER_TUNNEL: http://127.0.0.1:18080
      ADMIN_EMAILS: ${ADMIN_EMAILS:-admin@muninai.org}
```

## File Location

```
munin-vps/
├── gateway/
│   ├── Dockerfile
│   ├── main.py           # FastAPI: proxy, key validation, rate limiting
│   ├── requirements.txt
│   └── models.py         # Pydantic models for requests/responses
├── config/
│   └── quotas.yml        # Rate limit configuration
```

## Implementation Priority

1. **Proxy + usage logging** — get the gateway passing traffic and logging (no keys yet)
2. **API key CRUD** — create, list, revoke keys
3. **API key validation** — Bearer token auth on `api.muninai.org`
4. **Rate limiting** — per-user quotas
5. **Usage dashboard** — frontend stats page
