"""Physical USB camera capture using OpenCV with OS-specific backends and non-blocking enumeration."""

from __future__ import annotations

import logging
import platform
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.cameras.base import BaseCamera
from agent_cam.models import (
    CameraInfo,
    CameraSettings,
    CameraSettingsResult,
    CameraSourceType,
    CameraState,
    PrivacyMask,
)

logger = logging.getLogger(__name__)


def get_default_backend() -> int:
    """Return appropriate OpenCV capture backend for the current OS."""
    system = platform.system()
    if system == "Windows":
        return cv2.CAP_DSHOW
    elif system == "Linux":
        return cv2.CAP_V4L2
    elif system == "Darwin":
        return cv2.CAP_AVFOUNDATION
    return cv2.CAP_ANY


def get_windows_camera_names() -> Dict[int, str]:
    """Query Windows PNP camera friendly names via PowerShell without hanging."""
    names: Dict[int, str] = {}
    if platform.system() != "Windows":
        return names
    try:
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-PnpDevice -Class Camera,Image -Status OK -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FriendlyName",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3.0)
        if res.returncode == 0:
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            for idx, name in enumerate(lines):
                names[idx] = name
    except Exception as e:
        logger.debug("Failed to query Windows camera PNP names: %s", e)
    return names


def probe_single_camera_index(
    index: int, backend: int, timeout_s: float = 2.0
) -> Optional[CameraInfo]:
    """Test whether camera index opens, with a strict timeout thread to avoid driver hangs."""
    result: Dict[str, Any] = {"success": False, "width": 1280, "height": 720, "fps": 30.0}

    def _worker():
        cap = None
        try:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                # Probe for sensor native high resolution (up to 1080p FHD)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
                ret, frame = cap.read()
                if ret and frame is not None:
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or frame.shape[1]
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or frame.shape[0]
                    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                    result["success"] = True
                    result["width"] = w
                    result["height"] = h
                    result["fps"] = fps if (fps > 0 and fps <= 120) else 30.0
        except Exception:
            pass
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if result["success"]:
        return CameraInfo(
            id=str(index),
            name=f"Camera {index}",
            source_type=CameraSourceType.USB,
            source_uri=f"usb://{index}",
            state=CameraState.IDLE,
            width=result["width"],
            height=result["height"],
            fps=result["fps"],
        )
    return None


def enumerate_usb_cameras(max_indices: int = 4, timeout_per_probe: float = 1.5) -> List[CameraInfo]:
    """Enumerate USB webcams safely across platforms."""
    backend = get_default_backend()
    win_names = get_windows_camera_names()
    cameras: List[CameraInfo] = []

    for idx in range(max_indices):
        info = probe_single_camera_index(idx, backend, timeout_s=timeout_per_probe)
        if info:
            if idx in win_names:
                info.name = win_names[idx]
            cameras.append(info)
        elif idx == 0 and not cameras and len(win_names) > 0:
            # Device known in PNP but probe was slow or in use
            friendly = win_names.get(0, "USB Camera")
            cameras.append(
                CameraInfo(
                    id=str(idx),
                    name=friendly,
                    source_type=CameraSourceType.USB,
                    source_uri=f"usb://{idx}",
                    state=CameraState.IDLE,
                    width=1280,
                    height=720,
                    fps=30.0,
                )
            )

    return cameras


