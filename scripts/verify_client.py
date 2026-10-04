"""Verification script testing the exact installed command outside the IDE."""

from __future__ import annotations

import asyncio
import json
import base64
import sys
from pathlib import Path
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.session import ClientSession
import cv2
import numpy as np


async def verify_mcp_command():
    exe_path = r"D:\Projects\mcp-tool-development\agent-cam-mcp-tool\.venv\Scripts\agent-cam-mcp.exe"
    print(f"Connecting to installed command: {exe_path}")

    server_params = StdioServerParameters(
        command=exe_path,
        args=[],
        env={},
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # 1. Initialize
            init_res = await session.initialize()
            print(f"1. initialize result: serverName={init_res.serverInfo.name} version={init_res.serverInfo.version}")

            # 2. List tools
            tools_res = await session.list_tools()
            tool_names = [t.name for t in tools_res.tools]
            print(f"2. tools/list: found {len(tool_names)} tools -> {', '.join(tool_names)}")

            # 3. Call get_status
            status_res = await session.call_tool("get_status", {})
            print(f"3. get_status call: isError={status_res.isError}")
            status_data = json.loads(status_res.content[0].text)
            print(f"   Uptime: {status_data['uptime_seconds']}s, Cameras: {len(status_data['cameras'])}, Paused: {status_data['paused_by_user']}")

            # 4. Call capture_image
            cap_res = await session.call_tool("capture_image", {"width": 640})
            print(f"4. capture_image call: isError={cap_res.isError}, contents={len(cap_res.content)}")
            has_valid_image = False
            for c in cap_res.content:
                if hasattr(c, "data") and c.type == "image":
                    raw = base64.b64decode(c.data)
                    arr = np.frombuffer(raw, dtype=np.uint8)
                    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if img is not None:
                        has_valid_image = True
                        print(f"   Decoded image shape: {img.shape}, bytes: {len(raw)} bytes")

            assert has_valid_image, "Failed to decode image from capture_image call"
            print("VERIFICATION COMPLETE: EXACT INSTALLED COMMAND PROVEN WORKING!")


if __name__ == "__main__":
    asyncio.run(verify_mcp_command())
