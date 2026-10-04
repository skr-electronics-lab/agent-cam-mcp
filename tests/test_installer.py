"""Unit tests for client installer and safe JSON merging."""

from __future__ import annotations

import json
from pathlib import Path

from agent_cam.installer import update_json_mcp_config


def test_update_json_mcp_config_dry_run(tmp_path: Path):
    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "foo"}}}), encoding="utf-8"
    )

    entry = {"command": "agent-cam-mcp", "args": []}
    ok, msg, diff = update_json_mcp_config(cfg_file, "agent-cam", entry, dry_run=True)

    assert ok is True
    assert "[DRY RUN]" in msg
    assert "agent-cam" in diff
    # File untouched
    content = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "agent-cam" not in content["mcpServers"]


def test_update_json_mcp_config_real_merge_and_uninstall(tmp_path: Path):
    cfg_file = tmp_path / "mcp_config.json"
    cfg_file.write_text(
        json.dumps({"mcpServers": {"existing": {"command": "foo"}}}), encoding="utf-8"
    )

    entry = {"command": "D:/path/to/agent-cam-mcp.exe", "args": []}
    # 1. Install
    ok, msg, _ = update_json_mcp_config(cfg_file, "agent-cam", entry, dry_run=False)
    assert ok is True

    updated = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "agent-cam" in updated["mcpServers"]
    assert "existing" in updated["mcpServers"]

    # Check that backup file exists
    backups = list(tmp_path.glob("mcp_config.json.bak.*"))
    assert len(backups) == 1

    # 2. Uninstall
    ok2, msg2, _ = update_json_mcp_config(cfg_file, "agent-cam", entry, uninstall=True)
    assert ok2 is True
    uninstalled = json.loads(cfg_file.read_text(encoding="utf-8"))
    assert "agent-cam" not in uninstalled["mcpServers"]
    assert "existing" in uninstalled["mcpServers"]