class OpenCVCamera(BaseCamera):
    """USB webcam using OpenCV with backend controls and settings negotiation."""

    def __init__(
        self,
        info: CameraInfo,
        device_index: int,
        idle_timeout_s: float = 30.0,
        privacy_masks_provider: Optional[Callable[[], List[PrivacyMask]]] = None,
    ) -> None:
        super().__init__(
            info, idle_timeout_s=idle_timeout_s, privacy_masks_provider=privacy_masks_provider
        )
        self.device_index = device_index
        self._cap: Optional[cv2.VideoCapture] = None
        self._backend = get_default_backend()

    def _open_device(self) -> bool:
        try:
            self._cap = cv2.VideoCapture(self.device_index, self._backend)
            if not self._cap or not self._cap.isOpened():
                self.info.state = CameraState.BUSY
                self.info.last_error = "camera_busy"
                self.info.last_error_fix = (
                    "Camera is in use by another application or disconnected."
                )
                return False

            # Request MJPG for lower USB bandwidth
            fourcc = cv2.VideoWriter_fourcc(*"MJPG")
            self._cap.set(cv2.CAP_PROP_FOURCC, fourcc)

            # Request resolution
            req_w = self.info.width if self.info.width > 640 else 1920
            req_h = self.info.height if self.info.height > 480 else 1080
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, req_w)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, req_h)
            if self.info.fps > 0:
                self._cap.set(cv2.CAP_PROP_FPS, self.info.fps)

            # Verify actual resolution
            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if actual_w > 0 and actual_h > 0:
                self.info.width = actual_w
                self.info.height = actual_h

            self.info.last_error = None
            self.info.last_error_fix = None
            return True
        except Exception as e:
            self.info.state = CameraState.ERROR
            self.info.last_error = str(e)
            self.info.last_error_fix = "Check device connection and driver permissions."
            return False

    def _read_device_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._cap is None or not self._cap.isOpened():
            return False, None
        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                return False, None
            return True, frame
        except Exception:
            return False, None

    def _close_device(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def apply_settings(self, settings: CameraSettings) -> CameraSettingsResult:
        """Apply hardware camera settings and report which were accepted or ignored."""
        applied: Dict[str, Any] = {}
        ignored: Dict[str, Any] = {}

        if self._cap is None or not self._cap.isOpened():
            return CameraSettingsResult(
                camera=self.info.id,
                applied=applied,
                ignored={"all": "Camera is not currently open/streaming."},
                driver_feedback="Device closed.",
            )

        if settings.exposure is not None:
            ret = self._cap.set(cv2.CAP_PROP_EXPOSURE, float(settings.exposure))
            if ret:
                applied["exposure"] = settings.exposure
            else:
                ignored["exposure"] = "Driver rejected CAP_PROP_EXPOSURE"

        if settings.focus is not None:
            ret = self._cap.set(cv2.CAP_PROP_FOCUS, float(settings.focus))
            if ret:
                applied["focus"] = settings.focus
            else:
                ignored["focus"] = "Driver rejected CAP_PROP_FOCUS"

        if settings.brightness is not None:
            ret = self._cap.set(cv2.CAP_PROP_BRIGHTNESS, float(settings.brightness))
            if ret:
                applied["brightness"] = settings.brightness
            else:
                ignored["brightness"] = "Driver rejected CAP_PROP_BRIGHTNESS"

        if settings.contrast is not None:
            ret = self._cap.set(cv2.CAP_PROP_CONTRAST, float(settings.contrast))
            if ret:
                applied["contrast"] = settings.contrast
            else:
                ignored["contrast"] = "Driver rejected CAP_PROP_CONTRAST"

        if settings.gain is not None:
            ret = self._cap.set(cv2.CAP_PROP_GAIN, float(settings.gain))
            if ret:
                applied["gain"] = settings.gain
            else:
                ignored["gain"] = "Driver rejected CAP_PROP_GAIN"

        if settings.lock_auto is not None:
            # 0.25 manual, 0.75 auto on DirectShow
            val = 0.25 if settings.lock_auto else 0.75
            ret = self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, val)
            if ret:
                applied["lock_auto"] = settings.lock_auto
            else:
                ignored["lock_auto"] = "Driver rejected CAP_PROP_AUTO_EXPOSURE"

        return CameraSettingsResult(
            camera=self.info.id,
            applied=applied,
            ignored=ignored,
            driver_feedback="Settings negotiation complete.",
        )
