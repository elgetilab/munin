"""Usage capture and the publication-usage summary (docs/DOCS-SYNC-AND-CREDIT-PLAN.md)."""
import json
import sqlite3

import httpx
import pytest

FACTS = {
    "backbone": {"served_name": "sage-a1.5", "display": "Sage-A1.5"},
    "history": [
        {"slug": "qwen3.8-27b", "display": "Qwen3.8-27B", "from": "2026-08-26T00:00:00Z",
         "to": "2026-10-21T00:00:00Z", "model_id": "cyankiwi/Qwen3.8-27B-AWQ-INT4",
         "revision": "63768c10df38", "vllm": "0.20.2rc1"},
        {"slug": "claude-haiku-5-5", "display": "Claude Haiku 5.5 (standby)",
         "from": "2026-10-21T00:00:00Z", "to": "2026-11-12T17:46:00Z", "hosted": "Anthropic API"},
        {"slug": "sage-a1.5", "display": "Sage-A1.5", "from": "2026-11-12T17:46:00Z", "to": None},
    ],
    "catalog": {
        "sage-a1.5": {"display": "Sage-A1.5", "developer": "InternScience",
                      "checkpoint": "cyankiwi/Sage-A1.5-AWQ-INT4", "revision": "12e664ccaf57a3c8",
                      "quantization": "AWQ-INT4 g32", "vllm_version": "0.33.0",
                      "reference": {"apa": "Sage-A1.5 Team. (2026). Sage-A1.5.", "bibtex": "@misc{sage}"}},
        "claude-haiku-5-5": {"display": "Claude Haiku 5.5", "hosted_by": "Anthropic API",
                             "reference": {"apa": "Anthropic. (2026). Claude Haiku 5.5.", "bibtex": "@misc{haiku}"}},
    },
    "references": {"munin": {"apa": "Fischer et al. (2026). MuninAI.", "bibtex": "@misc{munin}"},
                   "vllm": {"apa": "Kwon et al. (2023). vLLM.", "bibtex": "@inproceedings{vllm}"}},
}


def sse(*events):
    return "".join(f"event: {e}\r\ndata: {json.dumps(d)}\r\n\r\n" for e, d in events).encode()


def test_scanner_reads_split_events(gw_env):
    body = sse(("routing", {"profile": "research"}),
               ("conversation", {"id": "c1", "title": None, "is_new": True}),
               ("tool_call", {"name": "paper_search", "arguments": {}}),
               ("done", {"usage": {"prompt_tokens": 70, "completion_tokens": 30, "total_tokens": 100},
                         "model": "sage-a1.5"}))
    scan = gw_env.SseUsageScanner()
    for i in range(0, len(body), 7):  # events split across arbitrary reads
        scan.feed(body[i:i + 7])
    assert (scan.tokens_input, scan.tokens_output, scan.tokens_total) == (70, 30, 100)
    assert scan.model == "sage-a1.5"
    assert scan.conversation_id == "c1"
    assert scan.persona == "research"
    assert scan.tools == ["paper_search"]


def test_scanner_openai_chunks(gw_env):
    body = (b'data: {"model": "sage-a1.5", "choices": [{"delta": {"content": "hi"}}]}\n\n'
            b'data: {"model": "sage-a1.5", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}\n\n'
            b"data: [DONE]\n\n")
    scan = gw_env.SseUsageScanner()
    scan.feed(body)
    assert scan.model == "sage-a1.5" and scan.tokens_total == 7


def test_migration_adds_columns(gw_env, tmp_path):
    conn = sqlite3.connect(gw_env.DB_PATH)
    conn.execute("""CREATE TABLE usage_log (id TEXT PRIMARY KEY, user_email TEXT NOT NULL,
        timestamp TEXT NOT NULL, persona TEXT, source TEXT NOT NULL, api_key_id TEXT,
        endpoint TEXT NOT NULL, tokens_input INTEGER, tokens_output INTEGER, tokens_total INTEGER,
        tools_invoked TEXT, duration_ms INTEGER, status_code INTEGER, model TEXT)""")
    conn.commit()
    conn.close()
    gw_env.init_db()
    gw_env.init_db()  # idempotent
    cols = {r[1] for r in sqlite3.connect(gw_env.DB_PATH).execute("PRAGMA table_info(usage_log)")}
    assert {"group_id", "conversation_id"} <= cols
    gw_env.log_usage("u1", "browser", None, "/api/chat/completions", tokens_input=3,
                     tokens_output=4, model="sage-a1.5", group_id="g1", conversation_id="c9")
    row = sqlite3.connect(gw_env.DB_PATH).execute(
        "SELECT tokens_total, model, group_id, conversation_id FROM usage_log").fetchone()
    assert row == (7, "sage-a1.5", "g1", "c9")


