"""Data models and schemas for Agent Cam MCP."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    NO_CAMERAS = "no_cameras"
    CAMERA_NOT_FOUND = "camera_not_found"
    CAMERA_BUSY = "camera_busy"
    CAMERA_DISCONNECTED = "camera_disconnected"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    REGION_NOT_FOUND = "region_not_found"
    BASELINE_NOT_FOUND = "baseline_not_found"
    PAUSED_BY_USER = "paused_by_user"
    APPROVAL_DENIED = "approval_denied"
    RATE_LIMITED = "rate_limited"
    NOT_INSTALLED = "not_installed"
    ADAPTER_ERROR = "adapter_error"
    INVALID_ARGUMENT = "invalid_argument"
    INTERNAL_ERROR = "internal_error"


class StructuredError(Exception):
    def __init__(
        self,
        error_code: str,
        message: str,
        fix: str,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.fix = fix
        self.retryable = retryable
        self.details = details

    def model_dump(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "fix": self.fix,
            "retryable": self.retryable,
        }
        if self.details:
            d["details"] = self.details
        return d

    def model_dump_json(self, indent: int = 2) -> str:
        import json

        return json.dumps(self.model_dump(), indent=indent)


class CameraState(str, Enum):
    IDLE = "idle"
    STREAMING = "streaming"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    BUSY = "busy"


class CameraSourceType(str, Enum):
    USB = "usb"
    IP = "ip"
    FAKE = "fake"


class CameraInfo(BaseModel):
    id: str
    name: str
    source_type: CameraSourceType
    source_uri: Optional[str] = None
    state: CameraState = CameraState.IDLE
    width: int = 1280
    height: int = 720
    fps: float = 30.0
    rotation: int = 0
    flip_horizontal: bool = False
    flip_vertical: bool = False
    last_error: Optional[str] = None
    last_error_fix: Optional[str] = None


class RegionUnit(str, Enum):
    NORMALIZED = "normalized"
    PIXELS = "pixels"


class Region(BaseModel):
    name: str
    x: float
    y: float
    w: float
    h: float
    camera: Optional[str] = None
    units: RegionUnit = RegionUnit.NORMALIZED


class PrivacyMask(BaseModel):
    id: str
    camera: Optional[str] = None
    x: float
    y: float
    w: float
    h: float
    units: RegionUnit = RegionUnit.NORMALIZED


class CameraSettings(BaseModel):
    camera: str
    exposure: Optional[int] = None
    focus: Optional[int] = None
    brightness: Optional[int] = None
    white_balance: Optional[int] = None
    lock_auto: Optional[bool] = None


class CameraSettingsResult(BaseModel):
    camera: str
    applied: Dict[str, Any]
    ignored: Dict[str, Any]
    driver_feedback: Optional[str] = None


class MeasureResult(BaseModel):
    mean_brightness: float
    contrast: float
    lit_pixel_percentage: float
    dominant_colors: List[List[int]]
    edge_density: float
    sharpness: float
    motion_level: float
    width: int
    height: int
    timestamp: float


class WatchCondition(str, Enum):
    CHANGE = "change"
    MOTION_START = "motion_start"
    MOTION_STOP = "motion_stop"
    BRIGHTNESS_ABOVE = "brightness_above"
    BRIGHTNESS_BELOW = "brightness_below"
    COLOR_PRESENT = "color_present"


class WatchResult(BaseModel):
    fired: bool
    condition: str
    elapsed_seconds: float
    message: str
    before_timestamp: float
    after_timestamp: float
    threshold_value: Optional[float] = None
    detected_value: Optional[float] = None


class BaselineInfo(BaseModel):
    name: str
    camera: str
    region: Optional[str] = None
    created_at: float
    width: int
    height: int


class ComparisonResult(BaseModel):
    name: str
    similarity_score: float  # 0.0 to 1.0 (SSIM or normalized similarity)
    percent_changed_pixels: float
    is_match: bool
    threshold: float
    timestamp: float


class EventLine(BaseModel):
    timestamp: float
    source: str
    text: str


class DaemonStatus(BaseModel):
    version: str
    uptime_seconds: float
    paused_by_user: bool
    active_agent_connections: int
    buffer_fill_seconds: float
    buffer_memory_mb: float
    cameras: List[CameraInfo]
    adapters: Dict[str, bool]
    warnings: List[str] = Field(default_factory=list)
