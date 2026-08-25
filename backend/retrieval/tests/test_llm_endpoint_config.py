"""Endpoint portability: the thinking toggle and the LLM_BASE_URL aliases.

Munin talks plain OpenAI-compatible HTTP, but it sends one field that is not in
the OpenAI schema: `chat_template_kwargs`, vLLM's passthrough to the chat
template, used to suppress reasoning on mechanical sub-tasks. Strict servers
reject unknown top-level fields with a 400, so it is gated.

The load-bearing assertion here is that the DEFAULT is unchanged: the reference
deployment must keep sending exactly what it sent before this was gated.

NOTE ON STYLE: everything below drives a pure function with an explicit mapping
or flag. An earlier version of this file used `importlib.reload(database)` under
`monkeypatch.setenv` instead. It passed in isolation and failed inside the full
suite, because once another module has imported `database` a reload does not
reliably pick the environment back up. That is a trap worth not re-laying: if
you add a case here, extend the pure function, do not reach for reload.
"""

import pytest

from database import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL_NAME,
    LLM_THINKING_TOGGLE,
    _env_flag,
    resolve_llm_endpoint,
    thinking_off,
    thinking_off_fields,
    reasoning_effort_fields,
)

FIELD = {"chat_template_kwargs": {"enable_thinking": False}}


# --- the toggle -------------------------------------------------------------

def test_default_sends_the_field_exactly_as_before():
    """Regression guard on the reference deployment. If this flips, every
    mechanical sub-task silently stops suppressing its reasoning trace."""
    assert LLM_THINKING_TOGGLE is True
    assert thinking_off_fields() == FIELD


def test_disabled_omits_the_field_entirely():
    """Absent, not `{"chat_template_kwargs": {}}` and not a null: a strict
    endpoint rejects the unknown key, not merely a bad value."""
    assert thinking_off_fields(enabled=False) == {}


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off", "", "  "])
def test_falsey_spellings_disable(raw):
    assert _env_flag("X", True, {"X": raw}) is False


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
def test_truthy_spellings_enable(raw):
    assert _env_flag("X", False, {"X": raw}) is True


def test_unset_falls_back_to_the_given_default():
    assert _env_flag("X", True, {}) is True
    assert _env_flag("X", False, {}) is False


def test_mutating_form_matches_the_splat_form():
    body = {"model": "m", "messages": []}
    assert thinking_off(body) is body                       # mutates in place
    assert body == {"model": "m", "messages": [], **FIELD}


def test_mutating_form_is_a_noop_when_disabled():
    body = {"model": "m"}
    thinking_off(body, enabled=False)
    assert body == {"model": "m"}


def test_toggle_overwrites_only_its_own_key():
    """The mutating form is applied to bodies built up in steps. It replaces
    the whole `chat_template_kwargs` value, so record that: no caller today
    sets a sibling key, and one that did would need this revisited."""
    body = {"chat_template_kwargs": {"something_else": 1}}
    thinking_off(body)
    assert body == FIELD


# --- endpoint resolution ----------------------------------------------------

def test_defaults_when_nothing_is_set():
    assert resolve_llm_endpoint({}) == (DEFAULT_LLM_BASE_URL, DEFAULT_LLM_MODEL_NAME)


def test_legacy_vllm_vars_still_work():
    """cluster.env and every vLLM launch script export these. Breaking them
    would be a silent misconfiguration on the next deploy, not a loud one."""
    assert resolve_llm_endpoint(
        {"VLLM_URL": "http://legacy:8000", "VLLM_MODEL_NAME": "legacy"}
    ) == ("http://legacy:8000", "legacy")


def test_new_names_work():
    assert resolve_llm_endpoint(
        {"LLM_BASE_URL": "http://ollama:11434", "LLM_MODEL_NAME": "qwen3:32b"}
    ) == ("http://ollama:11434", "qwen3:32b")


def test_new_names_win_over_legacy():
    """Both set is a half-migrated host; the new name is the one the operator
    edited most recently."""
    assert resolve_llm_endpoint({
        "VLLM_URL": "http://old:8000", "LLM_BASE_URL": "http://new:9000",
        "VLLM_MODEL_NAME": "old", "LLM_MODEL_NAME": "new",
    }) == ("http://new:9000", "new")


def test_empty_new_name_falls_back_rather_than_blanking():
    """`LLM_BASE_URL=` in an .env file is an unset knob. Letting it win would
    send every request to a relative path."""
    assert resolve_llm_endpoint(
        {"LLM_BASE_URL": "", "VLLM_URL": "http://legacy:8000"}
    )[0] == "http://legacy:8000"


def test_each_name_resolves_independently():
    """URL from one scheme and model from the other must not interfere."""
    assert resolve_llm_endpoint(
        {"LLM_BASE_URL": "http://new:9000", "VLLM_MODEL_NAME": "legacy"}
    ) == ("http://new:9000", "legacy")


def test_module_constants_agree_with_the_resolver():
    """Ties the exported constants to the tested function, so the two cannot
    drift apart without a failure here."""
    import database
    assert (database.VLLM_URL, database.VLLM_MODEL_NAME) == resolve_llm_endpoint()


# --- reasoning effort -------------------------------------------------------
# Qwen3.8's own default is "xhigh", which at the production 16K output cap spent
# the entire budget inside <think> and returned an EMPTY answer (measured
# 2026-08-25). These pin the parts of that fix that fail silently.

def test_default_pins_medium():
    """If this flips to xhigh, hard turns start returning empty answers."""
    assert reasoning_effort_fields() == {
        "chat_template_kwargs": {"reasoning_effort": "medium"}}


def test_blank_omits_the_field_entirely():
    """Absent, not an empty string: the chat template raises on an unknown
    value, which surfaces as a 400 on EVERY chat turn, not a degraded one."""
    assert reasoning_effort_fields(effort="") == {}
    assert reasoning_effort_fields(effort="  ") == {}
    assert reasoning_effort_fields(effort="default") == {}


def test_rides_the_thinking_toggle():
    """Same non-OpenAI passthrough, so an endpoint strict enough to 400 on
    `chat_template_kwargs` must not receive this field either."""
    assert reasoning_effort_fields(enabled=False) == {}


def test_does_not_collide_with_the_thinking_field():
    """Both helpers write `chat_template_kwargs`. A caller that splats both
    into one literal would silently keep only the last, so nothing in the tree
    may do that; these are merged, never splatted together."""
    assert set(thinking_off_fields()) == set(reasoning_effort_fields())