def _row(ts, model=None, tokens=10, conv="c1", endpoint="/api/chat/completions"):
    return {"timestamp": ts, "endpoint": endpoint, "model": model, "tokens_input": 0,
            "tokens_output": 0, "tokens_total": tokens, "conversation_id": conv}


def test_summary_infers_models_from_history(gw_env):
    rows = [
        _row("2026-09-01T10:00:00+00:00"),                       # Qwen3.8 era, no model column
        _row("2026-11-01T10:00:00+00:00", conv="c2"),            # standby era
        _row("2026-11-20T10:00:00+00:00", model="sage-a1.5", conv="c3"),
        _row("2026-11-20T11:00:00+00:00", model="sage-a1.5", conv="c3"),
        _row("2026-11-20T12:00:00+00:00", tokens=0, endpoint="/api/upload"),  # not LLM use
    ]
    dr = [{"created_at": 1795500000.0, "model": "sage-a1.5", "tokens_prompt": 50,
           "tokens_completion": 50, "tokens_total": 100}]
    models = gw_env.summarize_usage(rows, dr, FACTS)
    by = {m["model"]: m for m in models}
    assert set(by) == {"qwen3.8-27b", "claude-haiku-5-5", "sage-a1.5"}
    assert by["qwen3.8-27b"]["inferred_requests"] == 1
    assert by["qwen3.8-27b"]["checkpoint"] == "cyankiwi/Qwen3.8-27B-AWQ-INT4"
    assert by["sage-a1.5"]["requests"] == 2 and by["sage-a1.5"]["conversations"] == 1
    assert by["sage-a1.5"]["deep_research_jobs"] == 1 and by["sage-a1.5"]["tokens_total"] == 120
    assert by["claude-haiku-5-5"]["hosted_by"] == "Anthropic API"

    st = gw_env.build_statement(models, FACTS)
    assert "[PURPOSE]" in st["methods"] and "[WHO / HOW]" in st["methods"]
    assert "Sage-A1.5" in st["methods"] and "revision 12e664ccaf57" in st["methods"]
    assert "Anthropic API" in st["methods"]
    assert st["references_apa"][0].startswith("Fischer")
    assert "@inproceedings{vllm}" in st["bibtex"]


def test_system_facts_survive_backend_down(gw_env, client, monkeypatch, tmp_path):
    gw_env.SYSTEM_FACTS_PATH = tmp_path / "facts.json"
    gw_env._system_cache.clear()

    class Down:
        async def get(self, *a, **k):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(gw_env, "backend_client", lambda: Down())
    r = client.get("/api/system")
    assert r.status_code == 200 and r.json() == {"available": False, "stale": True}

    class Up:
        async def get(self, *a, **k):
            return httpx.Response(200, json=FACTS)

    gw_env._system_cache.clear()
    monkeypatch.setattr(gw_env, "backend_client", lambda: Up())
    assert client.get("/api/system").json()["available"] is True

    gw_env._system_cache.clear()  # restart: memory gone, disk copy remains
    monkeypatch.setattr(gw_env, "backend_client", lambda: Down())
    j = client.get("/api/system").json()
    assert j["stale"] is True and j["backbone"]["display"] == "Sage-A1.5"


def test_summary_endpoint_auth_and_csv(gw_env, client, monkeypatch):
    gw_env._system_cache.update(facts=FACTS, at=1e18)
    monkeypatch.setattr(gw_env, "_dr_usage", lambda *a: _async([]))
    assert client.get("/api/usage/me/summary").status_code == 401
    h = {"X-Munin-User": "u1", "X-Munin-Email": "a@example.org"}
    gw_env.log_usage("u1", "browser", None, "/api/chat/completions", tokens_total=9, model="sage-a1.5")
    assert client.get("/api/usage/me/summary?from=2026-13-01", headers=h).status_code == 400
    j = client.get("/api/usage/me/summary", headers=h).json()
    assert j["models"][0]["model"] == "sage-a1.5" and j["totals"]["tokens_total"] == 9
    csv = client.get("/api/usage/me/summary?format=csv", headers=h)
    assert csv.headers["content-type"].startswith("text/csv") and "sage-a1.5" in csv.text
    # group summary: plain users refused, leaders see their group's totals only
    assert client.get("/api/usage/group/summary", headers=h).status_code == 403
    lh = {**h, "X-Munin-Role": "group_leader", "X-Munin-Group": "g1"}
    gw_env.log_usage("u2", "browser", None, "/api/chat/completions", tokens_total=5,
                     model="sage-a1.5", group_id="g1")
    g = client.get("/api/usage/group/summary", headers=lh).json()
    assert g["group"] == "g1" and g["totals"]["tokens_total"] == 5 and g["members_active"] == 1


async def _async(v):
    return v
