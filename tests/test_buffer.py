"""Unit tests for RAM ring buffer, bounding, and compression."""

from __future__ import annotations

import time

import numpy as np

from agent_cam.cameras.buffer import FrameBuffer


def test_frame_buffer_push_and_get():
    buf = FrameBuffer(buffer_seconds=10, buffer_fps=10, max_memory_mb=50)
    frame = np.full((120, 160, 3), 128, dtype=np.uint8)

    t0 = time.time()
    for i in range(5):
        buf.push_frame(frame, t0 + (i * 0.15), camera_id="cam-0")

    frames = buf.get_frames(last_seconds=5.0)
    assert len(frames) >= 4
    dec = frames[0].decode()
    assert dec is not None
    assert dec.shape == (120, 160, 3)


def test_frame_buffer_memory_cap():
    # Very small 1MB memory cap
    buf = FrameBuffer(buffer_seconds=60, buffer_fps=30, max_memory_mb=1)
    frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

    t = time.time()
    for i in range(50):
        buf.push_frame(frame, t + (i * 0.05), camera_id="cam-0")

    # Ensure memory stays below or around cap
    assert buf.get_memory_usage_mb() <= 1.5


def test_frame_buffer_time_eviction():
    buf = FrameBuffer(buffer_seconds=2, buffer_fps=10, max_memory_mb=50)
    frame = np.zeros((60, 80, 3), dtype=np.uint8)

    t = time.time()
    buf.push_frame(frame, t - 10.0, camera_id="cam-0")  # Old frame
    buf.push_frame(frame, t, camera_id="cam-0")  # Recent frame

    frames = buf.get_frames(last_seconds=2.0)
    assert len(frames) == 1
