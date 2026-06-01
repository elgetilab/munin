"""Tests for contributors_sync.py (P1 #11).

Exercises the atomic-write-on-diff path with a mock httpx client.
Run locally:
    cd backend/retrieval && python -m pytest tests/test_contributors_sync.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest


# Make `import contributors_sync` work when pytest is run from backend/retrieval.
RETRIEVAL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RETRIEVAL_DIR))


class _MockResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _MockClient:
    def __init__(self, responses):
        # responses: list of strings OR (text, status) tuples; consumed in order.
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {})))
        item = self._responses.pop(0)
        if isinstance(item, tuple):
            return _MockResponse(item[0], item[1])
        return _MockResponse(item)


def test_atomic_write_creates_file(tmp_path):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    changed = cs._atomic_write(target, "contributors: []\n")
    assert changed is True
    assert target.read_text() == "contributors: []\n"


def test_atomic_write_no_change_returns_false(tmp_path):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    target.write_text("contributors: []\n")
    changed = cs._atomic_write(target, "contributors: []\n")
    assert changed is False


def test_atomic_write_replaces_when_content_differs(tmp_path):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    target.write_text("old\n")
    changed = cs._atomic_write(target, "new\n")
    assert changed is True
    assert target.read_text() == "new\n"


def test_fetch_once_writes_when_changed(tmp_path, monkeypatch):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    monkeypatch.setattr(cs, "CONTRIBUTORS_CONFIG_PATH", str(target))
    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_URL", "https://auth.example/admin/contributors.yaml")
    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_TOKEN", "tok-123")

    client = _MockClient(["contributors:\n  - email: x@y\n"])
    changed = asyncio.run(cs.fetch_once(client))
    assert changed is True
    assert target.read_text() == "contributors:\n  - email: x@y\n"
    # Token must have been sent.
    assert client.calls[0][1]["Authorization"] == "Bearer tok-123"


def test_fetch_once_no_change_returns_false(tmp_path, monkeypatch):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    target.write_text("contributors: []\n")
    monkeypatch.setattr(cs, "CONTRIBUTORS_CONFIG_PATH", str(target))
    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_URL", "https://auth.example/admin/contributors.yaml")
    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_TOKEN", "")

    client = _MockClient(["contributors: []\n"])
    changed = asyncio.run(cs.fetch_once(client))
    assert changed is False
    # No Authorization header when token is empty.
    assert "Authorization" not in client.calls[0][1]


def test_fetch_once_propagates_http_error(tmp_path, monkeypatch):
    import contributors_sync as cs

    target = tmp_path / "contributors.yml"
    monkeypatch.setattr(cs, "CONTRIBUTORS_CONFIG_PATH", str(target))
    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_URL", "https://auth.example/admin/contributors.yaml")

    client = _MockClient([("error body", 500)])
    with pytest.raises(RuntimeError):
        asyncio.run(cs.fetch_once(client))
    assert not target.exists()


def test_start_sync_task_noop_when_url_unset(monkeypatch):
    import contributors_sync as cs

    monkeypatch.setattr(cs, "CONTRIBUTORS_SYNC_URL", "")
    # Need a running loop to create tasks; the function returns None first.
    assert cs.start_sync_task() is None
