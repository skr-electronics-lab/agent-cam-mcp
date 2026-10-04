"""IP Camera capture supporting RTSP and HTTP/MJPEG with auto-reconnect and exponential backoff."""

from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.cameras.base import BaseCamera
from agent_cam.models import CameraInfo, CameraState, PrivacyMask

logger = logging.getLogger(__name__)


class IPCamera(BaseCamera):
    """Network camera stream with exponential backoff reconnects."""

    def __init__(
        self,
        info: CameraInfo,
        url: str,
        idle_timeout_s: float = 30.0,
        privacy_masks_provider: Optional[Callable[[], List[PrivacyMask]]] = None,
    ) -> None:
        super().__init__(
            info, idle_timeout_s=idle_timeout_s, privacy_masks_provider=privacy_masks_provider
        )
        self.url = url
        self._cap: Optional[cv2.VideoCapture] = None
        self._backoff_delay: float = 1.0

    def _open_device(self) -> bool:
        try:
            self._cap = cv2.VideoCapture(self.url)
            if not self._cap or not self._cap.isOpened():
                self.info.state = CameraState.DISCONNECTED
                self.info.last_error = "connection_failed"
                self.info.last_error_fix = (
                    "Check RTSP/HTTP URL, network connectivity, and credentials."
                )
                return False

            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if actual_w > 0 and actual_h > 0:
                self.info.width = actual_w
                self.info.height = actual_h

            self.info.last_error = None
            self.info.last_error_fix = None
            self._backoff_delay = 1.0
            return True
        except Exception as e:
            self.info.state = CameraState.ERROR
            self.info.last_error = str(e)
            self.info.last_error_fix = "Verify stream URL and network connectivity."
            return False

    def _read_device_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._cap is None or not self._cap.isOpened():
            # Apply exponential backoff
            time.sleep(self._backoff_delay)
            self._backoff_delay = min(30.0, self._backoff_delay * 1.5)
            if self._open_device():
                self._backoff_delay = 1.0
            else:
                return False, None

        try:
            ret, frame = self._cap.read()
            if not ret or frame is None:
                self._close_device()
                return False, None
            return True, frame
        except Exception:
            self._close_device()
            return False, None

    def _close_device(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
