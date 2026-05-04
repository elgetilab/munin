"""Agentic orchestration for the Munin retrieval service."""

from .registry import (
    load_agents,
    get_agent,
    list_agents,
    agent_summaries_for_prompt,
)
from .executor import execute_agent

__all__ = [
    "load_agents",
    "get_agent",
    "list_agents",
    "agent_summaries_for_prompt",
    "execute_agent",
]
