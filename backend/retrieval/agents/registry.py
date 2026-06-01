"""
Agent registry — loads agent definitions from YAML and validates them.

Each agent entry has a system prompt, a tool allowlist that is checked
against the MCP tool registry on load, and numeric guardrails (iteration
cap, total tool call cap, wall-clock timeout). Agents that reference
unknown tools are loaded with the bad tools stripped and a warning.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from mcp.schemas import MCP_TOOLS

logger = logging.getLogger(__name__)

AGENTS_CONFIG = os.getenv("AGENTS_CONFIG", "/app/config/agents.yml")

_agents: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Schema (P2 #20). `extra="forbid"` is the whole point — a typo like
# `max_iteratons: 8` raises a ValidationError at startup instead of
# silently inheriting the default. Numeric bounds reject obviously
# broken values; runtime tool-name resolution + invoke_agent stripping
# still happens below because they cross the schema/runtime boundary.
# ---------------------------------------------------------------------------


class AgentEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    name: str = Field(..., min_length=1)
    description: str = ""
    system: str = Field(..., min_length=1)
    tools: list[str] = Field(default_factory=list)
    max_iterations: int = Field(default=8, ge=1, le=30)
    max_tool_calls: int = Field(default=20, ge=1, le=100)
    timeout_seconds: int = Field(default=300, ge=10, le=1800)

    @field_validator("description", "system", mode="before")
    @classmethod
    def _strip(cls, v):
        if v is None:
            return v
        if isinstance(v, str):
            return v.strip()
        return v


def _normalize(entry: dict) -> Optional[dict]:
    try:
        validated = AgentEntry.model_validate(entry).model_dump()
    except ValidationError as e:
        name_hint = entry.get("name") if isinstance(entry, dict) else "?"
        logger.error("Agent %r failed validation, skipping: %s", name_hint, e)
        return None

    # Runtime concerns the schema deliberately doesn't enforce: tool
    # names are not cross-checked against MCP_TOOLS at parse time (the
    # schema layer shouldn't know about the tool registry), and
    # invoke_agent self-recursion is a behaviour rule, not a shape rule.
    name = validated["name"]
    allowlist: list[str] = []
    for tool in validated["tools"]:
        if tool not in MCP_TOOLS:
            logger.warning(
                "Agent %r references unknown tool %r, ignored", name, tool
            )
            continue
        if tool == "invoke_agent":
            logger.warning("Agent %r cannot call invoke_agent, ignored", name)
            continue
        allowlist.append(tool)
    validated["tools"] = allowlist
    return validated


def load_agents() -> dict[str, dict]:
    """(Re)load agent definitions from AGENTS_CONFIG."""
    global _agents
    _agents = {}

    if not os.path.isfile(AGENTS_CONFIG):
        logger.warning("Agents config not found: %s", AGENTS_CONFIG)
        return _agents

    try:
        with open(AGENTS_CONFIG, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.exception("Failed to parse %s", AGENTS_CONFIG)
        return _agents

    entries = data.get("agents") or []
    if not isinstance(entries, list):
        logger.error("%s 'agents' must be a list", AGENTS_CONFIG)
        return _agents

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize(entry)
        if normalized is None:
            continue
        _agents[normalized["name"]] = normalized
        logger.info(
            "Loaded agent: %s (%d tools)",
            normalized["name"], len(normalized["tools"]),
        )

    return _agents


def get_agent(name: str) -> Optional[dict]:
    return _agents.get(name)


def list_agents() -> list[dict]:
    """Return lightweight summaries for every registered agent."""
    return [
        {
            "name": a["name"],
            "description": a["description"],
            "tools": a["tools"],
            "max_iterations": a["max_iterations"],
            "timeout_seconds": a["timeout_seconds"],
        }
        for a in _agents.values()
    ]


def agent_summaries_for_prompt() -> str:
    """
    Render agent descriptions as a bullet list suitable for injecting into
    the main assistant's system prompt. Returns "" if no agents are loaded.
    """
    if not _agents:
        return ""
    lines = ["Available agent workflows (invoke via the `invoke_agent` tool):"]
    for a in _agents.values():
        desc = a["description"] or "(no description)"
        lines.append(f"- {a['name']}: {desc}")
    return "\n".join(lines)
