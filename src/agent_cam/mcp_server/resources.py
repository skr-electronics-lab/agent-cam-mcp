"""MCP Resources exposing system status and named regions."""

from __future__ import annotations

import json
from typing import Any, Dict

import mcp.types as types

STATUS_RESOURCE = types.Resource(
    uri="agentcam://status",
    name="Agent Cam System Status",
    description="Current camera states, daemon status, and buffer metrics",
    mimeType="application/json",
)

REGIONS_RESOURCE = types.Resource(
    uri="agentcam://regions",
    name="Agent Cam Named Regions",
    description="Defined hardware regions of interest (LEDs, displays, zones)",
    mimeType="application/json",
)


def format_status_resource(data: Dict[str, Any]) -> types.TextResourceContents:
    return types.TextResourceContents(
        uri="agentcam://status",
        mimeType="application/json",
        text=json.dumps(data, indent=2),
    )


def format_regions_resource(regions: Dict[str, Any]) -> types.TextResourceContents:
    return types.TextResourceContents(
        uri="agentcam://regions",
        mimeType="application/json",
        text=json.dumps(regions, indent=2),
    )
