"""Region coordinate conversion, cropping, and visual bounding box annotations."""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.models import Region, RegionUnit


def crop_region(
    frame: np.ndarray,
    region: Region,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """Crop frame to the specified region. Returns (cropped_frame, (x1, y1, x2, y2))."""
    h, w = frame.shape[:2]
    if region.units == RegionUnit.NORMALIZED:
        x1 = max(0, min(w - 1, int(region.x * w)))
        y1 = max(0, min(h - 1, int(region.y * h)))
        x2 = max(x1 + 1, min(w, int((region.x + region.w) * w)))
        y2 = max(y1 + 1, min(h, int((region.y + region.h) * h)))
    else:
        x1 = max(0, min(w - 1, int(region.x)))
        y1 = max(0, min(h - 1, int(region.y)))
        x2 = max(x1 + 1, min(w, int(region.x + region.w)))
        y2 = max(y1 + 1, min(h, int(region.y + region.h)))

    cropped = frame[y1:y2, x1:x2].copy()
    return cropped, (x1, y1, x2, y2)


def annotate_regions(
    frame: np.ndarray,
    regions: List[Region],
    highlight_name: Optional[str] = None,
) -> np.ndarray:
    """Draw bounding boxes and labels for regions onto a copy of the frame."""
    out = frame.copy()
    h, w = out.shape[:2]

    for reg in regions:
        if reg.units == RegionUnit.NORMALIZED:
            x1 = max(0, min(w - 1, int(reg.x * w)))
            y1 = max(0, min(h - 1, int(reg.y * h)))
            x2 = max(x1 + 1, min(w, int((reg.x + reg.w) * w)))
            y2 = max(y1 + 1, min(h, int((reg.y + reg.h) * h)))
        else:
            x1 = max(0, min(w - 1, int(reg.x)))
            y1 = max(0, min(h - 1, int(reg.y)))
            x2 = max(x1 + 1, min(w, int(reg.x + reg.w)))
            y2 = max(y1 + 1, min(h, int(reg.y + reg.h)))

        is_highlight = highlight_name is not None and reg.name == highlight_name
        box_color = (0, 215, 255) if is_highlight else (255, 140, 0)  # BGR
        thickness = 2 if is_highlight else 1

        cv2.rectangle(out, (x1, y1), (x2, y2), box_color, thickness)

        # Label tag
        label = f"[{reg.name}]"
        (lw, lh), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        tag_y1 = max(0, y1 - lh - 4)
        tag_y2 = y1
        cv2.rectangle(out, (x1, tag_y1), (x1 + lw + 6, tag_y2), box_color, -1)
        cv2.putText(
            out,
            label,
            (x1 + 3, tag_y2 - 3),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 0),
            1,
            cv2.LINE_AA,
        )

    return out
