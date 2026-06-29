"""
Persona loading + serving.

Personas are defined as JSON files in PERSONAS_DIR (one per persona id). The
frontend expects a simplified public shape (id, name, description, icon_url,
tags, capabilities, prompt_suggestions). Internally we also keep the raw
vLLM sampling params and system prompt.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)

PERSONAS_DIR = os.getenv("PERSONAS_DIR", "/app/personas")
# The base routing profile: supplies the (now shared Munin) frame and the
# router's fallback target. chat/code/research are INTERNAL routing profiles.
DEFAULT_PERSONA_ID = os.getenv("DEFAULT_PERSONA", "chat")
# The single USER-FACING identity. Selecting it (or omitting a persona) means
# "let the router decide" (auto-route, no pin). It is NOT a loaded persona id;
# the chat path maps it to no-pin. Power users still steer with /chat//code//research.
AUTO_PERSONA_ID = os.getenv("AUTO_PERSONA", "munin")

_personas: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Schema (P2 #20). The loaded `_personas` dict still stores raw dicts so
# accessors like `params.get("temperature")` keep working; the Pydantic
# model below is a validation gate only — `model_validate()` rejects
# typos and out-of-bounds values at load time, before any consumer sees
# the entry.
#
# `extra="forbid"` at every level is the whole point: it makes
# `params.temprature: 1.0` or `meta.profile_imag_url: ...` an error
# instead of a silent default.
# ---------------------------------------------------------------------------


class _PromptSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    subtitle: str
    content: str


class _TagDict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str


class _Params(BaseModel):
    """Sampling + tool-use parameters. Every field is optional — defaults
    are applied by the consumer (vLLM, personas.max_turns, ...) so a
    persona that doesn't set `top_k` still works."""
    model_config = ConfigDict(extra="forbid")

    system: Optional[str] = None
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(default=None, ge=0)
    min_p: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, gt=0)
    # Bounds match personas.max_turns clamp range below; reject out-of-bounds
    # rather than silently clamping so the operator sees the typo at boot.
    max_turns: Optional[int] = Field(default=None, ge=1, le=30)
    # A4b SOFT bias (replaces the retired hard `tool_allowlist`): the profile's
    # high-value deferred tools to SURFACE in the resident schema beyond CORE,
    # so the model calls them without a tool_search hop. Additive only — every
    # tool stays reachable via tool_search; nothing is rejected.
    resident_tools: Optional[list[str]] = None
    tool_allowlist: Optional[list[str]] = None
    # P2 #24 Phase 2: list of MCP tool names that REQUIRE an approved
    # plan before they run. The preToolUse hook in
    # ``hooks/plan_approval.py`` checks this list per dispatch; when a
    # listed tool is called without an approval, the call short-circuits
    # and the UI shows Approve / Approve-all / Edit / Reject buttons.
    # Entries are cross-checked against MCP_TOOLS at startup via
    # ``load_personas`` so a typo (`delegate_to_persoona`) is caught
    # at boot rather than at first user gating attempt.
    plan_approval: Optional[list[str]] = None


class _Meta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: Optional[str] = None
    profile_image_url: Optional[str] = None
    tags: Optional[list[Union[str, _TagDict]]] = None
    capabilities: Optional[dict[str, bool]] = None
    toolIds: Optional[list[str]] = None


