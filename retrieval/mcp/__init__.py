"""
MCP (Model Context Protocol) module for the Munin Retrieval Service.

This module provides:
- MCP tool definitions (schemas.py)
- Tool implementations (tools/)
- Tool executor (executor.py)
- FastAPI endpoints (endpoints.py)
"""

from .schemas import MCP_TOOLS
from .executor import execute_mcp_tool

__all__ = ["MCP_TOOLS", "execute_mcp_tool"]
