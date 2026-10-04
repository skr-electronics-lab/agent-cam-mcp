"""In-memory RAM ring buffer storing JPEG-compressed frames with bounded memory."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CompressedFrame:
    timestamp: float
    jpeg_bytes: bytes
    width: int
    height: int
    camera_id: str

    @property
    def size_bytes(self) -> int:
        return len(self.jpeg_bytes)

    def decode(self) -> np.ndarray:
        arr = np.frombuffer(self.jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return frame


class FrameBuffer:
    """Thread-safe ring buffer with low-fps JPEG compression and bounded memory."""

    def __init__(
        self,
        buffer_seconds: int = 30,
        buffer_fps: int = 4,
        max_memory_mb: int = 300,
        jpeg_quality: int = 75,
    ) -> None:
        self.buffer_seconds = buffer_seconds
        self.buffer_fps = buffer_fps
        self.max_memory_bytes = max_memory_mb * 1024 * 1024
        self.jpeg_quality = jpeg_quality

        self._lock = threading.Lock()
        self._max_frames = max(10, buffer_seconds * buffer_fps)
        self._frames: deque[CompressedFrame] = deque(maxlen=self._max_frames * 2)
        self._current_memory_bytes: int = 0
        self._last_push_time: float = 0.0
        self._min_push_interval: float = 1.0 / max(1.0, float(buffer_fps))

    def push_frame(self, frame: np.ndarray, timestamp: float, camera_id: str) -> None:
        """Compress and push a frame if enough time has elapsed since the last push."""
        now = timestamp
        with self._lock:
            if (now - self._last_push_time) < (self._min_push_interval * 0.9):
                return
            self._last_push_time = now

        h, w = frame.shape[:2]
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality]
        ret, enc = cv2.imencode(".jpg", frame, encode_params)
        if not ret:
            return
        jpeg_bytes = enc.tobytes()

        item = CompressedFrame(
            timestamp=timestamp,
            jpeg_bytes=jpeg_bytes,
            width=w,
            height=h,
            camera_id=camera_id,
        )

        with self._lock:
            self._frames.append(item)
            self._current_memory_bytes += item.size_bytes

            # Evict frames older than buffer_seconds
            cutoff = timestamp - self.buffer_seconds
            while self._frames and self._frames[0].timestamp < cutoff:
                removed = self._frames.popleft()
                self._current_memory_bytes -= removed.size_bytes

            # Enforce max memory cap
            while self._frames and self._current_memory_bytes > self.max_memory_bytes:
                removed = self._frames.popleft()
                self._current_memory_bytes -= removed.size_bytes

    def get_frames(
        self,
        last_seconds: float = 30.0,
        camera_id: Optional[str] = None,
    ) -> List[CompressedFrame]:
        """Return frames within the requested duration."""
        now = time.time()
        cutoff = now - max(1.0, last_seconds)
        with self._lock:
            result = []
            for item in self._frames:
                if item.timestamp >= cutoff:
                    if camera_id is None or item.camera_id == camera_id:
                        result.append(item)
            return result

    def get_fill_seconds(self) -> float:
        """Calculate buffer fill in seconds."""
        with self._lock:
            if len(self._frames) < 2:
                return 0.0
            return max(0.0, self._frames[-1].timestamp - self._frames[0].timestamp)

    def get_memory_usage_mb(self) -> float:
        """Current buffer RAM usage in MB."""
        with self._lock:
            return self._current_memory_bytes / (1024 * 1024)

    def clear(self) -> None:
        """Clear the buffer."""
        with self._lock:
            self._frames.clear()
            self._current_memory_bytes = 0
            self._last_push_time = 0.0
