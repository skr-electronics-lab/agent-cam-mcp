"""Base camera class with background grabber thread, warm-up, and frame preprocessing."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.models import CameraInfo, CameraState, PrivacyMask, RegionUnit

logger = logging.getLogger(__name__)


def apply_transformations(
    frame: np.ndarray,
    rotation: int = 0,
    flip_h: bool = False,
    flip_v: bool = False,
) -> np.ndarray:
    """Apply rotation and flips."""
    out = frame
    if rotation == 90:
        out = cv2.rotate(out, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        out = cv2.rotate(out, cv2.ROTATE_180)
    elif rotation == 270:
        out = cv2.rotate(out, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if flip_h and flip_v:
        out = cv2.flip(out, -1)
    elif flip_h:
        out = cv2.flip(out, 1)
    elif flip_v:
        out = cv2.flip(out, 0)

    return out


def apply_privacy_masks(
    frame: np.ndarray,
    masks: List[PrivacyMask],
    camera_id: str,
) -> np.ndarray:
    """Black out privacy masks in-place/directly before frame dispatch."""
    if not masks:
        return frame
    h, w = frame.shape[:2]
    out = frame.copy()
    for mask in masks:
        if mask.camera and mask.camera != camera_id:
            continue
        if mask.units == RegionUnit.NORMALIZED:
            x1 = max(0, int(mask.x * w))
            y1 = max(0, int(mask.y * h))
            x2 = min(w, int((mask.x + mask.w) * w))
            y2 = min(h, int((mask.y + mask.h) * h))
        else:
            x1 = max(0, int(mask.x))
            y1 = max(0, int(mask.y))
            x2 = min(w, int(mask.x + mask.w))
            y2 = min(h, int(mask.y + mask.h))

        if x2 > x1 and y2 > y1:
            out[y1:y2, x1:x2] = 0
    return out


class BaseCamera(ABC):
    """Abstract base camera with asynchronous grabber thread and latest-frame slot."""

    def __init__(
        self,
        info: CameraInfo,
        idle_timeout_s: float = 30.0,
        privacy_masks_provider: Optional[Callable[[], List[PrivacyMask]]] = None,
    ) -> None:
        self.info = info
        self.idle_timeout_s = idle_timeout_s
        self.privacy_masks_provider = privacy_masks_provider

        self._lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_timestamp: float = 0.0
        self._frame_count: int = 0

        self._grabber_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._last_access_time: float = time.time()
        self._active_users: int = 0  # Previews or agents currently reading
        self._is_opened: bool = False

    @abstractmethod
    def _open_device(self) -> bool:
        """Open the physical device/stream. Return True if successful."""
        pass

    @abstractmethod
    def _read_device_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read a single raw frame from the device."""
        pass

    @abstractmethod
    def _close_device(self) -> None:
        """Close the device and release driver resources."""
        pass

    def start(self) -> bool:
        """Start the grabber thread if not running."""
        with self._lock:
            self._last_access_time = time.time()
            if self._grabber_thread and self._grabber_thread.is_alive():
                return True

            self._stop_event.clear()
            if not self._open_device():
                self.info.state = CameraState.ERROR
                return False

            self._is_opened = True
            self.info.state = CameraState.STREAMING

            # Discard warm-up frames
            for _ in range(8):
                ret, _ = self._read_device_frame()
                if not ret:
                    break
                time.sleep(0.01)

            self._grabber_thread = threading.Thread(
                target=self._grabber_loop,
                name=f"Grabber-{self.info.id}",
                daemon=True,
            )
            self._grabber_thread.start()
            return True

    def stop(self) -> None:
        """Stop grabber thread and release device."""
        self._stop_event.set()
        thread = self._grabber_thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._grabber_thread = None
            self._is_opened = False
            self._close_device()
            self.info.state = CameraState.IDLE

    def _grabber_loop(self) -> None:
        """Continuous background grabber loop."""
        consecutive_failures = 0
        target_fps = max(1.0, min(self.info.fps, 60.0))
        interval = 1.0 / target_fps

        while not self._stop_event.is_set():
            loop_start = time.time()

            # Check idle release timeout
            with self._lock:
                idle_duration = time.time() - self._last_access_time
                if self._active_users == 0 and idle_duration > self.idle_timeout_s:
                    logger.info(
                        "Camera %s idle for %.1fs. Releasing device.", self.info.id, idle_duration
                    )
                    break

            ret, raw_frame = self._read_device_frame()
            if ret and raw_frame is not None:
                consecutive_failures = 0
                now = time.time()

                # Apply transformations
                transformed = apply_transformations(
                    raw_frame,
                    rotation=self.info.rotation,
                    flip_h=self.info.flip_horizontal,
                    flip_v=self.info.flip_vertical,
                )

                # Apply privacy masks
                masks = self.privacy_masks_provider() if self.privacy_masks_provider else []
                processed = apply_privacy_masks(transformed, masks, self.info.id)

                with self._lock:
                    self._latest_frame = processed
                    self._latest_timestamp = now
                    self._frame_count += 1
                    self.info.state = CameraState.STREAMING
                    self.info.width = processed.shape[1]
                    self.info.height = processed.shape[0]
            else:
                consecutive_failures += 1
                if consecutive_failures > 15:
                    logger.warning(
                        "Camera %s grab failed %d times consecutively.",
                        self.info.id,
                        consecutive_failures,
                    )
                    with self._lock:
                        self.info.state = CameraState.DISCONNECTED
                    time.sleep(1.0)
                else:
                    time.sleep(0.05)

            elapsed = time.time() - loop_start
            sleep_time = interval - elapsed
            if sleep_time > 0.001:
                time.sleep(sleep_time)

        # Loop exited (idle or stopped)
        with self._lock:
            self._is_opened = False
            self._close_device()
            if self.info.state != CameraState.DISCONNECTED:
                self.info.state = CameraState.IDLE

    def get_latest_frame(self, mark_used: bool = True) -> Tuple[Optional[np.ndarray], float]:
        """Fetch latest processed frame from memory with timestamp. Under 10ms."""
        now = time.time()
        with self._lock:
            if mark_used:
                self._last_access_time = now

            if (
                not self._is_opened
                or self._grabber_thread is None
                or not self._grabber_thread.is_alive()
            ):
                # Lazy open on demand
                pass

        if not self._is_opened:
            if not self.start():
                return None, 0.0
            # Wait up to 1.5 seconds for first frame
            for _ in range(30):
                with self._lock:
                    if self._latest_frame is not None:
                        return self._latest_frame.copy(), self._latest_timestamp
                time.sleep(0.05)

        with self._lock:
            if self._latest_frame is not None:
                return self._latest_frame.copy(), self._latest_timestamp
            return None, 0.0

    def increment_active_user(self) -> None:
        """Register an active preview or consumer to prevent idle release."""
        with self._lock:
            self._active_users += 1
            self._last_access_time = time.time()
        if not self._is_opened:
            self.start()

    def decrement_active_user(self) -> None:
        """Deregister active preview or consumer."""
        with self._lock:
            self._active_users = max(0, self._active_users - 1)
            self._last_access_time = time.time()
