"""
MCP tool: invoke_agent.

Delegates to an agent registered in `agents.yml`. Progress events (agent_start,
agent_thinking, agent_tool_call, agent_tool_result, agent_done) are streamed
through the SSE emitter ContextVar that chat_service installs before tool
dispatch — so the user sees the nested workflow in real time.
"""


async def invoke_agent(agent: str, query: str) -> dict:
    # Lazy imports to avoid a circular dependency during module init.
    import agents as agents_pkg

    agent_config = agents_pkg.get_agent(agent)
    if agent_config is None:
        return {
            "error": (
                f"Unknown agent '{agent}'. Available agents: "
                f"{', '.join(a['name'] for a in agents_pkg.list_agents()) or 'none'}"
            )
        }
    if not query or not query.strip():
        return {"error": "invoke_agent requires a non-empty 'query'"}

    return await agents_pkg.execute_agent(agent_config, query.strip())
