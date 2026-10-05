"""LLM_API_KEY becomes a Bearer header for the model endpoint; unset, none."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import resolve_llm_headers  # noqa: E402


def test_no_key_no_header():
    assert resolve_llm_headers({}) == {}
    assert resolve_llm_headers({"LLM_API_KEY": "  "}) == {}


def test_key_is_a_bearer_token():
    assert resolve_llm_headers({"LLM_API_KEY": "sk-abc"}) == {"Authorization": "Bearer sk-abc"}
