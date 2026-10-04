"""Synthetic camera for CI, tests, and mock demonstrations."""

from __future__ import annotations

import math
import time
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.cameras.base import BaseCamera
from agent_cam.models import CameraInfo, CameraSourceType, CameraState, PrivacyMask


class FakeCamera(BaseCamera):
    """Synthetic test camera generating real rendered frames with controllable hardware elements."""

    def __init__(
        self,
        camera_id: str = "fake-0",
        name: str = "Synthetic Lab Camera",
        width: int = 1280,
        height: int = 720,
        fps: float = 30.0,
        idle_timeout_s: float = 30.0,
        privacy_masks_provider: Optional[Callable[[], List[PrivacyMask]]] = None,
    ) -> None:
        info = CameraInfo(
            id=camera_id,
            name=name,
            source_type=CameraSourceType.FAKE,
            source_uri="fake://synthetic-lab",
            state=CameraState.IDLE,
            width=width,
            height=height,
            fps=fps,
        )
        super().__init__(
            info, idle_timeout_s=idle_timeout_s, privacy_masks_provider=privacy_masks_provider
        )

        # Controllable synthetic state
        self.led_state: bool = False
        self.led_blink_hz: float = 1.0  # 1 Hz default blink
        self.display_state: bool = True
        self.display_text: str = "SYSTEM READY"
        self.is_unplugged: bool = False
        self._start_time = time.time()

    def set_led(self, state: bool, blink_hz: float = 0.0) -> None:
        """Set simulated LED state or blinking frequency."""
        self.led_state = state
        self.led_blink_hz = blink_hz

    def set_display(self, state: bool, text: str = "SYSTEM READY") -> None:
        """Set simulated OLED/LCD screen state and text."""
        self.display_state = state
        self.display_text = text

    def simulate_unplug(self) -> None:
        """Simulate physical camera disconnect."""
        self.is_unplugged = True

    def simulate_replug(self) -> None:
        """Simulate camera reconnect."""
        self.is_unplugged = False

    def _open_device(self) -> bool:
        if self.is_unplugged:
            return False
        return True

    def _close_device(self) -> None:
        pass

    def _read_device_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self.is_unplugged:
            return False, None

        now = time.time()
        elapsed = now - self._start_time
        w = self.info.width
        h = self.info.height

        # Base neutral optical calibration background (dark slate gray: 30, 32, 36)
        frame = np.full((h, w, 3), (36, 32, 30), dtype=np.uint8)

        # Draw optical alignment grid
        grid_step = 60
        for x in range(0, w, grid_step):
            cv2.line(frame, (x, 0), (x, h), (55, 50, 48), 1)
        for y in range(0, h, grid_step):
            cv2.line(frame, (0, y), (w, y), (55, 50, 48), 1)

        # Central target / reticle area
        cx, cy = w // 2, h // 2
        cv2.circle(frame, (cx, cy), 120, (70, 65, 60), 2)
        cv2.circle(frame, (cx, cy), 60, (90, 85, 80), 1)
        cv2.line(frame, (cx - 140, cy), (cx + 140, cy), (90, 85, 80), 1)
        cv2.line(frame, (cx, cy - 140), (cx, cy + 140), (90, 85, 80), 1)

        # Calibration color swatches along bottom
        swatch_y1, swatch_y2 = h - 90, h - 40
        colors = [
            (220, 50, 50),  # Blue
            (50, 200, 50),  # Green
            (50, 50, 220),  # Red
            (50, 220, 220),  # Yellow
            (220, 50, 220),  # Magenta
            (220, 220, 50),  # Cyan
            (230, 230, 230),  # White
            (40, 40, 40),  # Dark
        ]
        swatch_w = int(w * 0.7) // len(colors)
        start_x = int(w * 0.15)
        for i, col in enumerate(colors):
            x1 = start_x + i * swatch_w
            x2 = x1 + swatch_w - 4
            cv2.rectangle(frame, (x1, swatch_y1), (x2, swatch_y2), col, -1)
            cv2.rectangle(frame, (x1, swatch_y1), (x2, swatch_y2), (100, 100, 100), 1)

        # Controllable test indicator light
        indicator_center = (cx + 220, cy - 60)
        is_lit = False
        if self.led_blink_hz > 0:
            phase = (elapsed * self.led_blink_hz * 2 * math.pi) % (2 * math.pi)
            is_lit = phase < math.pi
        else:
            is_lit = self.led_state

        if is_lit:
            cv2.circle(frame, indicator_center, 16, (0, 220, 255), -1)
            cv2.circle(frame, indicator_center, 22, (0, 160, 200), 2)
        else:
            cv2.circle(frame, indicator_center, 14, (30, 50, 60), -1)
            cv2.circle(frame, indicator_center, 18, (60, 80, 90), 1)

        cv2.putText(
            frame,
            "INDICATOR",
            (indicator_center[0] - 32, indicator_center[1] + 36),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (150, 150, 150),
            1,
            cv2.LINE_AA,
        )

        # Configurable display text panel
        panel_x1, panel_y1 = cx - 280, cy + 30
        panel_x2, panel_y2 = cx - 80, cy + 110
        cv2.rectangle(frame, (panel_x1, panel_y1), (panel_x2, panel_y2), (20, 22, 26), -1)
        cv2.rectangle(frame, (panel_x1, panel_y1), (panel_x2, panel_y2), (70, 75, 85), 1)
        if self.display_state:
            cv2.putText(
                frame,
                self.display_text,
                (panel_x1 + 10, panel_y1 + 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (200, 220, 240),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                f"UPTIME: {elapsed:.1f}s",
                (panel_x1 + 10, panel_y1 + 65),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (140, 160, 180),
                1,
                cv2.LINE_AA,
            )

        # Moving element for motion testing (bouncing marker along the top banner)
        bounce_x = int((math.sin(elapsed * 2.0) * 0.5 + 0.5) * (w - 100)) + 50
        cv2.circle(frame, (bounce_x, 30), 8, (0, 180, 255), -1)

        # Top status bar
        cv2.putText(
            frame,
            f"AGENT-CAM SYNTHETIC CAMERA | TIME: {now:.3f} | FRAME: {self._frame_count}",
            (20, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

        return True, frame
