"""Cameras package for Agent Cam MCP."""

from agent_cam.cameras.base import BaseCamera, apply_privacy_masks, apply_transformations
from agent_cam.cameras.buffer import CompressedFrame, FrameBuffer
from agent_cam.cameras.fake_cam import FakeCamera
from agent_cam.cameras.ip_cam import IPCamera
from agent_cam.cameras.manager import CameraManager
from agent_cam.cameras.opencv_cam import OpenCVCamera, enumerate_usb_cameras

__all__ = [
    "BaseCamera",
    "CompressedFrame",
    "FrameBuffer",
    "FakeCamera",
    "IPCamera",
    "CameraManager",
    "OpenCVCamera",
    "enumerate_usb_cameras",
    "apply_privacy_masks",
    "apply_transformations",
]
