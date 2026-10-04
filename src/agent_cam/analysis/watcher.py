"""Watch loop evaluating conditions across camera frames with timeouts."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional, Tuple

import numpy as np

from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.models import WatchCondition, WatchResult

logger = logging.getLogger(__name__)


async def watch_condition(
    frame_getter: Callable[[], Tuple[Optional[np.ndarray], float]],
    condition: WatchCondition,
    threshold: Optional[float] = None,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.1,
) -> Tuple[WatchResult, Optional[np.ndarray], Optional[np.ndarray]]:
    """Watch camera frames asynchronously until the specified condition is met or timeout expires."""
    max_timeout = min(120.0, max(1.0, timeout_s))
    start_time = time.time()
    deadline = start_time + max_timeout

    # Grab initial baseline frame
    initial_frame, initial_ts = frame_getter()
    while initial_frame is None and time.time() < deadline:
        await asyncio.sleep(poll_interval_s)
        initial_frame, initial_ts = frame_getter()

    if initial_frame is None:
        return (
            WatchResult(
                fired=False,
                condition=condition.value,
                elapsed_seconds=time.time() - start_time,
                message="No frames received from camera before timeout",
                before_timestamp=start_time,
                after_timestamp=time.time(),
            ),
            None,
            None,
        )

    before_frame = initial_frame.copy()
    before_ts = initial_ts
    prev_frame = initial_frame.copy()
    initial_metrics = calculate_metrics(initial_frame)

    # Set default thresholds if not specified
    if condition == WatchCondition.CHANGE:
        thresh = threshold if threshold is not None else 0.08  # 8% pixel motion
    elif condition == WatchCondition.MOTION_START:
        thresh = threshold if threshold is not None else 0.05  # 5% motion
    elif condition == WatchCondition.MOTION_STOP:
        thresh = threshold if threshold is not None else 0.01  # < 1% motion
    elif condition == WatchCondition.BRIGHTNESS_ABOVE:
        thresh = threshold if threshold is not None else (initial_metrics.mean_brightness + 30.0)
    elif condition == WatchCondition.BRIGHTNESS_BELOW:
        thresh = (
            threshold if threshold is not None else max(0.0, initial_metrics.mean_brightness - 30.0)
        )
    elif condition == WatchCondition.COLOR_PRESENT:
        thresh = threshold if threshold is not None else 50.0
    else:
        thresh = threshold or 0.0

    last_observed_value: Optional[float] = None

    while time.time() < deadline:
        await asyncio.sleep(poll_interval_s)
        curr_frame, curr_ts = frame_getter()
        if curr_frame is None:
            continue

        metrics = calculate_metrics(curr_frame, previous_frame=prev_frame)
        prev_frame = curr_frame.copy()

        fired = False
        message = ""

        if condition == WatchCondition.CHANGE:
            # Change vs initial frame
            initial_diff_metrics = calculate_metrics(curr_frame, previous_frame=initial_frame)
            last_observed_value = initial_diff_metrics.motion_level
            if initial_diff_metrics.motion_level >= thresh:
                fired = True
                message = f"Change detected: motion level {initial_diff_metrics.motion_level:.3f} >= {thresh:.3f}"

        elif condition == WatchCondition.MOTION_START:
            last_observed_value = metrics.motion_level
            if metrics.motion_level >= thresh:
                fired = True
                message = f"Motion started: {metrics.motion_level:.3f} >= {thresh:.3f}"

        elif condition == WatchCondition.MOTION_STOP:
            last_observed_value = metrics.motion_level
            if metrics.motion_level <= thresh:
                fired = True
                message = f"Motion stopped: {metrics.motion_level:.3f} <= {thresh:.3f}"

        elif condition == WatchCondition.BRIGHTNESS_ABOVE:
            last_observed_value = metrics.mean_brightness
            if metrics.mean_brightness >= thresh:
                fired = True
                message = (
                    f"Brightness {metrics.mean_brightness:.1f} rose above threshold {thresh:.1f}"
                )

        elif condition == WatchCondition.BRIGHTNESS_BELOW:
            last_observed_value = metrics.mean_brightness
            if metrics.mean_brightness <= thresh:
                fired = True
                message = (
                    f"Brightness {metrics.mean_brightness:.1f} fell below threshold {thresh:.1f}"
                )

        elif condition == WatchCondition.COLOR_PRESENT:
            # Check lit pixels or color dominance
            last_observed_value = metrics.lit_pixel_percentage
            if metrics.lit_pixel_percentage >= thresh:
                fired = True
                message = (
                    f"Color/lit saturation {metrics.lit_pixel_percentage:.1f}% >= {thresh:.1f}%"
                )

        if fired:
            elapsed = time.time() - start_time
            return (
                WatchResult(
                    fired=True,
                    condition=condition.value,
                    elapsed_seconds=round(elapsed, 3),
                    message=message,
                    before_timestamp=before_ts,
                    after_timestamp=curr_ts,
                    threshold_value=thresh,
                    detected_value=last_observed_value,
                ),
                before_frame,
                curr_frame,
            )

    # Timed out
    elapsed = time.time() - start_time
    return (
        WatchResult(
            fired=False,
            condition=condition.value,
            elapsed_seconds=round(elapsed, 3),
            message=f"Timeout reached ({max_timeout:.1f}s) without condition firing",
            before_timestamp=before_ts,
            after_timestamp=time.time(),
            threshold_value=thresh,
            detected_value=last_observed_value,
        ),
        before_frame,
        prev_frame,
    )
