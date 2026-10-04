"""LED state, blink frequency, duty cycle, and color analysis."""

from __future__ import annotations

import time
from typing import List, Optional, Tuple

import cv2
import numpy as np

from agent_cam.models import LEDAnalysisResult


def classify_led_color(rgb: List[int]) -> str:
    """Classify RGB color into high-level LED color category."""
    r, g, b = rgb
    max_c = max(r, g, b)
    if max_c < 30:
        return "off"

    # Convert RGB to HSV
    bgr_pixel = np.uint8([[[b, g, r]]])
    hsv = cv2.cvtColor(bgr_pixel, cv2.COLOR_BGR2HSV)[0][0]
    h, s, v = int(hsv[0]), int(hsv[1]), int(hsv[2])

    if s < 35 and v > 150:
        return "white"

    if h <= 10 or h >= 170:
        return "red"
    elif 11 <= h <= 24:
        return "amber"
    elif 25 <= h <= 35:
        return "yellow"
    elif 36 <= h <= 85:
        return "green"
    elif 86 <= h <= 105:
        return "cyan"
    elif 106 <= h <= 135:
        return "blue"
    elif 136 <= h <= 169:
        return "purple"
    return "unknown"


def analyze_led_signal(
    samples: List[Tuple[float, np.ndarray]],
    camera_id: str,
    region_name: Optional[str] = None,
) -> LEDAnalysisResult:
    """Analyze time-series of camera frames covering an LED target."""
    if not samples:
        return LEDAnalysisResult(
            camera=camera_id,
            region=region_name,
            state="off",
            frequency_hz=0.0,
            duty_cycle=0.0,
            color_name="off",
            dominant_rgb=[0, 0, 0],
            mean_brightness=0.0,
            confidence=0.0,
            sample_count=0,
            duration_s=0.0,
            timestamp=time.time(),
        )

    timestamps = [s[0] for s in samples]
    frames = [s[1] for s in samples]
    duration_s = max(0.001, timestamps[-1] - timestamps[0])

    brightness_list: List[float] = []
    rgb_list: List[List[float]] = []

    for f in frames:
        if len(f.shape) == 3:
            gray = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            bgr_mean = cv2.mean(f)[:3]
            rgb_list.append([bgr_mean[2], bgr_mean[1], bgr_mean[0]])
        else:
            gray = f
            rgb_list.append([float(np.mean(gray))] * 3)
        brightness_list.append(float(np.mean(gray)))

    b_arr = np.array(brightness_list)
    b_min = float(np.min(b_arr))
    b_max = float(np.max(b_arr))
    b_range = b_max - b_min
    mean_b = float(np.mean(b_arr))

    # Average dominant color during brightest phase
    threshold = (b_max + b_min) / 2.0
    lit_indices = np.where(b_arr >= threshold)[0]
    if len(lit_indices) > 0:
        lit_rgb = [rgb_list[i] for i in lit_indices]
        avg_r = int(np.mean([c[0] for c in lit_rgb]))
        avg_g = int(np.mean([c[1] for c in lit_rgb]))
        avg_b = int(np.mean([c[2] for c in lit_rgb]))
    else:
        avg_r = int(np.mean([c[0] for c in rgb_list]))
        avg_g = int(np.mean([c[1] for c in rgb_list]))
        avg_b = int(np.mean([c[2] for c in rgb_list]))

    dominant_rgb = [avg_r, avg_g, avg_b]
    color_name = classify_led_color(dominant_rgb)

    # State classification
    if b_max < 25 or (color_name == "off" and mean_b < 30):
        return LEDAnalysisResult(
            camera=camera_id,
            region=region_name,
            state="off",
            frequency_hz=0.0,
            duty_cycle=0.0,
            color_name="off",
            dominant_rgb=dominant_rgb,
            mean_brightness=round(mean_b, 2),
            confidence=0.95,
            sample_count=len(samples),
            duration_s=round(duration_s, 3),
            timestamp=time.time(),
        )

    if b_range < 12.0:
        # Stable brightness -> solid on
        return LEDAnalysisResult(
            camera=camera_id,
            region=region_name,
            state="solid_on",
            frequency_hz=0.0,
            duty_cycle=100.0,
            color_name=color_name,
            dominant_rgb=dominant_rgb,
            mean_brightness=round(mean_b, 2),
            confidence=0.92,
            sample_count=len(samples),
            duration_s=round(duration_s, 3),
            timestamp=time.time(),
        )

    # Blinking analysis: binary transition count
    binary_signal = (b_arr >= threshold).astype(int)
    duty_cycle = round(float(np.mean(binary_signal) * 100.0), 1)

    diff = np.diff(binary_signal)
    rising_edges = int(np.count_nonzero(diff == 1))
    falling_edges = int(np.count_nonzero(diff == -1))
    cycles = max(rising_edges, falling_edges)

    frequency_hz = 0.0
    if duration_s > 0 and cycles >= 1:
        frequency_hz = round(float(cycles / duration_s), 2)

    return LEDAnalysisResult(
        camera=camera_id,
        region=region_name,
        state="blinking",
        frequency_hz=frequency_hz,
        duty_cycle=duty_cycle,
        color_name=color_name,
        dominant_rgb=dominant_rgb,
        mean_brightness=round(mean_b, 2),
        confidence=0.88,
        sample_count=len(samples),
        duration_s=round(duration_s, 3),
        timestamp=time.time(),
    )
