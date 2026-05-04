"""
MCP Protocol Endpoints.

Provides FastAPI router with:
- /mcp/sse: SSE endpoint for Open WebUI
- /mcp/messages: JSON-RPC endpoint
- /mcp/call: REST endpoint for Deep Research
- /mcp/tools: List available tools
"""

import json
import uuid
import asyncio
from fastapi import APIRouter, Request, HTTPException

from models import MCPCallRequest
from .schemas import MCP_TOOLS
from .executor import execute_mcp_tool
from .context import (
    current_user_email,
    current_conversation_id,
    current_project_id,
)

router = APIRouter(prefix="/mcp", tags=["MCP"])


@router.get("/sse")
async def mcp_sse_endpoint(request: Request):
    """
    MCP SSE endpoint for Open WebUI native MCP support.

    This endpoint establishes an SSE connection for the MCP protocol.
    It sends tool definitions on connect and handles tool calls via SSE events.
    """
    from sse_starlette.sse import EventSourceResponse

    # Build absolute URL for the messages endpoint
    base_url = str(request.base_url).rstrip('/')
    session_id = str(uuid.uuid4())
    messages_url = f"{base_url}/mcp/messages?session_id={session_id}"

    async def event_generator():
        # Send initial connection event with absolute URL
        yield {
            "event": "endpoint",
            "data": messages_url
        }

        # Keep connection alive with periodic pings
        while True:
            if await request.is_disconnected():
                break
            yield {"event": "ping", "data": ""}
            await asyncio.sleep(30)

    return EventSourceResponse(event_generator())


@router.post("/sse")
async def mcp_sse_post_endpoint(request: Request):
    """
    Handle POST to /mcp/sse for Streamable HTTP transport.

    Some MCP clients (like Open WebUI's Streamable HTTP) POST directly
    to the SSE URL instead of using the SSE protocol.
    """
    return await mcp_messages_endpoint(request)


@router.post("/messages")
async def mcp_messages_endpoint(request: Request):
    """
    Handle MCP JSON-RPC messages (initialize, tools/list, tools/call).

    This endpoint handles the JSON-RPC protocol for MCP:
    - initialize: Return server capabilities
    - tools/list: Return available tools
    - tools/call: Execute a tool and return result
    """
    try:
        body = await request.json()
    except Exception:
        return {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None}

    jsonrpc = body.get("jsonrpc", "2.0")
    method = body.get("method", "")
    params = body.get("params", {})
    request_id = body.get("id")

    # Handle initialize
    if method == "initialize":
        return {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "munin-tools",
                    "version": "1.0.0"
                }
            }
        }

    # Handle tools/list
    elif method == "tools/list":
        tools_list = list(MCP_TOOLS.values())
        return {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "tools": tools_list
            }
        }

    # Handle tools/call
    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if tool_name not in MCP_TOOLS:
            return {
                "jsonrpc": jsonrpc,
                "id": request_id,
                "error": {
                    "code": -32602,
                    "message": f"Unknown tool: {tool_name}"
                }
            }

        # Execute the tool
        result = await execute_mcp_tool(tool_name, arguments)

        # Check for error in result
        if isinstance(result, dict) and "error" in result:
            return {
                "jsonrpc": jsonrpc,
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": f"Error: {result['error']}"
                        }
                    ],
                    "isError": True
                }
            }

        return {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, indent=2)
                    }
                ],
                "isError": False
            }
        }

    # Handle notifications/initialized (no response needed)
    elif method == "notifications/initialized":
        return {"jsonrpc": jsonrpc, "id": request_id, "result": {}}

    # Unknown method
    else:
        return {
            "jsonrpc": jsonrpc,
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
        }


@router.post("/call")
async def call_mcp_tool_rest(request: MCPCallRequest, http_request: Request):
    """
    REST endpoint for MCP tool execution (used by Deep Research and the
    stress test harness).

    Sets ``current_user_email`` from the ``X-Munin-Email`` header
    before dispatching, so user-scoped tools like ``search_user_docs``
    and ``search_past_conversations`` work the same way they do from
    inside chat completions. Optional; callers without a header (e.g.
    the deep research daemon) get a None user context, just like
    before.
    """
    if request.name not in MCP_TOOLS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown tool: {request.name}. Available: {list(MCP_TOOLS.keys())}"
        )

    user_email = http_request.headers.get("X-Munin-Email")
    conv_id = http_request.headers.get("X-Munin-Conversation-Id")
    if user_email:
        current_user_email.set(user_email)
    if conv_id:
        current_conversation_id.set(conv_id)
    # Auto-resolve the project for this conversation (§21) so
    # user-scoped tools like search_user_docs see the right project
    # context when invoked from outside chat_service. Lazy import to
    # avoid circular-init concerns; the lookup is a single-row SELECT.
    if user_email and conv_id:
        try:
            import project_store
            proj = await project_store.get_project_for_conversation(
                conv_id, user_email
            )
        except Exception:
            proj = None
        if proj is not None:
            current_project_id.set(proj.get("id"))

    result = await execute_mcp_tool(request.name, request.arguments)
    return result


@router.get("/tools")
async def list_mcp_tools():
    """
    List all available MCP tools with their schemas.

    This is a convenience endpoint for discovering available tools.
    """
    return {
        "tools": list(MCP_TOOLS.values()),
        "count": len(MCP_TOOLS)
    }
