"""
Standalone tests for the maintenance-mode flag reader (maintenance.py).

Run inside the retrieval container:
    docker exec munin-retrieval python /app/tests/test_maintenance_flag.py
Or locally:
    python backend/retrieval/tests/test_maintenance_flag.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from maintenance import read_maintenance  # noqa: E402


def _check(name: str, ok: bool, detail: str = "") -> bool:
    label = "PASS" if ok else "FAIL"
    print(f"[{label}] {name}{(' - ' + detail) if detail and not ok else ''}")
    return ok


def test_absent_flag_is_inactive() -> bool:
    res = read_maintenance("/nonexistent/maintenance.json")
    return _check(
        "absent flag file -> {'active': False}",
        res == {"active": False},
        f"res={res}",
    )


def test_present_flag_is_active_with_fields() -> bool:
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "maintenance.json")
        Path(path).write_text(json.dumps({
            "message": "Running NTL9 experiments",
            "since": "2026-05-22T14:30:00Z",
        }))
        res = read_maintenance(path)
    return _check(
        "present flag -> active with message + since",
        res == {
            "active": True,
            "message": "Running NTL9 experiments",
            "since": "2026-05-22T14:30:00Z",
        },
        f"res={res}",
    )


def test_present_flag_empty_fields() -> bool:
    """`munin-maintenance on` with no message writes {"message": "", ...}."""
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "maintenance.json")
        Path(path).write_text(json.dumps({"message": "", "since": ""}))
        res = read_maintenance(path)
    return _check(
        "present flag with empty fields is still active",
        res.get("active") is True and res.get("message") == "",
        f"res={res}",
    )


def test_malformed_flag_fails_safe_to_active() -> bool:
    """A present-but-corrupt flag file means the operator intended
    maintenance — show it (active) rather than swallow the state."""
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "maintenance.json")
        Path(path).write_text("{ not valid json")
        res = read_maintenance(path)
    return _check(
        "malformed flag file fails safe to active",
        res.get("active") is True,
        f"res={res}",
    )


def test_missing_keys_coerced() -> bool:
    """A flag file with no message/since keys still yields string fields."""
    with tempfile.TemporaryDirectory() as d:
        path = str(Path(d) / "maintenance.json")
        Path(path).write_text("{}")
        res = read_maintenance(path)
    return _check(
        "flag with no message/since keys yields empty strings",
        res == {"active": True, "message": "", "since": ""},
        f"res={res}",
    )


TESTS = [
    test_absent_flag_is_inactive,
    test_present_flag_is_active_with_fields,
    test_present_flag_empty_fields,
    test_malformed_flag_fails_safe_to_active,
    test_missing_keys_coerced,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            ok = t()
        except Exception:
            ok = False
            print(f"[FAIL] {t.__name__} - exception:")
            traceback.print_exc()
        passed += int(ok)
        failed += int(not ok)
    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
