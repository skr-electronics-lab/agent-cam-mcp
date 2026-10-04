"""Stdio MCP shim proxying IDE stdio to the local background daemon."""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from agent_cam.mcp_server.prompts import VERIFY_HARDWARE_MESSAGE, VERIFY_HARDWARE_PROMPT
from agent_cam.mcp_server.resources import REGIONS_RESOURCE, STATUS_RESOURCE
from agent_cam.mcp_server.server import TOOL_DEFINITIONS

# Ensure NO internal logs go to stdout
logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="[agent-cam-shim] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent_cam.shim")


def is_daemon_healthy(host: str = "127.0.0.1", port: int = 8765, timeout: float = 1.0) -> bool:
    """Check if the daemon is responding on the health endpoint."""
    url = f"http://{host}:{port}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("status") == "ok"
    except Exception:
        return False
    return False


def start_detached_daemon(host: str = "127.0.0.1", port: int = 8765) -> bool:
    """Start the daemon as a detached background process."""
    cmd = [
        sys.executable,
        "-m",
        "agent_cam.cli",
        "serve",
        "--host",
        host,
        "--port",
        str(port),
    ]

    logger.info("Spawning background daemon: %s", " ".join(cmd))

    creation_flags = 0
    if platform.system() == "Windows":
        # Detached process on Windows
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    try:
        subprocess.Popen(
            cmd,
            creationflags=creation_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True if platform.system() != "Windows" else False,
        )
    except Exception as e:
        logger.error("Failed to spawn detached daemon: %s", e)
        return False

    # Wait up to 15s for daemon to become healthy
    deadline = time.time() + 15.0
    while time.time() < deadline:
        time.sleep(0.3)
        if is_daemon_healthy(host, port, timeout=0.5):
            logger.info("Daemon started and healthy.")
            return True

    logger.error("Daemon did not become healthy within 15 seconds.")
    return False


def ensure_daemon_running(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Ensure daemon is running; start it if not."""
    if is_daemon_healthy(host, port):
        return

    logger.info("Daemon not running on %s:%d. Starting background daemon...", host, port)
    success = start_detached_daemon(host, port)
    if not success:
        raise RuntimeError(
            f"Failed to start Agent Cam daemon on {host}:{port}. Run 'agent-cam-mcp doctor' to diagnose."
        )


class StdioDaemonBridge:
    """Proxies MCP calls over HTTP to the daemon with automatic restart on failure."""

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self._client = httpx.AsyncClient(timeout=125.0)

    async def call_tool_remote(
        self, name: str, args: Dict[str, Any], retry: bool = True
    ) -> types.CallToolResult:
        url = f"{self.base_url}/api/mcp/call_tool"
        payload = {"name": name, "arguments": args, "client_id": "stdio-client"}
        try:
            resp = await self._client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                contents: List[types.Content] = []
                for c in data.get("content", []):
                    if c["type"] == "text":
                        contents.append(types.TextContent(type="text", text=c["text"]))
                    elif c["type"] == "image":
                        contents.append(
                            types.ImageContent(type="image", data=c["data"], mimeType=c["mimeType"])
                        )
                return types.CallToolResult(
                    content=contents, isError=bool(data.get("isError", False))
                )
            else:
                return types.CallToolResult(
                    content=[
                        types.TextContent(
                            type="text", text=f"Daemon returned HTTP {resp.status_code}"
                        )
                    ],
                    isError=True,
                )
        except (httpx.ConnectError, httpx.RemoteProtocolError) as e:
            if retry:
                logger.warning("Daemon connection lost. Restarting daemon and retrying call...")
                await asyncio.to_thread(ensure_daemon_running, self.host, self.port)
                return await self.call_tool_remote(name, args, retry=False)
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=f"Failed connecting to daemon: {e}")],
                isError=True,
            )

    async def get_resource_text(self, uri: str) -> str:
        if uri == "agentcam://status":
            url = f"{self.base_url}/api/status"
        elif uri == "agentcam://regions":
            url = f"{self.base_url}/api/regions"
        else:
            raise ValueError(f"Unknown resource URI: {uri}")

        resp = await self._client.get(url)
        return json.dumps(resp.json(), indent=2)


async def run_stdio_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Run the stdio MCP server that IDEs launch."""
    ensure_daemon_running(host, port)
    bridge = StdioDaemonBridge(host=host, port=port)

    app = Server("agent-cam")

    @app.list_tools()
    async def list_tools() -> List[types.Tool]:
        return TOOL_DEFINITIONS

    @app.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        return await bridge.call_tool_remote(name, arguments or {})

    @app.list_resources()
    async def list_resources() -> List[types.Resource]:
        return [STATUS_RESOURCE, REGIONS_RESOURCE]

    @app.read_resource()
    async def read_resource(uri: Any) -> types.TextResourceContents:
        uri_str = str(uri)
        text = await bridge.get_resource_text(uri_str)
        return types.TextResourceContents(uri=uri_str, mimeType="application/json", text=text)

    @app.list_prompts()
    async def list_prompts() -> List[types.Prompt]:
        return [VERIFY_HARDWARE_PROMPT]

    @app.get_prompt()
    async def get_prompt(
        name: str, arguments: Optional[Dict[str, Any]] = None
    ) -> types.GetPromptResult:
        if name == "verify_hardware":
            return types.GetPromptResult(
                description=VERIFY_HARDWARE_PROMPT.description,
                messages=[VERIFY_HARDWARE_MESSAGE],
            )
        raise ValueError(f"Unknown prompt: {name}")

    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
