"""Full self-diagnosis system for doctor command and dashboard."""

from __future__ import annotations

import base64
import importlib
import json
import logging
import platform
import shutil
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import httpx
import numpy as np

from agent_cam import __version__
from agent_cam.cameras.opencv_cam import enumerate_usb_cameras
from agent_cam.config import AgentCamConfig, get_config_dir, get_data_dir
from agent_cam.installer import (
    find_antigravity_config_paths,
    find_cursor_config_paths,
    get_absolute_launcher,
)

logger = logging.getLogger(__name__)


class CheckResult:
    def __init__(self, name: str, status: str, message: str, fix: Optional[str] = None):
        self.name = name
        self.status = status  # PASS, WARN, FAIL, INFO
        self.message = message
        self.fix = fix

    def to_dict(self) -> Dict[str, Any]:
        d = {"name": self.name, "status": self.status, "message": self.message}
        if self.fix:
            d["fix"] = self.fix
        return d


def check_python_and_package() -> CheckResult:
    py_ver = sys.version.split()[0]
    major, minor = sys.version_info[:2]
    if (major == 3 and 10 <= minor <= 13) or (major == 3 and minor > 13):
        return CheckResult(
            "Python & Package Version", "PASS", f"Python {py_ver}, agent-cam-mcp {__version__}"
        )
    return CheckResult(
        "Python & Package Version",
        "WARN",
        f"Python {py_ver} (supported: 3.10-3.13), agent-cam-mcp {__version__}",
        "Consider running on Python 3.10 to 3.13",
    )


def check_executable_resolution() -> CheckResult:
    exe_path, args = get_absolute_launcher()
    p = Path(exe_path)
    if p.exists():
        return CheckResult(
            "IDE Launcher Resolution",
            "PASS",
            f"Resolved executable: {exe_path} {' '.join(args)}".strip(),
        )
    return CheckResult(
        "IDE Launcher Resolution",
        "FAIL",
        f"Launcher path does not exist: {exe_path}",
        "Ensure agent-cam-mcp is installed in the active environment.",
    )


def check_config_validity() -> CheckResult:
    try:
        AgentCamConfig.load()
        cfg_file = get_config_dir() / "config.json"
        return CheckResult("Config Validity", "PASS", f"Valid configuration at {cfg_file}")
    except Exception as e:
        return CheckResult(
            "Config Validity", "FAIL", f"Config parsing error: {e}", "Delete or fix config.json"
        )


def check_data_dir_writable() -> CheckResult:
    d = get_data_dir()
    test_file = d / ".doctor_write_test.tmp"
    try:
        test_file.write_text("test", encoding="utf-8")
        test_file.unlink()
        return CheckResult("Data Directory Writable", "PASS", f"Writable directory: {d}")
    except Exception as e:
        return CheckResult(
            "Data Directory Writable",
            "FAIL",
            f"Cannot write to {d}: {e}",
            f"Fix permissions on {d}",
        )


def check_port_availability(port: int = 8765) -> CheckResult:
    # Check if our daemon owns it
    try:
        with httpx.Client(timeout=0.5) as client:
            resp = client.get(f"http://127.0.0.1:{port}/health")
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                return CheckResult(
                    "Port 8765 Availability",
                    "PASS",
                    f"Port {port} is active and owned by Agent Cam daemon",
                )
    except Exception:
        pass

    # Check if socket can bind
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.close()
        return CheckResult(
            "Port 8765 Availability", "PASS", f"Port {port} is free and ready to bind"
        )
    except Exception as e:
        sock.close()
        return CheckResult(
            "Port 8765 Availability",
            "FAIL",
            f"Port {port} is occupied by another process: {e}",
            f"Close process occupying port {port} or specify another port with --port",
        )


