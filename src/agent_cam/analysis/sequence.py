"""Contact sheet generation and sequence layout with timestamp and event overlays."""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.models import EventLine


def create_contact_sheet(
    frames_with_ts: List[Tuple[np.ndarray, float]],
    event_lines: Optional[List[EventLine]] = None,
    max_sheet_width: int = 1920,
    jpeg_quality: int = 80,
) -> np.ndarray:
    """Combine multiple timestamped frames into a single contact sheet grid."""
    if not frames_with_ts:
        blank = np.zeros((200, 400, 3), dtype=np.uint8)
        cv2.putText(
            blank,
            "NO FRAMES IN RANGE",
            (50, 100),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            1,
        )
        return blank

    n = len(frames_with_ts)
    cols = math.ceil(math.sqrt(n))
    if cols < 2 and n > 1:
        cols = 2
    rows = math.ceil(n / cols)

    # Determine frame cell dimensions
    first_frame = frames_with_ts[0][0]
    orig_h, orig_w = first_frame.shape[:2]

    # Calculate target cell width to stay within max_sheet_width
    cell_w = min(orig_w, max(240, max_sheet_width // cols))
    scale = cell_w / orig_w
    cell_h = int(orig_h * scale)

    # Event banner height if event lines present
    banner_height = 0
    if event_lines:
        banner_height = min(200, max(40, len(event_lines) * 22 + 20))

    grid_w = cols * cell_w
    grid_h = (rows * cell_h) + banner_height

    sheet = np.full((grid_h, grid_w, 3), (20, 22, 26), dtype=np.uint8)

    # Place frames
    for idx, (frame, ts) in enumerate(frames_with_ts):
        r = idx // cols
        c = idx % cols
        x1 = c * cell_w
        y1 = r * cell_h
        x2 = x1 + cell_w
        y2 = y1 + cell_h

        resized = cv2.resize(frame, (cell_w, cell_h), interpolation=cv2.INTER_AREA)

        # Draw 1px border around each cell
        cv2.rectangle(resized, (0, 0), (cell_w - 1, cell_h - 1), (50, 56, 68), 1)

        # Timestamp badge (bottom-left of cell)
        time_str = f"T+{ts:.2f}s" if ts < 1000000 else time_to_stamp(ts)
        cv2.putText(
            resized,
            time_str,
            (8, cell_h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            resized,
            time_str,
            (8, cell_h - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 240, 255),
            1,
            cv2.LINE_AA,
        )

        sheet[y1:y2, x1:x2] = resized

    # Render event lines if present
    if event_lines and banner_height > 0:
        event_y_start = rows * cell_h
        cv2.rectangle(sheet, (0, event_y_start), (grid_w, grid_h), (12, 14, 18), -1)
        cv2.line(sheet, (0, event_y_start), (grid_w, event_y_start), (60, 68, 80), 1)

        cv2.putText(
            sheet,
            "TIMELINE EVENTS:",
            (12, event_y_start + 18),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 190, 205),
            1,
            cv2.LINE_AA,
        )

        line_y = event_y_start + 38
        for ev in event_lines[:7]:
            ev_str = f"[{ev.source}] {ev.text}"
            cv2.putText(
                sheet,
                ev_str[:120],
                (20, line_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (220, 225, 235),
                1,
                cv2.LINE_AA,
            )
            line_y += 20
            if line_y > grid_h - 8:
                break

    return sheet


def time_to_stamp(ts: float) -> str:
    """Format unix timestamp as HH:MM:SS.mmm."""
    import datetime

    dt = datetime.datetime.fromtimestamp(ts)
    return dt.strftime("%H:%M:%S.%f")[:-3]
