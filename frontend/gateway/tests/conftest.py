"""Test setup: point DB_PATH / QUOTAS_PATH at a tmp dir before importing main."""
import sys
from pathlib import Path

import pytest

# Add gateway dir to import path so `import main` works when pytest runs elsewhere.
GATEWAY_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(GATEWAY_DIR))


@pytest.fixture
def gw_env(tmp_path, monkeypatch):
    """Isolate DB_PATH / QUOTAS_PATH per test."""
    db_path = tmp_path / "gateway.db"
    quotas_path = tmp_path / "quotas.yml"
    quotas_path.write_text("defaults:\n  requests_per_minute: 1000\n")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("QUOTAS_PATH", str(quotas_path))

    # Force a fresh import of main so it picks up the patched env.
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main."):
            del sys.modules[mod]
    import main  # noqa: E402

    # Override the module-level Path constants too (env is read at import).
    main.DB_PATH = db_path
    main.QUOTAS_PATH = quotas_path
    yield main


@pytest.fixture
def client(gw_env):
    """TestClient with the schema initialised."""
    from fastapi.testclient import TestClient

    gw_env.init_db()
    with TestClient(gw_env.app) as c:
        c.cookies.clear()
        yield c