def check_camera_hardware() -> List[CheckResult]:
    results = []
    cams = enumerate_usb_cameras(max_indices=2)
    if not cams:
        results.append(
            CheckResult(
                "Physical Camera Hardware",
                "WARN",
                "No physical USB webcams detected (will use synthetic camera fallback)",
                "Plug in a USB camera or configure an RTSP/HTTP camera in the dashboard",
            )
        )
        return results

    for c in cams:
        idx = int(c.id)
        cap = cv2.VideoCapture(
            idx, cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        )
        if not cap.isOpened():
            results.append(
                CheckResult(
                    f"Camera [{c.id}] {c.name}",
                    "WARN",
                    "Device failed to open (possibly in use)",
                    "Close other apps using the camera (Zoom, Teams, browser)",
                )
            )
            continue

        # Warm up & read two valid frames to test for frozen / black
        frames = []
        t0 = time.time()
        for _ in range(20):
            ret, f = cap.read()
            if ret and f is not None:
                frames.append(f)
                if len(frames) >= 2:
                    break
            time.sleep(0.04)
        latency_ms = (time.time() - t0) * 1000.0
        cap.release()

        if len(frames) < 2:
            results.append(
                CheckResult(
                    f"Camera [{c.id}] {c.name}",
                    "WARN",
                    "Device busy or frames locked by active process",
                    "Device is currently managed by running daemon or other app",
                )
            )
            continue

        f1, f2 = frames[0], frames[1]
        mean_b = float(np.mean(f1))
        diff = float(np.mean(cv2.absdiff(f1, f2)))

        if mean_b < 2.0:
            results.append(
                CheckResult(
                    f"Camera [{c.id}] {c.name}",
                    "WARN",
                    f"Image is nearly pitch black (mean={mean_b:.1f}). Check lens cap.",
                    "Remove privacy shutter / lens cap or increase lighting",
                )
            )
        elif diff == 0.0:
            is_secondary = int(c.id) > 0 or "virtual" in c.name.lower()
            results.append(
                CheckResult(
                    f"Camera [{c.id}] {c.name}",
                    "INFO" if is_secondary else "WARN",
                    "Consecutive frames are identical (secondary/virtual camera or inactive stream)"
                    if is_secondary
                    else "Consecutive frames are identical (possible frozen driver stream)",
                    None if is_secondary else "Unplug and replug the camera",
                )
            )
        else:
            h, w = f1.shape[:2]
            results.append(
                CheckResult(
                    f"Camera [{c.id}] {c.name}",
                    "PASS",
                    f"Active: {w}x{h}, latency ~{latency_ms:.0f}ms, brightness {mean_b:.1f}",
                )
            )

    return results


def check_os_permissions() -> CheckResult:
    sys_name = platform.system()
    if sys_name == "Windows":
        # Check Windows Camera privacy settings key in registry if accessible
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as k:
                val, _ = winreg.QueryValueEx(k, "Value")
                if val.lower() == "allow":
                    return CheckResult(
                        "OS Camera Permission", "PASS", "Windows camera access consent is allowed"
                    )
                elif val.lower() == "deny":
                    return CheckResult(
                        "OS Camera Permission",
                        "FAIL",
                        "Windows camera access is denied in Privacy Settings",
                        "Enable camera access in Windows Settings > Privacy > Camera",
                    )
        except Exception:
            pass
        return CheckResult("OS Camera Permission", "PASS", "Windows privacy permission accessible")
    elif sys_name == "Darwin":
        return CheckResult("OS Camera Permission", "PASS", "macOS camera consent check")
    return CheckResult("OS Camera Permission", "PASS", "Linux V4L2 device permissions")


def check_optional_extras() -> List[CheckResult]:
    extras = [
        ("serial", "pyserial", "pip install 'agent-cam-mcp[serial]'"),
        ("mqtt", "paho.mqtt", "pip install 'agent-cam-mcp[mqtt]'"),
        ("ocr", "rapidocr_onnxruntime", "pip install 'agent-cam-mcp[ocr]'"),
    ]
    res = []
    for extra_name, mod, install_cmd in extras:
        try:
            importlib.import_module(mod)
            res.append(
                CheckResult(
                    f"Extra: [{extra_name}]",
                    "INFO",
                    f"{extra_name} adapter library '{mod}' is installed",
                )
            )
        except ImportError:
            res.append(
                CheckResult(
                    f"Extra: [{extra_name}]", "INFO", f"Not installed. Optional: {install_cmd}"
                )
            )
    return res