class _Persona(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(..., min_length=1)
    version: Optional[str] = None
    name: Optional[str] = None
    description: Optional[str] = None
    params: Optional[_Params] = None
    meta: Optional[_Meta] = None
    prompt_suggestions: Optional[list[_PromptSuggestion]] = None


def _tag_list(raw: Any) -> list[str]:
    """Normalize a persona's tag field into a plain list of strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        out: list[str] = []
        for t in raw:
            if isinstance(t, str):
                out.append(t)
            elif isinstance(t, dict) and "name" in t:
                out.append(str(t["name"]))
        return out
    return []


def _icon_url_for(persona_id: str, meta: dict) -> str:
    """
    Pick an icon URL for a persona. Prefer an explicit path from the JSON,
    otherwise serve it through our own /api/personas/{id}/icon route.
    """
    explicit = meta.get("profile_image_url")
    if explicit:
        return str(explicit)
    return f"/api/personas/{persona_id}/icon"


def _public_view(raw: dict) -> dict:
    """Shape a raw persona JSON for the frontend."""
    persona_id = raw["id"]
    meta = raw.get("meta") or {}
    return {
        "id": persona_id,
        "name": raw.get("name") or persona_id,
        "description": raw.get("description") or meta.get("description") or "",
        "icon_url": _icon_url_for(persona_id, meta),
        "tags": _tag_list(meta.get("tags")),
        "capabilities": meta.get("capabilities") or {},
        "prompt_suggestions": raw.get("prompt_suggestions") or [],
    }


def load_personas() -> dict[str, dict]:
    """(Re)load all persona JSON files from disk. Returns the internal map."""
    global _personas
    _personas = {}

    if not os.path.isdir(PERSONAS_DIR):
        logger.warning("Personas dir not found: %s", PERSONAS_DIR)
        return _personas

    for entry in sorted(os.listdir(PERSONAS_DIR)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(PERSONAS_DIR, entry)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Failed to load persona %s: %s", entry, e)
            continue
        # Fill in id from filename if the JSON omitted it, then validate.
        if isinstance(data, dict) and not data.get("id"):
            data["id"] = entry.removesuffix(".json")
        try:
            _Persona.model_validate(data)
        except ValidationError as e:
            logger.error("Persona %s failed validation, skipping: %s", entry, e)
            continue
        # P2 #24 Phase 2: cross-check plan_approval entries against
        # the live MCP tool registry so a typo
        # ('delegate_to_persoona') surfaces at boot rather than at
        # first gating attempt. We do this AFTER schema validation
        # so the field shape is already known-good. Lazy import to
        # avoid a startup-time circular dep (mcp.schemas <- many
        # things during init).
        approval_list = ((data.get("params") or {})
                         .get("plan_approval") or [])
        if approval_list:
            try:
                from mcp.schemas import MCP_TOOLS
                unknown = [t for t in approval_list if t not in MCP_TOOLS]
                if unknown:
                    logger.error(
                        "Persona %s: plan_approval references unknown "
                        "tool(s) %r; skipping",
                        entry, unknown,
                    )
                    continue
            except Exception as e:
                logger.warning(
                    "Persona %s: could not cross-check plan_approval "
                    "(MCP registry unavailable): %s",
                    entry, e,
                )
        persona_id = data["id"]
        # A3: the per-turn router composes base[pin] + fragment[routed] using
        # the OUTPUT-STYLE/TASK-PLANNING markers. Warn (don't reject) if a
        # persona lacks them: it still loads, but the router falls back to
        # using it whole rather than as a layerable base/fragment.
        sys_prompt = ((data.get("params") or {}).get("system") or "")
        if sys_prompt and (
            _FRAGMENT_START_AFTER not in sys_prompt
            or _FRAGMENT_END_BEFORE not in sys_prompt
        ):
            logger.warning(
                "Persona %s lacks the A3 prompt-split markers "
                "(%r / %r); router layering will fall back to whole-prompt.",
                persona_id, _FRAGMENT_START_AFTER, _FRAGMENT_END_BEFORE,
            )
        _personas[persona_id] = data
        logger.info("Loaded persona: %s", persona_id)

    return _personas


def get_persona(persona_id: str) -> Optional[dict]:
    return _personas.get(persona_id)


def persona_display_name(persona_id: Optional[str]) -> str:
    """Short, human display name for a persona id (e.g. 'Turing').

    Mirrors the icon-path convention: the configured name is like
    'Turing - Code', so we take the part before the first dash. Falls
    back to the id when the persona is unknown.
    """
    if not persona_id:
        return "another persona"
    persona = get_persona(persona_id)
    raw_name = (persona.get("name") if persona else "") or ""
    short = raw_name.split("-")[0].strip()
    return short or persona_id


def persona_handoff_note(
    active_persona_id: Optional[str],
    prior_persona_ids,
    reason: Optional[str] = None,
) -> Optional[str]:
    """Build a system note telling the active persona that earlier turns
    in this same conversation were authored by a different persona.

    Returns ``None`` when there is no cross-persona history (so legacy
    chats with NULL persona, or single-persona chats, get no marker).

    ``reason`` is set on the live delegation hop (phrased as a deliberate
    just-now handoff); left ``None`` for replayed history on later turns.
    """
    prior: list[str] = []
    for pid in prior_persona_ids or []:
        if pid and pid != active_persona_id and pid not in prior:
            prior.append(pid)
    if not prior:
        return None

    active_name = persona_display_name(active_persona_id)
    prior_names = ", ".join(persona_display_name(p) for p in prior)
    if reason:
        lead = (
            f"[persona handoff] You ({active_name}) have just taken over this "
            f"conversation from {prior_names} at the user's request "
            f"(reason: {reason})."
        )
    else:
        lead = (
            f"[persona handoff] Earlier turns in this conversation were authored "
            f"by {prior_names}; you are now {active_name}."
        )
    return (
        lead + " Those earlier turns appear above as ordinary history and are "
        "part of this same ongoing conversation, which you can read in full. "
        "Do not claim you authored them, and do not tell the user there was no "
        "switch or that you have no memory of this conversation. If the user "
        "asks, acknowledge the handoff plainly."
    )


def public_personas() -> dict:
    """Return the /api/personas payload: the SINGLE user-facing **Munin**
    identity.

    Post-consolidation (todo_v2/PERSONA-CONSOLIDATION-PLAN.md) chat/code/research
    are INTERNAL routing profiles the router selects per turn, not user-pickable
    models. The frontend shows one "Munin" and never sends a model choice; the
    chat path auto-routes. Slash commands (/chat //code //research) remain the
    power-user per-turn override. Prompt suggestions are merged across the three
    profiles so the starter prompts stay varied."""
    suggestions: list = []
    seen: set = set()
    for pid in ("chat", "research", "code"):
        p = _personas.get(pid) or {}
        for s in (p.get("prompt_suggestions") or []):
            key = (s.get("title") or s.get("content") or "").strip().lower()
            if key and key not in seen:
                seen.add(key)
                suggestions.append(s)
    munin = {
        "id": AUTO_PERSONA_ID,
        "name": "Munin",
        "description": (
            "Munin routes each question automatically. Use /research, /code, "
            "or /chat to steer a single turn explicitly."
        ),
        "icon_url": None,   # the frontend supplies the Munin brand logo
        "tags": [],
        "capabilities": {},
        "prompt_suggestions": suggestions[:8],
    }
    return {"personas": [munin], "default_persona": AUTO_PERSONA_ID}


def get_icon_path(persona_id: str) -> Optional[str]:
    """
    Resolve the on-disk path for a persona's icon SVG. The convention on the
    cluster is `personas/logos/<name>-<id>-inverted.svg`; we fall back to
    alternative filenames if that doesn't exist.
    """
    persona = get_persona(persona_id)
    if persona is None:
        return None

    logos_dir = os.path.join(PERSONAS_DIR, "logos")
    if not os.path.isdir(logos_dir):
        return None

    # Preferred: "<name>-<id>-inverted.svg"
    raw_name = (persona.get("name") or "").split("-")[0].strip().lower()
    candidates: list[str] = []
    if raw_name:
        candidates.append(f"{raw_name}-{persona_id}-inverted.svg")
        candidates.append(f"{raw_name}-{persona_id}.svg")
    candidates.append(f"logo-{persona_id}.svg")

    for name in candidates:
        path = os.path.join(logos_dir, name)
        if os.path.exists(path):
            return path
    return None


def build_system_prompt(persona: dict) -> str:
    """Return the system prompt text from a raw persona dict."""
    params = persona.get("params") or {}
    return params.get("system") or persona.get("description") or ""


# --- A3 layered prompt: base[pin] + fragment[routed] ------------------------
#
# The per-turn router (A3) composes a system prompt from the PINNED persona's
# voice/rules and the ROUTED profile's task guidance, so a (e.g.) research-
# pinned "plot those values" turn gets research voice + code task guidance
# (decision Q2 = option d). Every persona prompt factors at two markers into:
#   prefix   = CORE RULES + OUTPUT STYLE (voice/rules)  -> from the PIN
#   fragment = the task-guidance middle section         -> from the ROUTED profile
#   suffix   = TASK PLANNING + DISCOVERING TOOLS         -> from the PIN
# Composition = prefix(pin) + fragment(routed) + suffix(pin). When pin==routed
# this reconstructs the original prompt exactly (behavior-preserving on
# ordinary turns; see todo_v2/A3-PLAN.md §2a).
_FRAGMENT_START_AFTER = "=== END OUTPUT STYLE ==="
_FRAGMENT_END_BEFORE = "=== TASK PLANNING ==="


def split_system_prompt(persona: dict) -> tuple[str, str, str]:
    """Split a persona's system prompt into (prefix, fragment, suffix).

    `fragment` is the task-guidance middle (the part the router swaps per
    turn); `prefix`/`suffix` are the pin's voice/rules around it. The split is
    LOSSLESS: prefix + fragment + suffix == the original system prompt.

    If a persona lacks the boundary markers (malformed or non-standard), the
    whole prompt is returned as `fragment` with empty prefix/suffix, and
    `compose_system_prompt` falls back to the routed persona as-is.
    """
    s = build_system_prompt(persona)
    i = s.find(_FRAGMENT_START_AFTER)
    j = s.find(_FRAGMENT_END_BEFORE)
    if i < 0 or j < 0 or j < i:
        return "", s, ""
    frag_start = i + len(_FRAGMENT_START_AFTER)
    return s[:frag_start], s[frag_start:j], s[j:]


def compose_system_prompt(pin_persona: dict, routed_persona: dict) -> str:
    """Compose the layered system prompt: pin's frame + routed task fragment.

    `prefix(pin) + fragment(routed) + suffix(pin)`. When `pin is routed` (no
    re-route this turn) this equals the pin's original prompt. Degrades
    gracefully: if the PIN can't be split (no markers), returns the routed
    persona's full prompt unchanged."""
    prefix, _pin_fragment, suffix = split_system_prompt(pin_persona)
    _routed_prefix, routed_fragment, _routed_suffix = split_system_prompt(routed_persona)
    if not prefix and not suffix:
        # Pin wasn't splittable -> no clean frame to layer onto; use the
        # routed persona's full prompt rather than emit a fragment alone.
        return build_system_prompt(routed_persona)
    return prefix + routed_fragment + suffix


def sampling_params(persona: dict) -> dict:
    """Extract vLLM sampling parameters from a raw persona dict."""
    params = persona.get("params") or {}
    out: dict[str, Any] = {}
    for key in (
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "presence_penalty",
        "max_tokens",
    ):
        if key in params:
            out[key] = params[key]
    return out


def tool_allowlist(persona: Optional[dict]) -> Optional[list[str]]:
    """
    Return the persona's explicit tool allowlist, or None if it has
    no ``params.tool_allowlist`` field.

    None means "fall back to all tools" — back-compat for personas
    written before the per-persona-tool-subset change (2026-04-28).
    Callers that filter the MCP schema must accept None and emit the
    full tool list in that case.

    Side effect: ``tool_search`` (+ plan-mode tools) are auto-injected into
    every explicit allowlist (deduped). They are infrastructure tools every
    persona needs (tool_search, P1 #7, is how the model discovers deferred
    tools), so they need not be spelled out in each persona JSON. Opt-out via
    a ``"-tool_name"`` entry is NOT supported yet — keep the auto-inject
    simple.

    The list is normalised to a list of strings (drops any non-string
    entries silently).
    """
    if not isinstance(persona, dict):
        return None
    params = persona.get("params") or {}
    raw = params.get("tool_allowlist")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    seen: set = set()
    for entry in raw:
        if isinstance(entry, str) and entry and entry not in seen:
            out.append(entry)
            seen.add(entry)
    # Infrastructure tools auto-injected into every persona's
    # allowlist. These are control-flow tools every persona needs
    # regardless of its content-tool set:
    #   - tool_search:         discover deferred (non-core) tools (P1 #7)
    #   - set_plan,
    #     update_plan_item:    structural plan mode (P2 #24 Phase 1).
    #                          Without auto-inject, every persona's
    #                          JSON would have to list them or the
    #                          persona-allowlist reject path in
    #                          _run_tool_calls would short-circuit
    #                          every set_plan call with a synthetic
    #                          "not available" error before the
    #                          dispatcher ran. Discovered via the
    #                          2026-05-29 smoke test on hugin.
    for infra_tool in (
        "tool_search",
        "set_plan",
        "update_plan_item",
    ):
        if infra_tool not in seen:
            out.append(infra_tool)
            seen.add(infra_tool)
    return out


def resident_tools(persona: Optional[dict]) -> list[str]:
    """A4b SOFT bias: the profile's high-value deferred tools to surface in
    the resident schema beyond CORE (from ``params.resident_tools``). Returns
    a deduped list of strings, or [] if absent. Additive only — these tools
    are SHOWN by default so the model skips the tool_search hop; every other
    tool is still reachable via tool_search and nothing is rejected."""
    if not isinstance(persona, dict):
        return []
    raw = (persona.get("params") or {}).get("resident_tools")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set = set()
    for entry in raw:
        if isinstance(entry, str) and entry and entry not in seen:
            out.append(entry)
            seen.add(entry)
    return out


# Tool-use loop bounds (P1 #16). The default applies to any persona
# without an explicit ``params.max_turns``; the clamp guards a JSON
# typo from creating a runaway loop or a zero-turn deadlock.
_DEFAULT_MAX_TURNS = 10
_MIN_MAX_TURNS = 1
_MAX_MAX_TURNS = 30


def max_turns(persona: Optional[dict]) -> int:
    """Return the persona's tool-use turn budget.

    Reads ``params.max_turns``, defaulting to 10. A research persona
    doing deep multi-call exploration may want 15-20; a chat persona
    rarely needs more than a handful. The value is clamped to
    [1, 30] so a malformed persona JSON cannot uncap the loop.
    """
    if not isinstance(persona, dict):
        return _DEFAULT_MAX_TURNS
    raw = (persona.get("params") or {}).get("max_turns")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return _DEFAULT_MAX_TURNS
    return max(_MIN_MAX_TURNS, min(_MAX_MAX_TURNS, raw))


def plan_approval_tools(persona: Optional[dict]) -> frozenset[str]:
    """Return the set of MCP tool names that require an approved
    plan for this persona (P2 #24 Phase 2). Empty frozenset when
    ``params.plan_approval`` is absent or empty — meaning no gating.

    Entries are cross-checked against ``MCP_TOOLS`` at load time
    (``load_personas``), so any value returned here is guaranteed
    to name a real tool."""
    if not isinstance(persona, dict):
        return frozenset()
    raw = (persona.get("params") or {}).get("plan_approval")
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(t for t in raw if isinstance(t, str) and t)
