"""Quantitative image measurement metrics: brightness, contrast, lit pixels, edge density, sharpness, and motion."""

from __future__ import annotations

import time
from typing import List, Optional

import cv2
import numpy as np

from agent_cam.models import MeasureResult


def calculate_metrics(
    frame: np.ndarray,
    previous_frame: Optional[np.ndarray] = None,
    lit_threshold: int = 180,
) -> MeasureResult:
    """Compute deterministic quantitative measurements on a frame."""
    h, w = frame.shape[:2]
    now = time.time()

    # Convert to grayscale
    if len(frame.shape) == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame

    # 1. Mean brightness (0 - 255)
    mean_brightness = float(np.mean(gray))

    # 2. Contrast (standard deviation of grayscale values)
    contrast = float(np.std(gray))

    # 3. Lit pixel percentage (% of pixels above lit_threshold)
    lit_count = int(np.count_nonzero(gray >= lit_threshold))
    total_pixels = max(1, h * w)
    lit_pixel_pct = float((lit_count / total_pixels) * 100.0)

    # 4. Dominant colors (using downsampled k-means or histogram in RGB space)
    dominant_colors: List[List[int]] = []
    if len(frame.shape) == 3:
        # Downsample for sub-10ms performance
        small = cv2.resize(frame, (64, 64), interpolation=cv2.INTER_AREA)
        rgb_small = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        pixels = rgb_small.reshape(-1, 3).astype(np.float32)

        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        k = 3
        try:
            _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
            # Sort centers by frequency
            counts = np.bincount(labels.flatten())
            sorted_indices = np.argsort(-counts)
            for idx in sorted_indices:
                color = centers[idx].astype(int).tolist()
                dominant_colors.append(color)
        except Exception:
            dominant_colors = [[int(mean_brightness)] * 3]
    else:
        dominant_colors = [[int(mean_brightness)] * 3]

    # 5. Edge density (% of edge pixels using Canny)
    edges = cv2.Canny(gray, 50, 150)
    edge_count = int(np.count_nonzero(edges))
    edge_density = float((edge_count / total_pixels) * 100.0)

    # 6. Sharpness (Laplacian variance - standard focus metric)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    sharpness = float(laplacian.var())

    # 7. Motion level vs previous frame (0.0 to 1.0)
    motion_level = 0.0
    if previous_frame is not None:
        if previous_frame.shape[:2] != (h, w):
            prev_resized = cv2.resize(previous_frame, (w, h))
        else:
            prev_resized = previous_frame

        if len(prev_resized.shape) == 3:
            prev_gray = cv2.cvtColor(prev_resized, cv2.COLOR_BGR2GRAY)
        else:
            prev_gray = prev_resized

        diff = cv2.absdiff(gray, prev_gray)
        motion_level = float(np.mean(diff) / 255.0)

    return MeasureResult(
        mean_brightness=round(mean_brightness, 2),
        contrast=round(contrast, 2),
        lit_pixel_percentage=round(lit_pixel_pct, 2),
        dominant_colors=dominant_colors,
        edge_density=round(edge_density, 2),
        sharpness=round(sharpness, 2),
        motion_level=round(motion_level, 4),
        width=w,
        height=h,
        timestamp=now,
    )
