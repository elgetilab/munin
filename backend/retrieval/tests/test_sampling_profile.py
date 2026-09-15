"""Sampling profile and thinking mode: the two things a backbone swap changes
in every request body, pinned so the Qwen3.8 reference deployment is
byte-identical to before they became configurable (2026-09-15).

Everything drives a pure function with an explicit mapping or argument; no
importlib.reload (see test_llm_endpoint_config.py for why).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pytest
except ModuleNotFoundError:
    print("[SKIP] test_sampling_profile requires pytest. Run it on the host:\n"
          "       cd backend/retrieval && python -m pytest tests/test_sampling_profile.py")
    raise SystemExit(0)

import database
import personas

REPO_PERSONAS = Path(__file__).resolve().parents[3] / "shared" / "personas"
DEPLOYED_PERSONAS = Path("/app/personas")

# The values the persona files carried on every committed benchmark run, and
# therefore what the built-in profile MUST reproduce with no env set.
QWEN3_CHAT = {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5}
QWEN3_CODE = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0}
OPENAI_GPTOSS = {"temperature": 1.0, "top_p": 1.0}


def _persona(name: str) -> dict:
    for root in (REPO_PERSONAS, DEPLOYED_PERSONAS):
        p = root / f"{name}.json"
        if p.exists():
            return json.loads(p.read_text())
    pytest.skip(f"no {name}.json persona in {REPO_PERSONAS} or {DEPLOYED_PERSONAS}")


# --- sampling profile -------------------------------------------------------

def test_builtin_profile_is_the_qwen3_set():
    prof = database.resolve_sampling_profile({})
    assert prof == {"default": QWEN3_CHAT, "code": QWEN3_CODE}


@pytest.mark.parametrize("name,expected", [
    ("chat", QWEN3_CHAT), ("research", QWEN3_CHAT), ("code", QWEN3_CODE),
])
def test_shipped_personas_reproduce_the_pre_migration_bodies(name, expected):
    """The load-bearing assertion: with no SAMPLING_* set, each shipped
    persona yields exactly the numbers it used to carry inline."""
    persona = _persona(name)
    params = persona.get("params") or {}
    for k in ("temperature", "top_p", "top_k", "min_p", "presence_penalty"):
        assert k not in params, f"{name}.json still carries {k}; it belongs to the model profile now"
    got = personas.sampling_params(persona, profile=database.resolve_sampling_profile({}))
    got.pop("max_tokens", None)
    assert got == expected


def test_gpt_oss_profile_from_env_replaces_the_set_entirely():
    env = {"SAMPLING_DEFAULT": json.dumps(OPENAI_GPTOSS),
           "SAMPLING_CODE": json.dumps(OPENAI_GPTOSS)}
    prof = database.resolve_sampling_profile(env)
    assert prof == {"default": OPENAI_GPTOSS, "code": OPENAI_GPTOSS}
    # no top_k / min_p / presence_penalty leak in from the Qwen fallback
    assert personas.sampling_params(_persona("chat"), profile=prof) == OPENAI_GPTOSS
    assert personas.sampling_params(_persona("code"), profile=prof) == OPENAI_GPTOSS


def test_one_class_set_leaves_the_other_on_its_fallback():
    prof = database.resolve_sampling_profile({"SAMPLING_CODE": json.dumps({"temperature": 0.2})})
    assert prof["default"] == QWEN3_CHAT
    assert prof["code"] == {"temperature": 0.2}


def test_blank_variable_means_unset():
    assert database.resolve_sampling_profile({"SAMPLING_DEFAULT": "  "})["default"] == QWEN3_CHAT


@pytest.mark.parametrize("raw", ["not json", "[1,2]", '{"temperatur": 1.0}', '"1.0"'])
def test_malformed_profile_fails_at_import_not_at_the_turn(raw):
    with pytest.raises(ValueError):
        database.resolve_sampling_profile({"SAMPLING_DEFAULT": raw})


def test_persona_numeric_keys_remain_an_escape_hatch():
    persona = {"id": "x", "params": {"sampling_class": "default", "temperature": 0.3,
                                     "max_tokens": 512}}
    got = personas.sampling_params(persona, profile=database.resolve_sampling_profile({}))
    assert got == {**QWEN3_CHAT, "temperature": 0.3, "max_tokens": 512}


def test_unknown_sampling_class_is_rejected():
    with pytest.raises(ValueError):
        database.model_sampling("creative", database.resolve_sampling_profile({}))


def test_model_sampling_returns_a_copy():
    prof = database.resolve_sampling_profile({})
    a = database.model_sampling("default", prof)
    a["temperature"] = 0.0
    assert prof["default"]["temperature"] == 1.0


# --- thinking mode ----------------------------------------------------------

def test_default_mode_is_the_qwen_form():
    assert database.resolve_thinking_mode({}) == "enable_thinking"
    assert database.thinking_off_fields(enabled=True, mode="enable_thinking") == \
        {"chat_template_kwargs": {"enable_thinking": False}}


def test_effort_low_for_a_model_that_cannot_disable_reasoning():
    assert database.resolve_thinking_mode({"LLM_THINKING_MODE": "effort_low"}) == "effort_low"
    assert database.thinking_off_fields(enabled=True, mode="effort_low") == \
        {"chat_template_kwargs": {"reasoning_effort": "low"}}


def test_none_sends_nothing():
    assert database.thinking_off_fields(enabled=True, mode="none") == {}


def test_toggle_off_wins_over_any_mode():
    for mode in database.THINKING_MODES:
        assert database.thinking_off_fields(enabled=False, mode=mode) == {}


@pytest.mark.parametrize("raw", ["sometimes", "ENABLE_THINKING", "low"])
def test_unknown_mode_fails_at_import(raw):
    with pytest.raises(ValueError):
        database.resolve_thinking_mode({"LLM_THINKING_MODE": raw})


def test_blank_mode_is_the_default():
    assert database.resolve_thinking_mode({"LLM_THINKING_MODE": ""}) == "enable_thinking"
