"""request_user_key: the key a request's data is stored under."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from user_key import request_user_key  # noqa: E402


def test_user_key_wins_over_the_address():
    assert request_user_key({"X-Munin-User": "u12", "X-Munin-Email": "a@b.org"}) == "u12"


def test_address_is_the_key_for_callers_without_one():
    """Scripts and benchmarks predate user keys and keep working."""
    assert request_user_key({"X-Munin-Email": " Bench@Example.org "}) == "bench@example.org"


def test_no_identity_is_none():
    assert request_user_key({}) is None
    assert request_user_key({"X-Munin-User": "  "}) is None
