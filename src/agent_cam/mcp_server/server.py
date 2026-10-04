"""MCP Server initialization, tool registry, resources, and prompts."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import mcp.types as types
from mcp.server.lowlevel import Server

from agent_cam.mcp_server.prompts import VERIFY_HARDWARE_MESSAGE, VERIFY_HARDWARE_PROMPT
from agent_cam.mcp_server.resources import (
    REGIONS_RESOURCE,
    STATUS_RESOURCE,
    format_regions_resource,
    format_status_resource,
)
from agent_cam.mcp_server.tools import MCPToolExecutor

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS = [
    types.Tool(
        name="get_status",
        description=(
            "Use this any time you need to see the physical hardware: while debugging, after reboots, "
            "during serial output, mid-process. Do not wait until the end of a task. Returns daemon version, "
            "uptime, camera states, pause flag, buffer fill, adapters, and system warnings. Always call this first."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="list_cameras",
        description=(
            "List all detected physical USB webcams and IP cameras with their ID, name, resolution, fps, and state."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "boolean",
                    "description": "If true, rescans USB bus and PNP devices for newly plugged cameras.",
                }
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="capture_image",
        description=(
            "Use this any time you need to visually inspect the hardware. Captures a live frame directly as an MCP "
            "image result. Never saves to disk by default. Can crop to a defined region and annotate existing regions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Camera ID (default: primary camera)"},
                "region": {"type": "string", "description": "Optional named region to crop to"},
                "width": {
                    "type": "integer",
                    "description": "Target image width (default 1024, max 3840)",
                    "default": 1024,
                },
                "annotate": {
                    "type": "boolean",
                    "description": "If true, draws bounding boxes and labels for all defined regions",
                    "default": False,
                },
                "format": {
                    "type": "string",
                    "enum": ["jpeg", "png"],
                    "description": "Image format (default jpeg)",
                    "default": "jpeg",
                },
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="capture_sequence",
        description=(
            "Captures a sequence of frames over time and returns a single token-efficient contact sheet with timestamps. "
            "Use to observe physical motions, LED blink patterns, or mechanical movements."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Camera ID"},
                "seconds": {
                    "type": "integer",
                    "description": "Duration in seconds (1-30)",
                    "default": 3,
                },
                "fps": {"type": "integer", "description": "Frames per second (1-10)", "default": 2},
                "region": {"type": "string", "description": "Optional named region to crop"},
            },
            "required": ["seconds"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="get_timeline",
        description=(
            "Returns a contact sheet of buffered history frames plus timestamp-aligned event lines from serial or log adapters. "
            "Use to correlate hardware events (e.g. crash, reset, button press) with visual frames."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "last_seconds": {
                    "type": "integer",
                    "description": "Duration of history window in seconds (1-60)",
                    "default": 15,
                },
                "camera": {"type": "string", "description": "Camera ID"},
                "include_events": {
                    "type": "boolean",
                    "description": "Whether to overlay adapter events on the timeline banner",
                    "default": True,
                },
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="measure",
        description=(
            "Compute quantitative measurements on the camera frame or region without returning heavy images. "
            "Returns mean brightness, contrast, lit-pixel percentage, dominant colors, edge density, sharpness (Laplacian variance), and motion level."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Camera ID"},
                "region": {"type": "string", "description": "Optional named region to measure"},
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="watch",
        description=(
            "Asynchronously monitor the camera until a physical condition is met or timeout expires. "
            "Conditions: 'change', 'motion_start', 'motion_stop', 'brightness_above', 'brightness_below', 'color_present'. "
            "Returns fired status, timing, and before/after evidence images."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "enum": [
                        "change",
                        "motion_start",
                        "motion_stop",
                        "brightness_above",
                        "brightness_below",
                        "color_present",
                    ],
                    "description": "Condition to wait for",
                },
                "threshold": {
                    "type": "number",
                    "description": "Optional numeric threshold for the condition",
                },
                "timeout_s": {
                    "type": "number",
                    "description": "Maximum wait timeout in seconds (1-120)",
                    "default": 20.0,
                },
                "camera": {"type": "string", "description": "Camera ID"},
                "region": {"type": "string", "description": "Optional named region to watch"},
            },
            "required": ["condition"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="define_region",
        description=(
            "Define and save a named region of interest (e.g. 'status_led', 'oled_screen', 'nozzle_area') "
            "using normalized coordinates (0.0 to 1.0) or pixels."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique region name"},
                "x": {"type": "number", "description": "Top-left X coordinate"},
                "y": {"type": "number", "description": "Top-left Y coordinate"},
                "w": {"type": "number", "description": "Region width"},
                "h": {"type": "number", "description": "Region height"},
                "camera": {"type": "string", "description": "Optional camera ID to bind region to"},
                "units": {
                    "type": "string",
                    "enum": ["normalized", "pixels"],
                    "description": "Coordinate system ('normalized' or 'pixels')",
                    "default": "normalized",
                },
            },
            "required": ["name", "x", "y", "w", "h"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="list_regions",
        description="List all saved named hardware regions of interest.",
        inputSchema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="delete_region",
        description="Delete a saved named region.",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Name of region to delete"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="request_region_from_user",
        description=(
            "Ask the user in the Dashboard UI to interactively draw a region on the camera preview. "
            "Blocks until user finishes drawing or timeout."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the requested region"},
                "message": {
                    "type": "string",
                    "description": "Instructions for user on what to select",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="save_baseline",
        description=(
            "Capture and persist a golden reference image for subsequent comparison. "
            "Use to establish a known-good baseline before running experiments or flashing firmware."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Baseline name"},
                "camera": {"type": "string", "description": "Camera ID"},
                "region": {
                    "type": "string",
                    "description": "Optional named region to save as baseline",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="compare_to_baseline",
        description=(
            "Compare the current physical camera state against a saved baseline. "
            "Returns SSIM structural similarity score, percent of changed pixels, and a visual difference heatmap image."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of baseline to compare against"},
                "camera": {"type": "string", "description": "Camera ID"},
                "region": {"type": "string", "description": "Optional named region"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="read_text",
        description=(
            "Perform OCR to extract printed text or seven-segment display digits from the camera frame or region. "
            "Returns extracted text and confidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Camera ID"},
                "region": {
                    "type": "string",
                    "description": "Optional named region containing text/display",
                },
            },
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="set_camera_settings",
        description=(
            "Adjust hardware camera parameters: exposure, focus, brightness, white balance, or lock auto settings. "
            "Reports exactly which parameters were accepted vs rejected by the driver."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "description": "Camera ID"},
                "exposure": {"type": "integer", "description": "Manual exposure value"},
                "focus": {"type": "integer", "description": "Manual focus value"},
                "brightness": {"type": "integer", "description": "Brightness value"},
                "white_balance": {"type": "integer", "description": "White balance value"},
                "lock_auto": {
                    "type": "boolean",
                    "description": "Lock auto-exposure/focus to current values",
                },
            },
            "required": ["camera"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="read_events",
        description=(
            "Read timestamped lines from an active event adapter (serial, log file, MQTT). "
            "Can block until specific text appears or until timeout."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "Adapter source name (e.g. 'serial:COM1', 'logfile')",
                },
                "last_seconds": {
                    "type": "number",
                    "description": "Time window in seconds",
                    "default": 30.0,
                },
                "lines": {"type": "integer", "description": "Max lines to return", "default": 100},
                "until_text": {"type": "string", "description": "Optional text to wait for"},
                "timeout_s": {
                    "type": "number",
                    "description": "Timeout in seconds to wait for until_text",
                    "default": 5.0,
                },
            },
            "required": ["source"],
            "additionalProperties": False,
        },
    ),
    types.Tool(
        name="run_action",
        description=(
            "Execute a configured allowlist action (e.g. 'reset_board', 'pause_print'). "
            "Subject to approval policy. If observe_seconds is configured, returns post-action timeline sheet."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Allowlisted action name"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional arguments",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    ),
]


def create_mcp_server(executor: MCPToolExecutor) -> Server:
    """Build and configure the low-level MCP server instance."""
    app = Server("agent-cam")

    @app.list_tools()
    async def list_tools() -> List[types.Tool]:
        return TOOL_DEFINITIONS

    @app.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> types.CallToolResult:
        return await executor.execute(name, arguments or {})

    @app.list_resources()
    async def list_resources() -> List[types.Resource]:
        return [STATUS_RESOURCE, REGIONS_RESOURCE]

    @app.read_resource()
    async def read_resource(uri: Any) -> types.TextResourceContents:
        uri_str = str(uri)
        if uri_str == "agentcam://status":
            res, _, _ = await executor.tool_get_status({}, "resource_reader")
            return format_status_resource(json.loads(res.content[0].text))
        elif uri_str == "agentcam://regions":
            res, _, _ = await executor.tool_list_regions({}, "resource_reader")
            return format_regions_resource(json.loads(res.content[0].text))
        raise ValueError(f"Unknown resource: {uri_str}")

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

    return app
