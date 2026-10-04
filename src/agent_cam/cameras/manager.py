"""Camera manager coordinating cameras, lazy activation, and frame buffer integration."""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

import numpy as np

from agent_cam.cameras.base import BaseCamera
from agent_cam.cameras.buffer import FrameBuffer
from agent_cam.cameras.fake_cam import FakeCamera
from agent_cam.cameras.ip_cam import IPCamera
from agent_cam.cameras.opencv_cam import OpenCVCamera, enumerate_usb_cameras
from agent_cam.config import AgentCamConfig
from agent_cam.models import (
    CameraInfo,
    CameraSettings,
    CameraSettingsResult,
    CameraSourceType,
    CameraState,
    PrivacyMask,
)

logger = logging.getLogger(__name__)


class CameraManager:
    """Manages active cameras, buffer aggregation, and lifecycle."""

    def __init__(self, config: AgentCamConfig, buffer: FrameBuffer) -> None:
        self.config = config
        self.buffer = buffer
        self._lock = threading.Lock()
        self._cameras: Dict[str, BaseCamera] = {}
        self._buffer_worker_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def get_privacy_masks(self) -> List[PrivacyMask]:
        return self.config.privacy_masks

    def initialize_cameras(self, force_refresh: bool = False) -> None:
        """Scan and initialize configured and discovered cameras."""
        with self._lock:
            if self._cameras and not force_refresh:
                return

            # Keep existing cameras if possible
            existing = dict(self._cameras)
            self._cameras.clear()

            # If fake camera mode requested or forced in config
            if self.config.use_fake_camera:
                fake_id = "fake-0"
                if fake_id in existing and isinstance(existing[fake_id], FakeCamera):
                    self._cameras[fake_id] = existing[fake_id]
                else:
                    self._cameras[fake_id] = FakeCamera(
                        camera_id=fake_id,
                        idle_timeout_s=self.config.idle_release_seconds,
                        privacy_masks_provider=self.get_privacy_masks,
                    )
                return

            # Enumerate USB cameras
            usb_cams = enumerate_usb_cameras(max_indices=4)
            for info in usb_cams:
                idx = int(info.id)
                if info.id in existing and isinstance(existing[info.id], OpenCVCamera):
                    cam = existing[info.id]
                    cam.info.name = info.name
                    self._cameras[info.id] = cam
                else:
                    self._cameras[info.id] = OpenCVCamera(
                        info=info,
                        device_index=idx,
                        idle_timeout_s=self.config.idle_release_seconds,
                        privacy_masks_provider=self.get_privacy_masks,
                    )

            # Add IP cameras from config
            for ip_cfg in self.config.ip_cameras:
                info = CameraInfo(
                    id=ip_cfg.id,
                    name=ip_cfg.name,
                    source_type=CameraSourceType.IP,
                    source_uri=ip_cfg.url,
                    state=CameraState.IDLE,
                    fps=ip_cfg.fps,
                    rotation=ip_cfg.rotation,
                    flip_horizontal=ip_cfg.flip_h,
                    flip_vertical=ip_cfg.flip_v,
                )
                self._cameras[ip_cfg.id] = IPCamera(
                    info=info,
                    url=ip_cfg.url,
                    idle_timeout_s=self.config.idle_release_seconds,
                    privacy_masks_provider=self.get_privacy_masks,
                )

            # If no physical or IP cameras found at all, create a synthetic fallback so tools never hard crash
            if not self._cameras:
                logger.info(
                    "No physical cameras discovered. Initializing synthetic fallback camera."
                )
                fake_id = "0"
                self._cameras[fake_id] = FakeCamera(
                    camera_id=fake_id,
                    name="Default Synthetic Camera (No hardware detected)",
                    idle_timeout_s=self.config.idle_release_seconds,
                    privacy_masks_provider=self.get_privacy_masks,
                )

    def start_buffer_collector(self) -> None:
        """Start background buffer sampler across active streaming cameras."""
        if self._buffer_worker_thread and self._buffer_worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._buffer_worker_thread = threading.Thread(
            target=self._buffer_loop,
            name="BufferCollector",
            daemon=True,
        )
        self._buffer_worker_thread.start()

    def _buffer_loop(self) -> None:
        """Periodically pushes latest frame from streaming cameras to the ring buffer."""
        interval = 1.0 / max(1.0, float(self.config.buffer_fps))
        while not self._stop_event.is_set():
            with self._lock:
                cams = list(self._cameras.values())

            for cam in cams:
                if cam.info.state == CameraState.STREAMING:
                    frame, ts = cam.get_latest_frame(mark_used=False)
                    if frame is not None and ts > 0:
                        self.buffer.push_frame(frame, ts, cam.info.id)

            time.sleep(interval)

    def get_camera(self, camera_id: Optional[str] = None) -> Optional[BaseCamera]:
        """Get camera instance by ID or fallback to primary camera."""
        with self._lock:
            if not self._cameras:
                return None
            target_id = camera_id or self.config.primary_camera
            if target_id in self._cameras:
                return self._cameras[target_id]
            # Fallback to first available
            first_key = next(iter(self._cameras))
            return self._cameras[first_key]

    def list_cameras_info(self) -> List[CameraInfo]:
        """List metadata for all known cameras."""
        with self._lock:
            return [cam.info for cam in self._cameras.values()]

    def get_frame(
        self, camera_id: Optional[str] = None
    ) -> Tuple[Optional[np.ndarray], float, Optional[str]]:
        """Fetch latest frame from target camera. Returns (frame, timestamp, actual_camera_id)."""
        cam = self.get_camera(camera_id)
        if not cam:
            return None, 0.0, None
        frame, ts = cam.get_latest_frame(mark_used=True)
        return frame, ts, cam.info.id

    def set_camera_settings(self, settings: CameraSettings) -> CameraSettingsResult:
        """Apply hardware camera settings."""
        cam = self.get_camera(settings.camera)
        if not cam:
            return CameraSettingsResult(
                camera=settings.camera,
                applied={},
                ignored={"camera": f"Camera '{settings.camera}' not found"},
                driver_feedback="Camera not found",
            )
        if isinstance(cam, OpenCVCamera):
            return cam.apply_settings(settings)
        return CameraSettingsResult(
            camera=settings.camera,
            applied={},
            ignored={"all": "Settings negotiation only supported on USB/OpenCV cameras."},
            driver_feedback="Unsupported camera type",
        )

    def shutdown(self) -> None:
        """Stop buffer collector and all camera grabbers."""
        self._stop_event.set()
        if self._buffer_worker_thread and self._buffer_worker_thread.is_alive():
            self._buffer_worker_thread.join(timeout=2.0)

        with self._lock:
            for cam in self._cameras.values():
                try:
                    cam.stop()
                except Exception as e:
                    logger.debug("Error stopping camera %s: %s", cam.info.id, e)
            self._cameras.clear()
