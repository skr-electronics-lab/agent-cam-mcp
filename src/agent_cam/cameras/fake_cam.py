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

        # Base dark workbench background (RGB: ~24, 28, 36)
        frame = np.full((h, w, 3), (32, 28, 24), dtype=np.uint8)

        # Draw a grid pattern (workbench cutting mat)
        grid_step = 60
        for x in range(0, w, grid_step):
            cv2.line(frame, (x, 0), (x, h), (45, 40, 35), 1)
        for y in range(0, h, grid_step):
            cv2.line(frame, (0, y), (w, y), (45, 40, 35), 1)

        # Draw simulated PCB / Embedded board in center
        board_x1, board_y1 = int(w * 0.25), int(h * 0.25)
        board_x2, board_y2 = int(w * 0.75), int(h * 0.75)
        # Dark green PCB surface
        cv2.rectangle(frame, (board_x1, board_y1), (board_x2, board_y2), (20, 60, 20), -1)
        cv2.rectangle(frame, (board_x1, board_y1), (board_x2, board_y2), (40, 100, 40), 2)

        # PCB mounting holes
        cv2.circle(frame, (board_x1 + 20, board_y1 + 20), 8, (180, 180, 180), -1)
        cv2.circle(frame, (board_x2 - 20, board_y1 + 20), 8, (180, 180, 180), -1)
        cv2.circle(frame, (board_x1 + 20, board_y2 - 20), 8, (180, 180, 180), -1)
        cv2.circle(frame, (board_x2 - 20, board_y2 - 20), 8, (180, 180, 180), -1)

        # Simulated MCU chip (black square)
        chip_x1, chip_y1 = int(w * 0.42), int(h * 0.40)
        chip_x2, chip_y2 = int(w * 0.58), int(h * 0.60)
        cv2.rectangle(frame, (chip_x1, chip_y1), (chip_x2, chip_y2), (15, 15, 15), -1)
        cv2.rectangle(frame, (chip_x1, chip_y1), (chip_x2, chip_y2), (60, 60, 60), 1)
        cv2.putText(
            frame,
            "MCU-CORE",
            (chip_x1 + 10, chip_y1 + 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (160, 160, 160),
            1,
            cv2.LINE_AA,
        )

        # Simulated Status LED
        led_center = (board_x1 + 80, board_y1 + 80)
        is_lit = False
        if self.led_blink_hz > 0:
            phase = (elapsed * self.led_blink_hz * 2 * math.pi) % (2 * math.pi)
            is_lit = phase < math.pi
        else:
            is_lit = self.led_state

        if is_lit:
            # Bright cyan/blue or green LED
            cv2.circle(frame, led_center, 12, (255, 200, 50), -1)
            cv2.circle(frame, led_center, 18, (200, 150, 30), 2)
        else:
            # Off state (dark olive)
            cv2.circle(frame, led_center, 10, (30, 40, 20), -1)
            cv2.circle(frame, led_center, 12, (50, 60, 40), 1)

        cv2.putText(
            frame,
            "LED_STATUS",
            (led_center[0] - 35, led_center[1] + 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (120, 120, 120),
            1,
            cv2.LINE_AA,
        )

        # Simulated OLED Display
        disp_x1, disp_y1 = board_x2 - 200, board_y1 + 50
        disp_x2, disp_y2 = board_x2 - 40, board_y1 + 150
        cv2.rectangle(frame, (disp_x1, disp_y1), (disp_x2, disp_y2), (10, 10, 10), -1)
        cv2.rectangle(frame, (disp_x1, disp_y1), (disp_x2, disp_y2), (80, 80, 80), 1)
        if self.display_state:
            cv2.putText(
                frame,
                self.display_text,
                (disp_x1 + 10, disp_y1 + 40),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                frame,
                f"T: {elapsed:.1f}s",
                (disp_x1 + 10, disp_y1 + 75),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 200, 200),
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
