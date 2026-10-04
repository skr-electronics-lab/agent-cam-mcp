"""Safe client installer for Antigravity, Claude Code, Cursor, and Codex."""

from __future__ import annotations

import json
import logging
import platform
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def get_absolute_launcher() -> Tuple[str, List[str]]:
    """Determine the most reliable absolute command and args for IDE execution."""
    # Find agent-cam-mcp executable
    agent_cam_exe = shutil.which("agent-cam-mcp")
    if agent_cam_exe and Path(agent_cam_exe).is_absolute():
        return str(Path(agent_cam_exe).resolve()), []

    # Check python script in current venv/interpreter
    py_dir = Path(sys.executable).parent
    script_candidate = py_dir / (
        "agent-cam-mcp.exe" if platform.system() == "Windows" else "agent-cam-mcp"
    )
    if script_candidate.exists():
        return str(script_candidate.resolve()), []

    # Fallback to python -m agent_cam.cli
    return str(Path(sys.executable).resolve()), ["-m", "agent_cam.cli"]


def find_antigravity_config_paths() -> List[Path]:
    """Find Antigravity MCP config paths on the current system."""
    paths = []
    user_home = Path.home()

    candidates = [
        user_home / ".gemini" / "antigravity-ide" / "mcp_config.json",
        user_home / ".gemini" / "antigravity" / "mcp_config.json",
        user_home / ".gemini" / "config" / "mcp_config.json",
    ]
    for c in candidates:
        if c.exists():
            paths.append(c)

    return paths


def find_claude_code_config_path() -> Optional[Path]:
    p = Path.home() / ".claude.json"
    return p if p.exists() else None


def find_cursor_config_paths() -> List[Path]:
    paths = []
    home = Path.home()
    candidates = [
        home / ".cursor" / "mcp.json",
        home / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage" / "mcp.json",
    ]
    for c in candidates:
        if c.exists():
            paths.append(c)
    return paths


def find_codex_config_path() -> Optional[Path]:
    p = Path.home() / ".codex" / "config.toml"
    return p if p.exists() else None


def update_json_mcp_config(
    config_path: Path,
    server_name: str,
    server_entry: Dict[str, Any],
    dry_run: bool = False,
    uninstall: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Safely merge or remove server entry in JSON config with timestamped backup."""
    if not config_path.exists():
        return False, f"Config file {config_path} does not exist", None

    try:
        content = config_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except Exception as e:
        return False, f"Failed parsing JSON from {config_path}: {e}", None

    servers = data.setdefault("mcpServers", {})

    if uninstall:
        if server_name not in servers:
            return True, f"'{server_name}' was not registered in {config_path}", None
        diff_desc = f"Removed '{server_name}' from {config_path}"
        del servers[server_name]
    else:
        diff_desc = f"Added/Updated '{server_name}' in {config_path}"
        servers[server_name] = server_entry

    new_content = json.dumps(data, indent=2) + "\n"

    if dry_run:
        return True, f"[DRY RUN] {diff_desc}", new_content

    # Create timestamped backup
    backup_path = config_path.with_suffix(f".json.bak.{int(time.time())}")
    shutil.copy2(config_path, backup_path)

    # Atomic write
    tmp_path = config_path.with_suffix(".json.tmp")
    tmp_path.write_text(new_content, encoding="utf-8")
    tmp_path.replace(config_path)

    # Verify reload
    try:
        verify_data = json.loads(config_path.read_text(encoding="utf-8"))
        if not uninstall and server_name not in verify_data.get("mcpServers", {}):
            raise ValueError("Verification failed: key not found after write")
    except Exception as e:
        shutil.copy2(backup_path, config_path)
        return False, f"Failed verification after write, restored backup: {e}", None

    return True, f"{diff_desc} (Backup: {backup_path.name})", new_content


def install_client(
    client_name: str,
    dry_run: bool = False,
    uninstall: bool = False,
) -> Dict[str, Any]:
    """Execute client registration."""
    cmd_exe, cmd_args = get_absolute_launcher()

    server_entry = {
        "$typeName": "exa.cascade_plugins_pb.CascadePluginCommandTemplate",
        "command": cmd_exe,
        "args": cmd_args,
        "env": {},
        "disabled": False,
    }

    results = []

    if client_name in ("antigravity", "all"):
        ag_paths = find_antigravity_config_paths()
        if not ag_paths:
            results.append(
                {
                    "client": "antigravity",
                    "status": "not_found",
                    "message": "Antigravity mcp_config.json not found in ~/.gemini/. Use 'print' mode.",
                }
            )
        else:
            for p in ag_paths:
                ok, msg, diff = update_json_mcp_config(
                    p, "agent-cam", server_entry, dry_run=dry_run, uninstall=uninstall
                )
                results.append(
                    {
                        "client": "antigravity",
                        "path": str(p),
                        "status": "success" if ok else "failed",
                        "message": msg,
                        "diff": diff,
                    }
                )

    if client_name in ("cursor", "all"):
        cursor_paths = find_cursor_config_paths()
        if not cursor_paths:
            results.append(
                {
                    "client": "cursor",
                    "status": "not_found",
                    "message": "Cursor mcp.json not found.",
                }
            )
        else:
            std_entry = {"command": cmd_exe, "args": cmd_args}
            for p in cursor_paths:
                ok, msg, diff = update_json_mcp_config(
                    p, "agent-cam", std_entry, dry_run=dry_run, uninstall=uninstall
                )
                results.append(
                    {
                        "client": "cursor",
                        "path": str(p),
                        "status": "success" if ok else "failed",
                        "message": msg,
                        "diff": diff,
                    }
                )

    if client_name in ("claude-code", "all"):
        results.append(
            {
                "client": "claude-code",
                "status": "command_info",
                "message": f'Run command: claude mcp add agent-cam -- "{cmd_exe}" {" ".join(cmd_args)}'.strip(),
            }
        )

    if client_name in ("codex", "all"):
        results.append(
            {
                "client": "codex",
                "status": "config_info",
                "message": f'Add to ~/.codex/config.toml:\n[mcp_servers.agent-cam]\ncommand = "{cmd_exe}"\nargs = {json.dumps(cmd_args)}',
            }
        )

    if client_name == "print":
        generic_snippet = {
            "mcpServers": {
                "agent-cam": {
                    "command": cmd_exe,
                    "args": cmd_args,
                }
            }
        }
        results.append(
            {
                "client": "print",
                "status": "print",
                "message": json.dumps(generic_snippet, indent=2),
            }
        )

    return {"client": client_name, "dry_run": dry_run, "results": results}
