"""Test setup for hook-service tests."""
import sys
from pathlib import Path

import pytest


UPLOAD_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(UPLOAD_DIR))


@pytest.fixture
def hook_env(monkeypatch):
    """Fresh hook_service import per test."""
    for mod in list(sys.modules):
        if mod == "hook_service":
            del sys.modules[mod]
    monkeypatch.setenv("KB_GATE_TOKEN", "test-token")
    monkeypatch.setenv("AUTH_CHECK_URL", "http://auth.test/admin/check-role")
    monkeypatch.setenv("KB_GATE_FAIL_OPEN", "false")
    import hook_service  # noqa: E402
    yield hook_service