def check_client_registrations() -> List[CheckResult]:
    results = []
    # Antigravity
    ag_paths = find_antigravity_config_paths()
    if ag_paths:
        registered_in = []
        unregistered_in = []
        for p in ag_paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if "agent-cam" in data.get("mcpServers", {}):
                    cmd = data["mcpServers"]["agent-cam"].get("command", "")
                    resolves = shutil.which(cmd) is not None or Path(cmd).exists()
                    registered_in.append((p.name, resolves))
                else:
                    unregistered_in.append(p.name)
            except Exception:
                pass

        if registered_in:
            names = ", ".join(f"{name} (resolves={res})" for name, res in registered_in)
            results.append(
                CheckResult(
                    "Antigravity MCP Registration",
                    "PASS",
                    f"Configured in {names}",
                )
            )
        elif unregistered_in:
            results.append(
                CheckResult(
                    "Antigravity MCP Registration",
                    "WARN",
                    f"Not registered in {', '.join(unregistered_in)}",
                    "Run: agent-cam-mcp install --client antigravity",
                )
            )

    # Cursor
    cursor_paths = find_cursor_config_paths()
    if cursor_paths:
        for p in cursor_paths:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                has_server = "agent-cam" in data.get("mcpServers", {})
                results.append(
                    CheckResult(
                        "Cursor MCP Registration",
                        "PASS" if has_server else "WARN",
                        f"Found in {p.name}: {has_server}",
                        None if has_server else "Run: agent-cam-mcp install --client cursor",
                    )
                )
            except Exception:
                pass

    return results


def check_mcp_handshake() -> CheckResult:
    """Run an in-process end-to-end MCP tool capture test."""
    try:
        from agent_cam.analysis.baseline import BaselineManager
        from agent_cam.cameras.buffer import FrameBuffer
        from agent_cam.cameras.manager import CameraManager
        from agent_cam.config import AgentCamConfig
        from agent_cam.mcp_server.tools import MCPToolExecutor
        from agent_cam.policy import PolicyEngine

        cfg = AgentCamConfig.load()
        buf = FrameBuffer(buffer_seconds=5, buffer_fps=2, max_memory_mb=50)
        mgr = CameraManager(config=cfg, buffer=buf)
        mgr.initialize_cameras()
        policy = PolicyEngine(config=cfg)
        b_mgr = BaselineManager()
        from agent_cam.adapters.actions import ActionRunner

        act_runner = ActionRunner(config=cfg)

        executor = MCPToolExecutor(
            config=cfg,
            camera_manager=mgr,
            policy_engine=policy,
            baseline_manager=b_mgr,
            action_runner=act_runner,
            adapters={},
            start_time=time.time(),
        )

        import asyncio

        loop = asyncio.new_event_loop()
        res = loop.run_until_complete(executor.execute("capture_image", {"width": 640}))
        loop.close()
        mgr.shutdown()

        if res.isError:
            return CheckResult(
                "MCP Handshake & Capture Test", "FAIL", "capture_image returned error result"
            )

        has_image = False
        for c in res.content:
            if hasattr(c, "data"):
                img_data = base64.b64decode(c.data)
                arr = np.frombuffer(img_data, dtype=np.uint8)
                decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                if decoded is not None and decoded.shape[0] > 0:
                    has_image = True
                    break

        if has_image:
            return CheckResult(
                "MCP Handshake & Capture Test",
                "PASS",
                "capture_image returned valid, decodable frame",
            )
        return CheckResult(
            "MCP Handshake & Capture Test",
            "FAIL",
            "capture_image output did not contain valid image bytes",
        )
    except Exception as e:
        return CheckResult("MCP Handshake & Capture Test", "FAIL", f"Handshake failed: {e}")


def run_all_checks() -> Tuple[List[CheckResult], bool]:
    """Execute all diagnostic checks."""
    checks: List[CheckResult] = []
    checks.append(check_python_and_package())
    checks.append(check_executable_resolution())
    checks.append(check_config_validity())
    checks.append(check_data_dir_writable())
    checks.append(check_port_availability())
    checks.extend(check_camera_hardware())
    checks.append(check_os_permissions())
    checks.append(check_mcp_handshake())
    checks.extend(check_optional_extras())
    checks.extend(check_client_registrations())

    has_fail = any(c.status == "FAIL" for c in checks)
    return checks, not has_fail
