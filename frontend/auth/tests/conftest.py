"""Test setup: point DB_PATH + WHITELIST_PATH at a tmp dir before importing main."""
import os
import sys
from pathlib import Path

import pytest


# Add auth dir to import path so `import main` works when pytest is run from elsewhere.
AUTH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AUTH_DIR))


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    """Isolate DB_PATH and WHITELIST_PATH for a single test."""
    db_path = tmp_path / "sessions.db"
    csv_path = tmp_path / "whitelist.csv"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WHITELIST_PATH", str(csv_path))
    monkeypatch.setenv("SECRET_KEY", "test-only")

    # Force a fresh import of main so it picks up the patched env.
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main."):
            del sys.modules[mod]
    import main  # noqa: E402

    # Override the module-level Path constants too (env is read at import).
    main.DB_PATH = db_path
    main.WHITELIST_PATH = csv_path
    yield main
