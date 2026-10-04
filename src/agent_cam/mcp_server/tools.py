"""Implementation of all Agent Cam MCP tools conforming to the specification."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import mcp.types as types
import numpy as np

from agent_cam.adapters.actions import ActionRunner
from agent_cam.adapters.base import BaseAdapter
from agent_cam.analysis.baseline import BaselineManager
from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.analysis.ocr import OCRError, perform_ocr
from agent_cam.analysis.regions import annotate_regions, crop_region
from agent_cam.analysis.sequence import create_contact_sheet
from agent_cam.analysis.watcher import watch_condition
from agent_cam.cameras.manager import CameraManager
from agent_cam.config import AgentCamConfig
from agent_cam.models import (
    CameraSettings,
    ErrorCode,
    EventLine,
    Region,
    RegionUnit,
    StructuredError,
    WatchCondition,
)
from agent_cam.policy import PolicyEngine

logger = logging.getLogger(__name__)


def encode_image_content(
    frame: np.ndarray,
    target_width: int = 1024,
    fmt: str = "jpeg",
    quality: int = 80,
) -> Tuple[types.ImageContent, bytes]:
    """Resize frame to target width and encode as base64 MCP ImageContent."""
    h, w = frame.shape[:2]
    if target_width > 0 and w > target_width:
        scale = target_width / float(w)
        target_h = int(h * scale)
        frame = cv2.resize(frame, (target_width, target_h), interpolation=cv2.INTER_AREA)

    if fmt.lower() == "png":
        ext = ".png"
        mime = "image/png"
        params = [int(cv2.IMWRITE_PNG_COMPRESSION), 4]
    else:
        ext = ".jpg"
        mime = "image/jpeg"
        params = [int(cv2.IMWRITE_JPEG_QUALITY), max(10, min(100, quality))]

    ret, enc = cv2.imencode(ext, frame, params)
    if not ret:
        raise ValueError("Failed encoding frame")

    raw_bytes = enc.tobytes()
    b64_str = base64.b64encode(raw_bytes).decode("ascii")
    return types.ImageContent(type="image", data=b64_str, mimeType=mime), raw_bytes


def make_error_result(err: StructuredError) -> types.CallToolResult:
    """Format structured error as an MCP tool error result."""
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=err.model_dump_json(indent=2))],
        isError=True,
    )


class MCPToolExecutor:
    """Executes MCP tools against the core CameraManager, PolicyEngine, and Adapters."""

    def __init__(
        self,
        config: AgentCamConfig,
        camera_manager: CameraManager,
        policy_engine: PolicyEngine,
        baseline_manager: BaselineManager,
        action_runner: ActionRunner,
        adapters: Dict[str, BaseAdapter],
        start_time: float,
    ) -> None:
        self.config = config
        self.camera_manager = camera_manager
        self.policy_engine = policy_engine
        self.baseline_manager = baseline_manager
        self.action_runner = action_runner
        self.adapters = adapters
        self.start_time = start_time

    async def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        client_id: str = "agent",
    ) -> types.CallToolResult:
        """Dispatch and execute an MCP tool with audit logging and rate limiting."""
        call_start = time.time()
        self.policy_engine.record_agent_heartbeat(client_id)

        # 1. Check if user paused agent access
        paused_err = self.policy_engine.check_paused()
        if paused_err and name != "get_status":
            self.policy_engine.log_activity(
                client=client_id,
                tool=name,
                parameters=arguments,
                duration_ms=(time.time() - call_start) * 1000.0,
                status="paused",
                error_code=paused_err.error_code,
            )
            return make_error_result(paused_err)

        # 2. Rate limiting check on image-returning tools
        image_tools = {
            "capture_image",
            "capture_sequence",
            "get_timeline",
            "watch",
            "compare_to_baseline",
        }
        if name in image_tools:
            rate_err = self.policy_engine.check_rate_limit(client_id, is_image_call=True)
            if rate_err:
                self.policy_engine.log_activity(
                    client=client_id,
                    tool=name,
                    parameters=arguments,
                    duration_ms=(time.time() - call_start) * 1000.0,
                    status="rate_limited",
                    error_code=rate_err.error_code,
                )
                return make_error_result(rate_err)

        # 3. Route tool call
        try:
            handler = getattr(self, f"tool_{name}", None)
            if not handler:
                err = StructuredError(
                    error_code=ErrorCode.INVALID_ARGUMENT.value,
                    message=f"Unknown tool '{name}'",
                    fix="Check tool name spelling against available tools list.",
                    retryable=False,
                )
                return make_error_result(err)

            res, preview_bytes, summary = await handler(arguments, client_id)
            duration_ms = (time.time() - call_start) * 1000.0

            self.policy_engine.log_activity(
                client=client_id,
                tool=name,
                parameters=arguments,
                duration_ms=duration_ms,
                status="error"
                if getattr(res, "isError", getattr(res, "is_error", False))
                else "success",
                image_bytes=preview_bytes,
                result_summary=summary,
            )
            return res
        except StructuredError as se:
            duration_ms = (time.time() - call_start) * 1000.0
            self.policy_engine.log_activity(
                client=client_id,
                tool=name,
                parameters=arguments,
                duration_ms=duration_ms,
                status="error",
                error_code=se.error_code,
            )
            return make_error_result(se)
        except Exception as e:
            duration_ms = (time.time() - call_start) * 1000.0
            logger.exception("Unexpected error in tool %s: %s", name, e)
            err = StructuredError(
                error_code=ErrorCode.INTERNAL_ERROR.value,
                message=f"Internal error executing {name}: {str(e)}",
                fix="Check daemon logs for trace details.",
                retryable=True,
            )
            self.policy_engine.log_activity(
                client=client_id,
                tool=name,
                parameters=arguments,
                duration_ms=duration_ms,
                status="error",
                error_code=err.error_code,
            )
            return make_error_result(err)

    # ---------------- Tool Implementations ----------------

    async def tool_get_status(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        uptime = time.time() - self.start_time
        cameras = self.camera_manager.list_cameras_info()
        data = {
            "version": "0.1.0",
            "uptime_seconds": round(uptime, 1),
            "paused_by_user": self.policy_engine.is_paused,
            "active_agent_connections": self.policy_engine.get_active_agent_count(),
            "buffer_fill_seconds": round(self.camera_manager.buffer.get_fill_seconds(), 1),
            "buffer_memory_mb": round(self.camera_manager.buffer.get_memory_usage_mb(), 2),
            "cameras": [c.model_dump() for c in cameras],
            "adapters": {k: a.is_running for k, a in self.adapters.items()},
            "regions_defined": len(self.config.regions),
            "warnings": [],
        }
        if not cameras:
            data["warnings"].append("No cameras currently detected or open.")
        if self.policy_engine.is_paused:
            data["warnings"].append("Agent access is currently PAUSED by user in Dashboard.")

        summary = f"Status OK. Cameras: {len(cameras)}, Paused: {self.policy_engine.is_paused}"
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
            ),
            None,
            summary,
        )

    async def tool_list_cameras(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        refresh = args.get("refresh", False)
        if refresh:
            await asyncio.to_thread(self.camera_manager.initialize_cameras, force_refresh=True)

        cams = self.camera_manager.list_cameras_info()
        data = {"cameras": [c.model_dump() for c in cams]}
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
            ),
            None,
            f"Listed {len(cams)} cameras",
        )

    async def tool_capture_image(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        camera_id = args.get("camera")
        region_name = args.get("region")
        target_width = min(3840, max(64, int(args.get("width", self.config.default_image_width))))
        annotate = bool(args.get("annotate", False))
        fmt = str(args.get("format", "jpeg"))

        frame, ts, actual_cam_id = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
        if frame is None:
            raise StructuredError(
                error_code=ErrorCode.NO_CAMERAS.value,
                message=f"Could not grab frame from camera '{camera_id or 'primary'}'.",
                fix="Ensure camera is plugged in and not in use by another app.",
                retryable=True,
            )

        crop_info = None
        if region_name:
            if region_name not in self.config.regions:
                raise StructuredError(
                    error_code=ErrorCode.REGION_NOT_FOUND.value,
                    message=f"Region '{region_name}' is not defined.",
                    fix="Use define_region or check list_regions for existing names.",
                    retryable=False,
                )
            reg = self.config.regions[region_name]
            frame, bounds = crop_region(frame, reg)
            crop_info = {"region": region_name, "bounds": bounds}
        elif annotate and self.config.regions:
            frame = annotate_regions(frame, list(self.config.regions.values()))

        img_content, raw_bytes = encode_image_content(
            frame,
            target_width=target_width,
            fmt=fmt,
            quality=self.config.jpeg_quality,
        )

        meta = {
            "camera": actual_cam_id,
            "timestamp": ts,
            "width": frame.shape[1],
            "height": frame.shape[0],
            "region": crop_info,
        }

        res = types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps(meta, indent=2)),
                img_content,
            ]
        )
        return (
            res,
            raw_bytes,
            f"Captured {meta['width']}x{meta['height']} image from camera {actual_cam_id}",
        )

    async def tool_capture_sequence(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        camera_id = args.get("camera")
        seconds = min(30, max(1, int(args.get("seconds", 3))))
        fps = min(10, max(1, int(args.get("fps", 2))))
        region_name = args.get("region")

        target_frames = seconds * fps
        interval = 1.0 / float(fps)
        frames_ts: List[Tuple[np.ndarray, float]] = []

        reg = self.config.regions.get(region_name) if region_name else None

        for _ in range(target_frames):
            frame, ts, _ = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
            if frame is not None:
                if reg:
                    frame, _ = crop_region(frame, reg)
                frames_ts.append((frame, ts))
            await asyncio.sleep(interval)

        if not frames_ts:
            raise StructuredError(
                error_code=ErrorCode.TIMEOUT.value,
                message="Failed capturing frames for sequence.",
                fix="Check camera status and retry.",
                retryable=True,
            )

        contact_sheet = await asyncio.to_thread(create_contact_sheet, frames_ts)
        img_content, raw_bytes = encode_image_content(
            contact_sheet, target_width=1920, fmt="jpeg", quality=self.config.jpeg_quality
        )

        meta = {
            "frames_captured": len(frames_ts),
            "duration_seconds": seconds,
            "fps": fps,
            "sheet_width": contact_sheet.shape[1],
            "sheet_height": contact_sheet.shape[0],
        }
        res = types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps(meta, indent=2)),
                img_content,
            ]
        )
        return res, raw_bytes, f"Captured sequence contact sheet ({len(frames_ts)} frames)"

    async def tool_get_timeline(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        last_seconds = min(60, max(1, int(args.get("last_seconds", 15))))
        camera_id = args.get("camera")
        include_events = bool(args.get("include_events", True))

        compressed_frames = self.camera_manager.buffer.get_frames(
            last_seconds=last_seconds, camera_id=camera_id
        )
        if not compressed_frames:
            # Fallback to current live frame
            f, ts, cid = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
            if f is not None:
                frames_ts = [(f, ts)]
            else:
                frames_ts = []
        else:
            # Subsample up to 16 frames for the sheet
            step = max(1, len(compressed_frames) // 16)
            frames_ts = []
            for item in compressed_frames[::step]:
                decoded = item.decode()
                if decoded is not None:
                    frames_ts.append((decoded, item.timestamp))

        # Collect event lines
        event_lines: List[EventLine] = []
        if include_events:
            for adapter in self.adapters.values():
                event_lines.extend(adapter.get_lines(last_seconds=last_seconds))
            event_lines.sort(key=lambda x: x.timestamp)

        sheet = await asyncio.to_thread(create_contact_sheet, frames_ts, event_lines=event_lines)
        img_content, raw_bytes = encode_image_content(
            sheet, target_width=1920, fmt="jpeg", quality=80
        )

        meta = {
            "buffered_frames_included": len(frames_ts),
            "event_lines_included": len(event_lines),
            "window_seconds": last_seconds,
        }
        res = types.CallToolResult(
            content=[
                types.TextContent(type="text", text=json.dumps(meta, indent=2)),
                img_content,
            ]
        )
        return (
            res,
            raw_bytes,
            f"Generated timeline sheet ({len(frames_ts)} frames, {len(event_lines)} events)",
        )

    async def tool_measure(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        camera_id = args.get("camera")
        region_name = args.get("region")

        frame, ts, _ = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
        if frame is None:
            raise StructuredError(
                error_code=ErrorCode.NO_CAMERAS.value,
                message="Camera frame unavailable for measurement.",
                fix="Check camera status and connection.",
                retryable=True,
            )

        if region_name:
            if region_name not in self.config.regions:
                raise StructuredError(
                    error_code=ErrorCode.REGION_NOT_FOUND.value,
                    message=f"Region '{region_name}' is not defined.",
                    fix="Use define_region or verify name.",
                    retryable=False,
                )
            frame, _ = crop_region(frame, self.config.regions[region_name])

        result = await asyncio.to_thread(calculate_metrics, frame)
        summary = f"Mean brightness: {result.mean_brightness}, Contrast: {result.contrast}, Lit%: {result.lit_pixel_percentage}%"
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=result.model_dump_json(indent=2))]
            ),
            None,
            summary,
        )

    async def tool_watch(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        condition_str = args.get("condition")
        try:
            condition = WatchCondition(condition_str)
        except Exception:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message=f"Invalid watch condition: '{condition_str}'",
                fix=f"Valid conditions are: {[c.value for c in WatchCondition]}",
                retryable=False,
            )

        threshold = (
            float(args["threshold"])
            if "threshold" in args and args["threshold"] is not None
            else None
        )
        timeout_s = min(120.0, max(1.0, float(args.get("timeout_s", 20.0))))
        camera_id = args.get("camera")
        region_name = args.get("region")

        reg = self.config.regions.get(region_name) if region_name else None

        def frame_getter() -> Tuple[Optional[np.ndarray], float]:
            f, ts, _ = self.camera_manager.get_frame(camera_id)
            if f is not None and reg:
                f, _ = crop_region(f, reg)
            return f, ts

        watch_res, before_img, after_img = await watch_condition(
            frame_getter=frame_getter,
            condition=condition,
            threshold=threshold,
            timeout_s=timeout_s,
        )

        content: List[types.Content] = [
            types.TextContent(type="text", text=watch_res.model_dump_json(indent=2))
        ]

        preview_bytes = None
        if before_img is not None and after_img is not None:
            # Combine before and after into a comparison sheet
            comparison_sheet = create_contact_sheet(
                [(before_img, watch_res.before_timestamp), (after_img, watch_res.after_timestamp)]
            )
            img_c, preview_bytes = encode_image_content(
                comparison_sheet, target_width=1280, fmt="jpeg", quality=80
            )
            content.append(img_c)

        summary = (
            f"Watch '{condition.value}' fired={watch_res.fired} in {watch_res.elapsed_seconds}s"
        )
        return types.CallToolResult(content=content), preview_bytes, summary

    async def tool_define_region(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        name = str(args.get("name", "")).strip()
        if not name:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message="Region name cannot be empty.",
                fix="Provide a descriptive name like 'led_status' or 'display_zone'.",
                retryable=False,
            )

        x = float(args.get("x", 0.0))
        y = float(args.get("y", 0.0))
        w = float(args.get("w", 0.0))
        h = float(args.get("h", 0.0))
        units_str = str(args.get("units", "normalized")).lower()
        units = RegionUnit.PIXELS if units_str == "pixels" else RegionUnit.NORMALIZED
        camera = args.get("camera")

        reg = Region(name=name, x=x, y=y, w=w, h=h, camera=camera, units=units)
        self.config.regions[name] = reg
        self.config.save()

        res_data = {"status": "created", "region": reg.model_dump()}
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(res_data, indent=2))]
            ),
            None,
            f"Defined region '{name}'",
        )

    async def tool_list_regions(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        data = {"regions": {k: v.model_dump() for k, v in self.config.regions.items()}}
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
            ),
            None,
            f"Listed {len(self.config.regions)} regions",
        )

    async def tool_delete_region(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        name = str(args.get("name", "")).strip()
        if name in self.config.regions:
            del self.config.regions[name]
            self.config.save()
            data = {"status": "deleted", "region": name}
        else:
            data = {"status": "not_found", "region": name}
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
            ),
            None,
            f"Deleted region '{name}'",
        )

    async def tool_request_region_from_user(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        name = str(args.get("name", "user_region"))
        message = str(args.get("message", "Please draw the region on the camera preview."))

        # Check if already defined
        if name in self.config.regions:
            data = {"status": "ready", "region": self.config.regions[name].model_dump()}
            return (
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
                ),
                None,
                f"Region '{name}' already present",
            )

        # Poll for up to 30s to see if user draws it on Dashboard
        for _ in range(60):
            await asyncio.sleep(0.5)
            if name in self.config.regions:
                data = {"status": "drawn_by_user", "region": self.config.regions[name].model_dump()}
                return (
                    types.CallToolResult(
                        content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
                    ),
                    None,
                    f"User drew region '{name}'",
                )

        raise StructuredError(
            error_code=ErrorCode.TIMEOUT.value,
            message=f"Timed out waiting for user to draw region '{name}'. Prompt: '{message}'",
            fix="Ensure the Dashboard UI is open at http://127.0.0.1:8765/ and draw the region.",
            retryable=True,
        )

    async def tool_save_baseline(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        name = str(args.get("name", "")).strip()
        if not name:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message="Baseline name cannot be empty.",
                fix="Provide a descriptive name like 'golden_board_off' or 'initial_state'.",
                retryable=False,
            )

        camera_id = args.get("camera")
        region_name = args.get("region")

        frame, _, actual_cid = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
        if frame is None:
            raise StructuredError(
                error_code=ErrorCode.NO_CAMERAS.value,
                message="Camera frame unavailable to save baseline.",
                fix="Check camera status.",
                retryable=True,
            )

        if region_name:
            if region_name not in self.config.regions:
                raise StructuredError(
                    error_code=ErrorCode.REGION_NOT_FOUND.value,
                    message=f"Region '{region_name}' is not defined.",
                    fix="Define region first or capture whole frame.",
                    retryable=False,
                )
            frame, _ = crop_region(frame, self.config.regions[region_name])

        info = await asyncio.to_thread(
            self.baseline_manager.save_baseline,
            name=name,
            frame=frame,
            camera_id=actual_cid or "0",
            region=region_name,
        )

        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=info.model_dump_json(indent=2))]
            ),
            None,
            f"Saved baseline '{name}'",
        )

    async def tool_compare_to_baseline(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        name = str(args.get("name", "")).strip()
        camera_id = args.get("camera")
        region_name = args.get("region")

        frame, _, _ = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
        if frame is None:
            raise StructuredError(
                error_code=ErrorCode.NO_CAMERAS.value,
                message="Camera frame unavailable for comparison.",
                fix="Check camera status.",
                retryable=True,
            )

        if region_name:
            if region_name not in self.config.regions:
                raise StructuredError(
                    error_code=ErrorCode.REGION_NOT_FOUND.value,
                    message=f"Region '{region_name}' is not defined.",
                    fix="Verify region name.",
                    retryable=False,
                )
            frame, _ = crop_region(frame, self.config.regions[region_name])

        comp_result, diff_img = await asyncio.to_thread(self.baseline_manager.compare, name, frame)
        if comp_result is None or diff_img is None:
            raise StructuredError(
                error_code=ErrorCode.BASELINE_NOT_FOUND.value,
                message=f"Baseline '{name}' not found.",
                fix="Save the baseline first with save_baseline.",
                retryable=False,
            )

        img_content, raw_bytes = encode_image_content(
            diff_img, target_width=1024, fmt="jpeg", quality=80
        )
        res = types.CallToolResult(
            content=[
                types.TextContent(type="text", text=comp_result.model_dump_json(indent=2)),
                img_content,
            ]
        )
        summary = f"Compared to '{name}': SSIM={comp_result.similarity_score}, Changed%={comp_result.percent_changed_pixels}%"
        return res, raw_bytes, summary

    async def tool_read_text(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        camera_id = args.get("camera")
        region_name = args.get("region")

        frame, _, _ = await asyncio.to_thread(self.camera_manager.get_frame, camera_id)
        if frame is None:
            raise StructuredError(
                error_code=ErrorCode.NO_CAMERAS.value,
                message="Camera frame unavailable for OCR.",
                fix="Check camera status.",
                retryable=True,
            )

        if region_name:
            if region_name not in self.config.regions:
                raise StructuredError(
                    error_code=ErrorCode.REGION_NOT_FOUND.value,
                    message=f"Region '{region_name}' is not defined.",
                    fix="Verify region name.",
                    retryable=False,
                )
            frame, _ = crop_region(frame, self.config.regions[region_name])

        try:
            text, confidence = await asyncio.to_thread(perform_ocr, frame)
            data = {"text": text, "confidence": confidence}
            return (
                types.CallToolResult(
                    content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
                ),
                None,
                f"OCR extracted {len(text)} characters",
            )
        except OCRError as oe:
            code = (
                ErrorCode.NOT_INSTALLED.value
                if oe.is_not_installed
                else ErrorCode.ADAPTER_ERROR.value
            )
            raise StructuredError(
                error_code=code,
                message=str(oe),
                fix="Install OCR extra: pip install 'agent-cam-mcp[ocr]'",
                retryable=False,
            )

    async def tool_set_camera_settings(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        camera_id = str(args.get("camera", self.config.primary_camera))
        settings = CameraSettings(
            camera=camera_id,
            exposure=args.get("exposure"),
            focus=args.get("focus"),
            brightness=args.get("brightness"),
            white_balance=args.get("white_balance"),
            lock_auto=args.get("lock_auto"),
        )
        res = await asyncio.to_thread(self.camera_manager.set_camera_settings, settings)
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=res.model_dump_json(indent=2))]
            ),
            None,
            f"Negotiated camera settings for {camera_id}",
        )

    async def tool_read_events(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        source = str(args.get("source", ""))
        last_seconds = float(args.get("last_seconds", 30.0))
        max_lines = int(args.get("lines", 100))
        until_text = args.get("until_text")
        timeout_s = min(60.0, max(0.5, float(args.get("timeout_s", 5.0))))

        adapter = None
        for a_name, a in self.adapters.items():
            if source in (a_name, a.source_name):
                adapter = a
                break

        if not adapter:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message=f"Event adapter '{source}' is not configured or running.",
                fix=f"Available adapters: {list(self.adapters.keys())}. Configure in config.json.",
                retryable=False,
            )

        deadline = time.time() + timeout_s
        matched_lines = []

        while time.time() < deadline:
            lines = adapter.get_lines(last_seconds=last_seconds, max_lines=max_lines)
            if until_text:
                found = any(until_text in line.text for line in lines)
                if found:
                    matched_lines = lines
                    break
            else:
                matched_lines = lines
                break
            await asyncio.sleep(0.2)

        data = {
            "source": adapter.source_name,
            "lines": [line.model_dump() for line in matched_lines],
            "count": len(matched_lines),
        }
        return (
            types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(data, indent=2))]
            ),
            None,
            f"Read {len(matched_lines)} event lines from {adapter.source_name}",
        )

    async def tool_run_action(
        self, args: Dict[str, Any], client_id: str
    ) -> Tuple[types.CallToolResult, Optional[bytes], str]:
        action_name = str(args.get("name", "")).strip()
        extra_args = args.get("args")
        action_cfg = self.action_runner.get_action(action_name)
        if not action_cfg:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message=f"Action '{action_name}' is not in the configured allowlist.",
                fix="Add this action to config.json under adapters.actions.",
                retryable=False,
            )

        # Check approval requirement
        if self.config.approval_mode != "off" and action_cfg.requires_approval:
            req = self.policy_engine.request_approval(action_name, args, client_id)
            # Wait up to 60s for human to click Approve in Dashboard
            approved = await asyncio.to_thread(req.event.wait, timeout=60.0)
            if not approved or req.status != "approved":
                raise StructuredError(
                    error_code=ErrorCode.APPROVAL_DENIED.value,
                    message=f"Action '{action_name}' was not approved by user.",
                    fix="Approve the pending action in the Agent Cam Dashboard.",
                    retryable=True,
                )

        # Run the action
        res = await asyncio.to_thread(self.action_runner.run, action_name, extra_args)

        content: List[types.Content] = [
            types.TextContent(type="text", text=json.dumps(res, indent=2))
        ]
        preview_bytes = None

        # If observe_seconds is set, capture post-action timeline
        observe_sec = action_cfg.observe_seconds
        if observe_sec > 0:
            timeline_res, preview_bytes, _ = await self.tool_get_timeline(
                {"last_seconds": observe_sec}, client_id
            )
            content.extend(timeline_res.content)

        summary = f"Executed action '{action_name}' (exit code {res.get('exit_code')})"
        return types.CallToolResult(content=content), preview_bytes, summary
