"""MCP Server package for Agent Cam."""

from agent_cam.mcp_server.server import TOOL_DEFINITIONS, create_mcp_server
from agent_cam.mcp_server.tools import MCPToolExecutor

__all__ = ["create_mcp_server", "MCPToolExecutor", "TOOL_DEFINITIONS"]
