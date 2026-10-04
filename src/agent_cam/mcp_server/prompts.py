"""MCP Prompts providing recommended agent hardware verification workflows."""

from __future__ import annotations

import mcp.types as types

VERIFY_HARDWARE_PROMPT = types.Prompt(
    name="verify_hardware",
    description="Recommended systematic workflow for verifying physical hardware state with Agent Cam.",
    arguments=[],
)

VERIFY_HARDWARE_MESSAGE = types.PromptMessage(
    role="user",
    content=types.TextContent(
        type="text",
        text="""You have access to Agent Cam MCP tools to see and verify physical hardware.
Follow this systematic workflow:
1. Call `get_status` to verify camera and daemon state.
2. Call `list_cameras` to confirm camera sources and resolutions.
3. Call `capture_image` to inspect the physical hardware setup.
4. Call `measure` on the frame or relevant regions (e.g. status LEDs, OLED display, workpiece) to verify quantitative numbers (brightness, contrast, lit pixels, motion).
5. If waiting for an event (LED blink, movement, boot completion), use `watch` with the expected condition.
6. If checking against known-good state, use `compare_to_baseline`.
7. Quote exact observed values and evidence in your response. Never claim physical success without photographic or numerical evidence.""",
    ),
)
