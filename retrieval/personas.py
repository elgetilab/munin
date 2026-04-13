"""
Persona loading + serving.

Personas are defined as JSON files in PERSONAS_DIR (one per persona id). The
frontend expects a simplified public shape (id, name, description, icon_url,
tags, capabilities, prompt_suggestions). Internally we also keep the raw
vLLM sampling params and system prompt.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

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
        print(f"[WARNING] Personas dir not found: {PERSONAS_DIR}")
        return _personas

    for entry in sorted(os.listdir(PERSONAS_DIR)):
        if not entry.endswith(".json"):
            continue
        path = os.path.join(PERSONAS_DIR, entry)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[WARNING] Failed to load persona {entry}: {e}")
            continue
        persona_id = data.get("id") or entry.removesuffix(".json")
        data["id"] = persona_id
        _personas[persona_id] = data
        print(f"[OK] Loaded persona: {persona_id}")

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
    for key in ("temperature", "top_p", "top_k", "min_p", "presence_penalty"):
        if key in params:
            out[key] = params[key]
    return out
