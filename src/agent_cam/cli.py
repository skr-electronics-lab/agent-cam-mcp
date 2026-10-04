"""Command line interface for Agent Cam MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
import webbrowser
from typing import Any, Dict

import httpx
import uvicorn

from agent_cam import __version__
from agent_cam.config import AgentCamConfig, get_lock_file
from agent_cam.daemon import DaemonServer
from agent_cam.doctor import run_all_checks
from agent_cam.installer import install_client
from agent_cam.shim import ensure_daemon_running, run_stdio_server

logger = logging.getLogger("agent_cam.cli")


def execute_doctor_checks(json_mode: bool = False) -> Dict[str, Any]:
    checks, passed = run_all_checks()
    data = {
        "passed": passed,
        "version": __version__,
        "checks": [c.to_dict() for c in checks],
    }
    return data


def print_doctor_report(report: Dict[str, Any]) -> None:
    print(f"Agent Cam MCP Tool - Self Diagnosis (v{report['version']})")
    print("-" * 65)
    for c in report["checks"]:
        status = c["status"]
        name = c["name"]
        msg = c["message"]
        print(f"[{status:4s}] {name:32s} : {msg}")
        if "fix" in c:
            print(f"       -> Fix: {c['fix']}")
    print("-" * 65)
    if report["passed"]:
        print("RESULT: ALL REQUIRED CHECKS PASSED")
    else:
        print("RESULT: ONE OR MORE CHECKS FAILED")


def cmd_serve(args: argparse.Namespace) -> None:
    """Run daemon in foreground."""
    cfg = AgentCamConfig.load()
    if args.port:
        cfg.port = args.port
    if args.host:
        cfg.host = args.host
    if getattr(args, "fake_camera", False):
        cfg.use_fake_camera = True

    if cfg.host not in ("127.0.0.1", "localhost"):
        print(f"WARNING: Binding to non-localhost address ({cfg.host}). Ensure network is secure.")

    daemon = DaemonServer(config=cfg, host=cfg.host, port=cfg.port)
    uvicorn.run(
        daemon.app,
        host=cfg.host,
        port=cfg.port,
        log_level="info",
        access_log=False,
    )


def cmd_ui(args: argparse.Namespace) -> None:
    """Ensure daemon running and open dashboard in default browser."""
    cfg = AgentCamConfig.load()
    port = args.port or cfg.port
    host = args.host or cfg.host

    print(f"Connecting to Agent Cam daemon on {host}:{port}...")
    try:
        ensure_daemon_running(host, port)
    except Exception as e:
        print(f"Error starting daemon: {e}")
        sys.exit(1)

    url = f"http://{host}:{port}/"
    print(f"Opening dashboard: {url}")
    webbrowser.open(url)


def cmd_status(args: argparse.Namespace) -> None:
    """Print status of the local daemon."""
    cfg = AgentCamConfig.load()
    port = args.port or cfg.port
    host = args.host or cfg.host

    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"http://{host}:{port}/health")
            if resp.status_code == 200:
                health = resp.json()
                status_resp = client.get(f"http://{host}:{port}/api/status")
                status_data = status_resp.json() if status_resp.status_code == 200 else {}
                print(f"Daemon Status: RUNNING (v{health.get('version', 'unknown')})")
                print(f"Uptime:        {health.get('uptime', 0)} seconds")
                print(f"Access Paused: {health.get('paused', False)}")
                print(f"Active Agents: {status_data.get('active_agent_connections', 0)}")
                print(f"Cameras:       {len(status_data.get('cameras', []))}")
                for c in status_data.get("cameras", []):
                    print(
                        f"  - [{c['id']}] {c['name']} ({c['state']}) {c['width']}x{c['height']} @ {c['fps']}fps"
                    )
                return
    except Exception:
        pass

    print(f"Daemon Status: NOT RUNNING on {host}:{port}")
    lock = get_lock_file()
    if lock.exists():
        print(f"(Stale lock file found at {lock})")


def cmd_stop(args: argparse.Namespace) -> None:
    """Stop running daemon cleanly."""
    lock = get_lock_file()
    if not lock.exists():
        print("No active daemon lock file found.")
        return

    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(data.get("pid", 0))
        if pid > 0:
            print(f"Stopping daemon process PID {pid}...")
            os.kill(pid, signal.SIGTERM)
            for _ in range(20):
                time.sleep(0.1)
                if not lock.exists():
                    break
            if lock.exists():
                try:
                    lock.unlink()
                except Exception:
                    pass
            print("Daemon stopped cleanly.")
            return
    except Exception as e:
        print(f"Error stopping daemon: {e}")
        if lock.exists():
            lock.unlink()


def cmd_doctor(args: argparse.Namespace) -> None:
    """Run full self-diagnosis."""
    report = execute_doctor_checks(json_mode=args.json)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_doctor_report(report)

    sys.exit(0 if report["passed"] else 1)


def cmd_install(args: argparse.Namespace) -> None:
    """Register server in client config."""
    client = args.client
    res = install_client(client_name=client, dry_run=args.dry_run, uninstall=args.uninstall)
    for r in res["results"]:
        client_name = r.get("client")
        status = r.get("status")
        msg = r.get("message")
        print(f"[{status}] {client_name}: {msg}")
        if "diff" in r and r["diff"] and not args.uninstall:
            print("--- Generated / Updated Config ---")
            print(r["diff"])


def cmd_version(args: argparse.Namespace) -> None:
    print(f"agent-cam-mcp {__version__}")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agent-cam-mcp",
        description="Agent Cam MCP Tool - Physical perception and verification for AI agents",
    )
    parser.add_argument("--version", action="store_true", help="Print version")
    parser.add_argument("--fake-camera", action="store_true", help=argparse.SUPPRESS)

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # serve
    sp_serve = subparsers.add_parser("serve", help="Run the daemon in the foreground")
    sp_serve.add_argument("--host", default="127.0.0.1", help="Host to bind to (default 127.0.0.1)")
    sp_serve.add_argument("--port", type=int, default=8765, help="Port to bind to (default 8765)")
    sp_serve.add_argument("--fake-camera", action="store_true", help="Use synthetic fake camera")

    # ui
    sp_ui = subparsers.add_parser("ui", help="Open the dashboard in default browser")
    sp_ui.add_argument("--host", default="127.0.0.1")
    sp_ui.add_argument("--port", type=int, default=8765)

    # status
    sp_status = subparsers.add_parser(
        "status", help="Print daemon status, cameras, and connected clients"
    )
    sp_status.add_argument("--host", default="127.0.0.1")
    sp_status.add_argument("--port", type=int, default=8765)

    # stop
    subparsers.add_parser("stop", help="Stop running daemon cleanly")

    # doctor
    sp_doctor = subparsers.add_parser("doctor", help="Run full self-diagnosis checks")
    sp_doctor.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # install
    sp_install = subparsers.add_parser("install", help="Register server in client config")
    sp_install.add_argument(
        "--client",
        choices=["antigravity", "claude-code", "codex", "cursor", "print", "all"],
        required=True,
        help="Target client to configure",
    )
    sp_install.add_argument(
        "--dry-run", action="store_true", help="Preview changes without writing"
    )
    sp_install.add_argument(
        "--uninstall", action="store_true", help="Remove registration from config"
    )

    # version
    subparsers.add_parser("version", help="Print version")

    args = parser.parse_args()

    if args.version:
        print(f"agent-cam-mcp {__version__}")
        sys.exit(0)

    if not args.command:
        # Default behavior: run the stdio shim for IDE agent connection
        cfg = AgentCamConfig.load()
        if args.fake_camera:
            cfg.use_fake_camera = True
            cfg.save()
        asyncio.run(run_stdio_server(host=cfg.host, port=cfg.port))
        return

    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "ui":
        cmd_ui(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "install":
        cmd_install(args)
    elif args.command == "version":
        cmd_version(args)


if __name__ == "__main__":
    main()
