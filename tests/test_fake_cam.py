"""Unit tests for synthetic fake camera source and controls."""

from __future__ import annotations

import time

import numpy as np

from agent_cam.cameras.fake_cam import FakeCamera
from agent_cam.models import CameraState


def test_fake_camera_generation():
    cam = FakeCamera(camera_id="fake-0", width=640, height=480, fps=30.0)
    frame, ts = cam.get_latest_frame()
    assert frame is not None
    assert frame.shape == (480, 640, 3)
    assert ts > 0
    assert cam.info.state == CameraState.STREAMING
    cam.stop()


def test_fake_camera_led_and_display_controls():
    cam = FakeCamera(camera_id="fake-0", width=640, height=480)
    cam.start()

    # Test LED ON
    cam.set_led(state=True, blink_hz=0.0)
    time.sleep(0.06)
    f_on, _ = cam.get_latest_frame()

    # Test LED OFF
    cam.set_led(state=False, blink_hz=0.0)
    time.sleep(0.06)
    f_off, _ = cam.get_latest_frame()

    assert f_on is not None and f_off is not None
    # Pixels around the LED should differ
    diff = float(np.mean(np.abs(f_on.astype(int) - f_off.astype(int))))
    assert diff > 0.0

    cam.stop()


def test_fake_camera_unplug_replug():
    cam = FakeCamera(camera_id="fake-0", width=640, height=480)
    ret, frame = cam._read_device_frame()
    assert ret is True
    assert frame is not None

    cam.simulate_unplug()
    ret_unplug, frame_unplug = cam._read_device_frame()
    assert ret_unplug is False
    assert frame_unplug is None

    cam.simulate_replug()
    ret_replug, frame_replug = cam._read_device_frame()
    assert ret_replug is True
    assert frame_replug is not None
