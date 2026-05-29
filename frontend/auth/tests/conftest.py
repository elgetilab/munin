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
    """Isolate DB_PATH / WHITELIST_PATH / CONTRIBUTORS_PATH per test."""
    db_path = tmp_path / "sessions.db"
    csv_path = tmp_path / "whitelist.csv"
    yaml_path = tmp_path / "contributors.yml"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("WHITELIST_PATH", str(csv_path))
    monkeypatch.setenv("CONTRIBUTORS_PATH", str(yaml_path))
    monkeypatch.setenv("SECRET_KEY", "test-only")

    # Force a fresh import of main so it picks up the patched env.
    for mod in list(sys.modules):
        if mod == "main" or mod.startswith("main."):
            del sys.modules[mod]
    import main  # noqa: E402

    # Override the module-level Path constants too (env is read at import).
    main.DB_PATH = db_path
    main.WHITELIST_PATH = csv_path
    main.CONTRIBUTORS_PATH = yaml_path
    yield main


@pytest.fixture
def client(auth_env):
    """TestClient with the schema initialised but no users seeded."""
    from fastapi.testclient import TestClient

    auth_env.init_db()
    with TestClient(auth_env.app) as c:
        # Strip cookies the test client adds (we set them per-call).
        c.cookies.clear()
        yield c


def make_user(auth_env, email: str, name: str, role: str = "user",
              group: str | None = None, username: str | None = None) -> int:
    conn = auth_env.get_db()
    user_id = auth_env._insert_user(conn, name=name, role=role,
                                    research_group=group, username=username)
    auth_env._attach_email(conn, user_id, email, is_primary=True)
    conn.commit()
    conn.close()
    return user_id


def session_cookie(auth_env, email: str, name: str = "Test") -> str:
    """Create a session row + return its signed cookie value."""
    return auth_env.create_session(email, name)
