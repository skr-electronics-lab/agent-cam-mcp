"""Region coordinate conversion, cropping, perspective warping, and visual annotations."""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.models import Region, RegionUnit


def order_quad_points(pts: np.ndarray) -> np.ndarray:
    """Sort 4 coordinates in consistent order: top-left, top-right, bottom-right, bottom-left."""
    rect = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def crop_region(
    frame: np.ndarray,
    region: Region,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """Crop or perspective-warp frame to the specified region.

    Returns (cropped_or_warped_frame, (x1, y1, x2, y2)).
    If region has 4 points defined, applies cv2 perspective warp to rectify the surface.
    """
    h, w = frame.shape[:2]

    # Check for 4-point quadrilateral perspective warp
    if region.points and len(region.points) == 4:
        raw_pts = []
        for p in region.points:
            if region.units == RegionUnit.NORMALIZED:
                px = max(0.0, min(float(w - 1), p[0] * w))
                py = max(0.0, min(float(h - 1), p[1] * h))
            else:
                px = max(0.0, min(float(w - 1), float(p[0])))
                py = max(0.0, min(float(h - 1), float(p[1])))
            raw_pts.append([px, py])

        pts = np.array(raw_pts, dtype="float32")
        rect = order_quad_points(pts)
        (tl, tr, br, bl) = rect

        width_a = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        width_b = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        max_w = max(int(width_a), int(width_b), 4)

        height_a = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        height_b = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        max_h = max(int(height_a), int(height_b), 4)

        dst = np.array(
            [
                [0, 0],
                [max_w - 1, 0],
                [max_w - 1, max_h - 1],
                [0, max_h - 1],
            ],
            dtype="float32",
        )

        matrix = cv2.getPerspectiveTransform(rect, dst)
        warped = cv2.warpPerspective(frame, matrix, (max_w, max_h))

        x1 = max(0, min(w - 1, int(np.min(pts[:, 0]))))
        y1 = max(0, min(h - 1, int(np.min(pts[:, 1]))))
        x2 = max(x1 + 1, min(w, int(np.max(pts[:, 0]))))
        y2 = max(y1 + 1, min(h, int(np.max(pts[:, 1]))))
        return warped, (x1, y1, x2, y2)

    # Standard rectangular bounding box
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
    """Draw bounding boxes, polygons, and labels for regions onto a copy of the frame."""
    out = frame.copy()
    h, w = out.shape[:2]

    for reg in regions:
        is_highlight = highlight_name is not None and reg.name == highlight_name
        box_color = (0, 215, 255) if is_highlight else (255, 140, 0)  # BGR
        thickness = 2 if is_highlight else 1

        if reg.points and len(reg.points) == 4:
            px_pts = []
            for p in reg.points:
                if reg.units == RegionUnit.NORMALIZED:
                    px_pts.append([int(p[0] * w), int(p[1] * h)])
                else:
                    px_pts.append([int(p[0]), int(p[1])])
            pts_arr = np.array(px_pts, dtype=np.int32).reshape((-1, 1, 2))
            cv2.polylines(out, [pts_arr], isClosed=True, color=box_color, thickness=thickness)

            # Draw corner vertex pins
            for pt in px_pts:
                cv2.circle(out, tuple(pt), 3, (0, 255, 255), -1)

            x1, y1 = px_pts[0][0], px_pts[0][1]
        else:
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
