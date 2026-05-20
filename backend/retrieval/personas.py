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
from typing import Any, Optional

logger = logging.getLogger(__name__)

PERSONAS_DIR = os.getenv("PERSONAS_DIR", "/app/personas")
DEFAULT_PERSONA_ID = os.getenv("DEFAULT_PERSONA", "chat")

_personas: dict[str, dict] = {}


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
        persona_id = data.get("id") or entry.removesuffix(".json")
        data["id"] = persona_id
        _personas[persona_id] = data
        logger.info("Loaded persona: %s", persona_id)

    return _personas


def get_persona(persona_id: str) -> Optional[dict]:
    return _personas.get(persona_id)


def public_personas() -> dict:
    """Return the /api/personas payload."""
    default_id = DEFAULT_PERSONA_ID if DEFAULT_PERSONA_ID in _personas else (
        next(iter(_personas), "chat")
    )
    return {
        "personas": [_public_view(p) for p in _personas.values()],
        "default_persona": default_id,
    }


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

    Side effect: ``delegate_to_persona`` and ``tool_search`` are
    auto-injected into every explicit allowlist (deduped). Both are
    infrastructure tools every persona needs — delegation hands a turn
    to another persona, and tool_search (P1 #7) is how the model
    discovers the deferred tools that aren't in its core schema. Neither
    has to be spelled out in each persona JSON. Opt-out via a
    ``"-tool_name"`` entry is NOT supported yet — keep the auto-inject
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
    for infra_tool in ("delegate_to_persona", "tool_search"):
        if infra_tool not in seen:
            out.append(infra_tool)
            seen.add(infra_tool)
    return out
