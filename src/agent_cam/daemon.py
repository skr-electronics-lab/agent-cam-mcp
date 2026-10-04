"""Agent Cam Daemon running FastAPI, MJPEG preview, WebSocket, and REST API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import cv2
import mcp.types as types
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent_cam.adapters.actions import ActionRunner
from agent_cam.adapters.base import BaseAdapter
from agent_cam.adapters.logfile_adapter import LogFileAdapter
from agent_cam.adapters.mqtt_adapter import MqttAdapter
from agent_cam.adapters.serial_adapter import SerialAdapter
from agent_cam.analysis.baseline import BaselineManager
from agent_cam.cameras.buffer import FrameBuffer
from agent_cam.analysis.led import analyze_led_signal
from agent_cam.analysis.regions import crop_region
from agent_cam.cameras.manager import CameraManager
from agent_cam.config import AgentCamConfig, get_lock_file, get_token_file
from agent_cam.mcp_server.server import TOOL_DEFINITIONS, create_mcp_server
from agent_cam.mcp_server.tools import MCPToolExecutor
from agent_cam.models import CameraSettings, PrivacyMask, Region
from agent_cam.policy import PolicyEngine

logger = logging.getLogger("agent_cam.daemon")


def get_or_create_auth_token() -> str:
    """Generate or retrieve persistent per-install random token."""
    token_file = get_token_file()
    if token_file.exists():
        try:
            return token_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    token = secrets.token_hex(32)
    try:
        token_file.write_text(token, encoding="utf-8")
        try:
            os.chmod(token_file, 0o600)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Could not persist auth token file: %s", e)
    return token


class DaemonServer:
    """Core daemon managing services and serving HTTP/WS/MCP."""

    def __init__(self, config: AgentCamConfig, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.config = config
        self.host = host
        self.port = port
        self.start_time = time.time()
        self.auth_token = get_or_create_auth_token()

        self.buffer = FrameBuffer(
            buffer_seconds=config.buffer_seconds,
            buffer_fps=config.buffer_fps,
            max_memory_mb=config.max_buffer_memory_mb,
            jpeg_quality=config.jpeg_quality,
        )
        self.camera_manager = CameraManager(config=config, buffer=self.buffer)
        self.policy_engine = PolicyEngine(config=config)
        self.baseline_manager = BaselineManager()
        self.action_runner = ActionRunner(config=config)

        # Adapters
        self.adapters: Dict[str, BaseAdapter] = {}
        if config.adapters.serial.enabled:
            self.adapters["serial"] = SerialAdapter(
                port=config.adapters.serial.port,
                baudrate=config.adapters.serial.baudrate,
                timeout_s=config.adapters.serial.timeout_s,
            )
        if config.adapters.logfile.enabled and config.adapters.logfile.file_path:
            self.adapters["logfile"] = LogFileAdapter(file_path=config.adapters.logfile.file_path)
        if config.adapters.mqtt.enabled:
            self.adapters["mqtt"] = MqttAdapter(
                broker=config.adapters.mqtt.broker,
                port=config.adapters.mqtt.port,
                topics=config.adapters.mqtt.topics,
                client_id=config.adapters.mqtt.client_id,
            )

        self.tool_executor = MCPToolExecutor(
            config=self.config,
            camera_manager=self.camera_manager,
            policy_engine=self.policy_engine,
            baseline_manager=self.baseline_manager,
            action_runner=self.action_runner,
            adapters=self.adapters,
            start_time=self.start_time,
        )
        self.mcp_server = create_mcp_server(self.tool_executor)

        self._active_websockets: List[WebSocket] = []
        self._ws_broadcast_task: Optional[asyncio.Task] = None
        self.app = self._build_fastapi_app()

    def _write_lock_file(self) -> None:
        lock_file = get_lock_file()
        try:
            lock_file.write_text(
                json.dumps(
                    {"pid": os.getpid(), "port": self.port, "host": self.host, "time": time.time()}
                )
            )
        except Exception as e:
            logger.warning("Could not write lock file: %s", e)

    def _remove_lock_file(self) -> None:
        lock_file = get_lock_file()
        try:
            if lock_file.exists():
                lock_file.unlink()
        except Exception:
            pass

    def _build_fastapi_app(self) -> FastAPI:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            # Startup
            self._write_lock_file()
            self.camera_manager.initialize_cameras()
            self.camera_manager.start_buffer_collector()
            for a in self.adapters.values():
                try:
                    a.start()
                except Exception as e:
                    logger.warning("Failed starting adapter %s: %s", a.source_name, e)

            self._ws_broadcast_task = asyncio.create_task(self._websocket_broadcaster())
            yield
            # Shutdown
            if self._ws_broadcast_task:
                self._ws_broadcast_task.cancel()
            for a in self.adapters.values():
                try:
                    a.stop()
                except Exception:
                    pass
            self.camera_manager.shutdown()
            self._remove_lock_file()

        app = FastAPI(title="Agent Cam Daemon", lifespan=lifespan, docs_url=None, redoc_url=None)

        # Host and Origin security middleware (Section 7)
        @app.middleware("http")
        async def security_header_check(request: Request, call_next):
            host_header = request.headers.get("host", "")
            # Allow localhost / 127.0.0.1, testserver, or configured host
            allowed_hosts = {
                "localhost",
                "127.0.0.1",
                "testserver",
                f"localhost:{self.port}",
                f"127.0.0.1:{self.port}",
            }
            if self.host not in ("127.0.0.1", "localhost"):
                allowed_hosts.add(self.host)
                allowed_hosts.add(f"{self.host}:{self.port}")

            if host_header and host_header.lower() not in allowed_hosts:
                return JSONResponse(
                    status_code=403, content={"error": "Forbidden: Invalid Host header"}
                )

            origin_header = request.headers.get("origin")
            if origin_header:
                # Disallow cross-origin requests from arbitrary web pages (DNS rebinding defense)
                if not any(
                    origin_header.startswith(f"http://{h}") for h in ("localhost", "127.0.0.1")
                ):
                    return JSONResponse(
                        status_code=403, content={"error": "Forbidden: Untrusted Origin"}
                    )

            response = await call_next(request)
            return response

        # REST Endpoints
        @app.get("/health")
        async def health():
            cams = self.camera_manager.list_cameras_info()
            return {
                "status": "ok",
                "version": "0.1.1",
                "uptime": round(time.time() - self.start_time, 1),
                "cameras_count": len(cams),
                "paused": self.policy_engine.is_paused,
            }

        @app.get("/api/token")
        async def get_token(request: Request):
            # Token delivery allowed from localhost
            client_ip = request.client.host if request.client else ""
            if client_ip not in ("127.0.0.1", "::1", "localhost"):
                raise HTTPException(status_code=403, detail="Token access restricted to localhost")
            return {"token": self.auth_token}

        @app.get("/api/status")
        async def get_status():
            res, _, _ = await self.tool_executor.tool_get_status({}, "dashboard")
            return json.loads(res.content[0].text)

        @app.post("/api/pause")
        async def toggle_pause(request: Request):
            self._verify_auth(request)
            body = await request.json()
            paused = bool(body.get("paused", not self.policy_engine.is_paused))
            self.policy_engine.set_paused(paused)
            return {"paused": self.policy_engine.is_paused}

        @app.get("/api/cameras")
        async def list_cameras(refresh: bool = False):
            if refresh:
                self.camera_manager.initialize_cameras(force_refresh=True)
            return {"cameras": [c.model_dump() for c in self.camera_manager.list_cameras_info()]}

        @app.post("/api/cameras/ip")
        async def add_ip_camera(request: Request):
            self._verify_auth(request)
            body = await request.json()
            from agent_cam.config import IPCameraConfig

            ip_cfg = IPCameraConfig(
                id=body.get("id", f"ip-{int(time.time())}"),
                name=body.get("name", "IP Camera"),
                url=body["url"],
                fps=float(body.get("fps", 15.0)),
                rotation=int(body.get("rotation", 0)),
                flip_h=bool(body.get("flip_h", False)),
                flip_v=bool(body.get("flip_v", False)),
            )
            self.config.ip_cameras.append(ip_cfg)
            self.config.save()
            self.camera_manager.initialize_cameras(force_refresh=True)
            return {"status": "created", "camera": ip_cfg.model_dump()}

        @app.get("/api/regions")
        async def list_regions():
            return {"regions": {k: v.model_dump() for k, v in self.config.regions.items()}}

        @app.post("/api/regions")
        async def save_region(request: Request):
            self._verify_auth(request)
            body = await request.json()
            reg = Region.model_validate(body)
            self.config.regions[reg.name] = reg
            self.config.save()
            return {"status": "saved", "region": reg.model_dump()}

        @app.delete("/api/regions/{name}")
        async def delete_region(name: str, request: Request):
            self._verify_auth(request)
            if name in self.config.regions:
                del self.config.regions[name]
                self.config.save()
            return {"status": "deleted", "name": name}

        @app.get("/api/regions/{name}/rectified")
        async def get_rectified_region(name: str):
            if name not in self.config.regions:
                raise HTTPException(status_code=404, detail="Region not found")
            reg = self.config.regions[name]
            frame, _, _ = self.camera_manager.get_frame(reg.camera)
            if frame is None:
                raise HTTPException(status_code=503, detail="Camera frame unavailable")
            warped, _ = crop_region(frame, reg)
            ret, buf = cv2.imencode(".jpg", warped)
            if not ret:
                raise HTTPException(status_code=500, detail="Encoding failed")
            return Response(content=buf.tobytes(), media_type="image/jpeg")

        @app.post("/api/led/analyze")
        async def api_analyze_led(request: Request):
            self._verify_auth(request)
            body = await request.json()
            region_name = body.get("region")
            camera_id = body.get("camera")
            duration_s = max(0.5, min(5.0, float(body.get("duration_s", 1.5))))
            reg = self.config.regions.get(region_name) if region_name else None
            samples = []
            start_t = time.time()
            interval = 0.04
            while time.time() - start_t < duration_s:
                t_now = time.time()
                f, _, _ = self.camera_manager.get_frame(camera_id)
                if f is not None:
                    if reg:
                        crop, _ = crop_region(f, reg)
                        samples.append((t_now, crop))
                    else:
                        samples.append((t_now, f))
                await asyncio.sleep(interval)
            cid = camera_id if camera_id else str(self.config.primary_camera)
            res = analyze_led_signal(samples, cid, region_name)
            return res.model_dump()

        @app.post("/api/cameras/{camera_id}/controls")
        async def api_camera_controls(camera_id: str, request: Request):
            self._verify_auth(request)
            body = await request.json()
            settings = CameraSettings(
                camera=camera_id,
                exposure=body.get("exposure"),
                focus=body.get("focus"),
                brightness=body.get("brightness"),
                contrast=body.get("contrast"),
                gain=body.get("gain"),
                white_balance=body.get("white_balance"),
                lock_auto=body.get("lock_auto"),
            )
            res = self.camera_manager.set_camera_settings(settings)
            return res.model_dump()

        @app.get("/api/privacy_masks")
        async def list_privacy_masks():
            return {"masks": [m.model_dump() for m in self.config.privacy_masks]}

        @app.post("/api/privacy_masks")
        async def add_privacy_mask(request: Request):
            self._verify_auth(request)
            body = await request.json()
            mask = PrivacyMask.model_validate(body)
            self.config.privacy_masks.append(mask)
            self.config.save()
            return {"status": "saved", "mask": mask.model_dump()}

        @app.delete("/api/privacy_masks/{mask_id}")
        async def delete_privacy_mask(mask_id: str, request: Request):
            self._verify_auth(request)
            self.config.privacy_masks = [m for m in self.config.privacy_masks if m.id != mask_id]
            self.config.save()
            return {"status": "deleted", "id": mask_id}

        @app.get("/api/baselines")
        async def list_baselines():
            return {"baselines": [b.model_dump() for b in self.baseline_manager.list_baselines()]}

        @app.delete("/api/baselines/{name}")
        async def delete_baseline(name: str, request: Request):
            self._verify_auth(request)
            deleted = self.baseline_manager.delete_baseline(name)
            return {"status": "deleted" if deleted else "not_found", "name": name}

        @app.get("/api/approvals")
        async def list_approvals():
            return {"approvals": self.policy_engine.list_pending_approvals()}

        @app.post("/api/approvals/{approval_id}")
        async def resolve_approval(approval_id: str, request: Request):
            self._verify_auth(request)
            body = await request.json()
            approved = bool(body.get("approved", True))
            ok = self.policy_engine.resolve_approval(approval_id, approved)
            return {"status": "resolved" if ok else "error", "approved": approved}

        @app.get("/api/activity")
        async def list_activity(limit: int = 50):
            return {"activity": self.policy_engine.get_recent_activity(limit=limit)}

        @app.get("/api/activity/{record_id}/image")
        async def get_activity_image(record_id: str):
            img_bytes = self.policy_engine.get_activity_image(record_id)
            if not img_bytes:
                raise HTTPException(status_code=404, detail="No image for this activity record")
            return Response(content=img_bytes, media_type="image/jpeg")

        @app.get("/api/settings")
        async def get_settings():
            return self.config.model_dump()

        @app.post("/api/settings")
        async def update_settings(request: Request):
            self._verify_auth(request)
            body = await request.json()
            for k, v in body.items():
                if hasattr(self.config, k):
                    setattr(self.config, k, v)
            self.config.save()
            return {"status": "updated", "config": self.config.model_dump()}

        @app.get("/api/doctor")
        async def run_doctor():
            from agent_cam.cli import execute_doctor_checks

            report = await asyncio.to_thread(execute_doctor_checks, json_mode=True)
            return report

        # MCP fast REST tool execution bridge
        @app.get("/api/mcp/list_tools")
        async def mcp_list_tools():
            tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.inputSchema,
                }
                for t in TOOL_DEFINITIONS
            ]
            return {"tools": tools}

        @app.post("/api/mcp/call_tool")
        async def mcp_call_tool(request: Request):
            body = await request.json()
            tool_name = body.get("name")
            args = body.get("arguments", {})
            client_id = body.get("client_id", "agent-stdio")
            res = await self.tool_executor.execute(tool_name, args, client_id=client_id)
            # Serialize CallToolResult
            contents = []
            for c in res.content:
                if isinstance(c, types.TextContent):
                    contents.append({"type": "text", "text": c.text})
                elif isinstance(c, types.ImageContent):
                    contents.append({"type": "image", "data": c.data, "mimeType": c.mimeType})
            return {"content": contents, "isError": res.isError}

        # MJPEG Preview Stream
        @app.get("/stream/{camera_id}")
        async def stream_mjpeg(camera_id: str):
            cam = self.camera_manager.get_camera(camera_id)
            if not cam:
                raise HTTPException(status_code=404, detail=f"Camera '{camera_id}' not found")

            async def frame_generator():
                cam.increment_active_user()
                encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 65]
                try:
                    while True:
                        frame, _ = cam.get_latest_frame(mark_used=True)
                        if frame is not None:
                            # Resize to 720p if larger for smooth 15-20fps streaming
                            h, w = frame.shape[:2]
                            if w > 1280:
                                scale = 1280.0 / w
                                frame = cv2.resize(
                                    frame, (1280, int(h * scale)), interpolation=cv2.INTER_AREA
                                )

                            ret, jpeg = cv2.imencode(".jpg", frame, encode_param)
                            if ret:
                                yield (
                                    b"--frame\r\n"
                                    b"Content-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
                                )
                        await asyncio.sleep(0.05)  # Target ~20 fps
                finally:
                    cam.decrement_active_user()

            return StreamingResponse(
                frame_generator(),
                media_type="multipart/x-mixed-replace; boundary=frame",
            )

        # WebSocket for live dashboard metrics and updates
        @app.websocket("/ws")
        async def websocket_endpoint(ws: WebSocket):
            await ws.accept()
            self._active_websockets.append(ws)
            try:
                while True:
                    data = await ws.receive_text()
                    # Client can ping or send commands
                    if data == "ping":
                        await ws.send_text("pong")
            except WebSocketDisconnect:
                pass
            finally:
                if ws in self._active_websockets:
                    self._active_websockets.remove(ws)

        # Mount static UI files
        static_dir = Path(__file__).parent / "web" / "static"
        if static_dir.exists():
            app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

        return app

    def _verify_auth(self, request: Request) -> None:
        """Verify auth token for mutating endpoints."""
        token = request.headers.get("x-auth-token")
        if not token or token != self.auth_token:
            # Check query param as fallback
            q_token = request.query_params.get("token")
            if not q_token or q_token != self.auth_token:
                raise HTTPException(
                    status_code=401, detail="Unauthorized: invalid or missing x-auth-token"
                )

    async def _websocket_broadcaster(self) -> None:
        """Broadcast live status and metrics to connected UI clients every 500ms."""
        while True:
            await asyncio.sleep(0.5)
            if not self._active_websockets:
                continue

            status_data = {
                "type": "heartbeat",
                "timestamp": time.time(),
                "uptime": round(time.time() - self.start_time, 1),
                "paused": self.policy_engine.is_paused,
                "active_agents": self.policy_engine.get_active_agent_count(),
                "buffer_fill_s": round(self.buffer.get_fill_seconds(), 1),
                "buffer_ram_mb": round(self.buffer.get_memory_usage_mb(), 2),
                "pending_approvals": len(self.policy_engine.list_pending_approvals()),
                "cameras": [c.model_dump() for c in self.camera_manager.list_cameras_info()],
            }
            msg = json.dumps(status_data)
            dead = []
            for ws in self._active_websockets:
                try:
                    await ws.send_text(msg)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                if ws in self._active_websockets:
                    self._active_websockets.remove(ws)
