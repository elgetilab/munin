"""gateway_headers(): env token, token file, precedence, and the unset case."""

import pytest

from munin_bench.gateway import gateway_headers


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MUNIN_GATEWAY_TOKEN", raising=False)
    monkeypatch.delenv("MUNIN_GATEWAY_TOKEN_FILE", raising=False)


def test_unset_sends_no_header():
    assert gateway_headers() == {}


def test_env_token(monkeypatch):
    monkeypatch.setenv("MUNIN_GATEWAY_TOKEN", "  abc123\n")
    assert gateway_headers() == {"X-Munin-Gateway-Token": "abc123"}


def test_token_file(monkeypatch, tmp_path):
    f = tmp_path / "token"
    f.write_text("fromfile\n")
    monkeypatch.setenv("MUNIN_GATEWAY_TOKEN_FILE", str(f))
    assert gateway_headers() == {"X-Munin-Gateway-Token": "fromfile"}


def test_env_wins_over_file(monkeypatch, tmp_path):
    f = tmp_path / "token"
    f.write_text("fromfile")
    monkeypatch.setenv("MUNIN_GATEWAY_TOKEN", "fromenv")
    monkeypatch.setenv("MUNIN_GATEWAY_TOKEN_FILE", str(f))
    assert gateway_headers() == {"X-Munin-Gateway-Token": "fromenv"}


def test_unreadable_file_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("MUNIN_GATEWAY_TOKEN_FILE", str(tmp_path / "missing"))
    with pytest.raises(OSError):
        gateway_headers()
