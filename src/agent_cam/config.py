"""Configuration management using platformdirs and pydantic."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

from platformdirs import PlatformDirs
from pydantic import BaseModel, Field

from agent_cam.models import PrivacyMask, Region

logger = logging.getLogger(__name__)

APP_NAME = "agent-cam-mcp"
APP_AUTHOR = "agent-cam"

dirs = PlatformDirs(appname=APP_NAME, appauthor=APP_AUTHOR)


def get_data_dir() -> Path:
    data_path = Path(dirs.user_data_dir)
    data_path.mkdir(parents=True, exist_ok=True)
    return data_path


def get_config_dir() -> Path:
    cfg_path = Path(dirs.user_config_dir)
    cfg_path.mkdir(parents=True, exist_ok=True)
    return cfg_path


def get_token_file() -> Path:
    return get_data_dir() / "auth_token.txt"


def get_lock_file() -> Path:
    return get_data_dir() / "daemon.lock"


def get_baselines_dir() -> Path:
    p = get_data_dir() / "baselines"
    p.mkdir(parents=True, exist_ok=True)
    return p


class ActionConfig(BaseModel):
    name: str
    command: List[str]  # argv list, never shell
    timeout_s: float = 30.0
    requires_approval: bool = True
    cwd: Optional[str] = None
    observe_seconds: float = 0.0


class SerialAdapterConfig(BaseModel):
    enabled: bool = False
    port: str = "COM1"
    baudrate: int = 115200
    timeout_s: float = 1.0


class LogFileAdapterConfig(BaseModel):
    enabled: bool = False
    file_path: str = ""


class MqttAdapterConfig(BaseModel):
    enabled: bool = False
    broker: str = "127.0.0.1"
    port: int = 1883
    topics: List[str] = Field(default_factory=lambda: ["#"])
    client_id: str = "agent-cam-subscriber"


class AdaptersConfig(BaseModel):
    serial: SerialAdapterConfig = Field(default_factory=SerialAdapterConfig)
    logfile: LogFileAdapterConfig = Field(default_factory=LogFileAdapterConfig)
    mqtt: MqttAdapterConfig = Field(default_factory=MqttAdapterConfig)
    actions: Dict[str, ActionConfig] = Field(default_factory=dict)


class IPCameraConfig(BaseModel):
    id: str
    name: str
    url: str
    fps: float = 15.0
    rotation: int = 0
    flip_h: bool = False
    flip_v: bool = False


class AgentCamConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765
    primary_camera: str = "0"
    idle_release_seconds: float = 30.0
    buffer_seconds: int = 30
    buffer_fps: int = 4
    max_buffer_memory_mb: int = 300
    default_image_width: int = 1024
    jpeg_quality: int = 80
    rate_limit_images_per_10s: int = 10
    approval_mode: Literal["off", "actions_only", "every_capture"] = "actions_only"
    log_level: str = "INFO"
    theme: Literal["system", "dark", "light"] = "system"
    use_fake_camera: bool = False
    regions: Dict[str, Region] = Field(default_factory=dict)
    privacy_masks: List[PrivacyMask] = Field(default_factory=list)
    ip_cameras: List[IPCameraConfig] = Field(default_factory=list)
    adapters: AdaptersConfig = Field(default_factory=AdaptersConfig)

    def save(self, path: Optional[Path] = None) -> None:
        target = path or (get_config_dir() / "config.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_file = target.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(self.model_dump(), f, indent=2)
        temp_file.replace(target)
        try:
            # User-only permission where supported
            os.chmod(target, 0o600)
        except Exception:
            pass

    @classmethod
    def load(cls, path: Optional[Path] = None) -> AgentCamConfig:
        target = path or (get_config_dir() / "config.json")
        if target.exists():
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls.model_validate(data)
            except Exception as e:
                logger.warning("Failed to parse config file %s: %s. Using defaults.", target, e)
        cfg = cls()
        cfg.save(target)
        return cfg
