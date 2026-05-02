"""
Agent registry — loads agent definitions from YAML and validates them.

Each agent entry has a system prompt, a tool allowlist that is checked
against the MCP tool registry on load, and numeric guardrails (iteration
cap, total tool call cap, wall-clock timeout). Agents that reference
unknown tools are loaded with the bad tools stripped and a warning.
"""

from __future__ import annotations

import os
from typing import Optional

import yaml

from mcp.schemas import MCP_TOOLS

AGENTS_CONFIG = os.getenv("AGENTS_CONFIG", "/app/config/agents.yml")

_agents: dict[str, dict] = {}


def _normalize(entry: dict) -> Optional[dict]:
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        print("[WARNING] Agent entry missing 'name' — skipping")
        return None

    description = (entry.get("description") or "").strip()
    system = (entry.get("system") or "").strip()
    if not system:
        print(f"[WARNING] Agent '{name}' has no system prompt — skipping")
        return None

    raw_tools = entry.get("tools") or []
    if not isinstance(raw_tools, list):
        print(f"[WARNING] Agent '{name}' tools must be a list — skipping")
        return None

    allowlist: list[str] = []
    for tool in raw_tools:
        if not isinstance(tool, str):
            continue
        if tool not in MCP_TOOLS:
            print(f"[WARNING] Agent '{name}' references unknown tool '{tool}' — ignored")
            continue
        # An agent calling itself would recurse infinitely; explicitly reject.
        if tool == "invoke_agent":
            print(f"[WARNING] Agent '{name}' cannot call invoke_agent — ignored")
            continue
        allowlist.append(tool)

    return {
        "name": name,
        "description": description,
        "system": system,
        "tools": allowlist,
        "max_iterations": int(entry.get("max_iterations", 8)),
        "max_tool_calls": int(entry.get("max_tool_calls", 20)),
        "timeout_seconds": int(entry.get("timeout_seconds", 300)),
    }


def load_agents() -> dict[str, dict]:
    """(Re)load agent definitions from AGENTS_CONFIG."""
    global _agents
    _agents = {}

    if not os.path.isfile(AGENTS_CONFIG):
        print(f"[WARNING] Agents config not found: {AGENTS_CONFIG}")
        return _agents

    try:
        with open(AGENTS_CONFIG, "r") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[ERROR] Failed to parse {AGENTS_CONFIG}: {e}")
        return _agents

    entries = data.get("agents") or []
    if not isinstance(entries, list):
        print(f"[ERROR] {AGENTS_CONFIG} 'agents' must be a list")
        return _agents

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = _normalize(entry)
        if normalized is None:
            continue
        _agents[normalized["name"]] = normalized
        print(f"[OK] Loaded agent: {normalized['name']} ({len(normalized['tools'])} tools)")

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
