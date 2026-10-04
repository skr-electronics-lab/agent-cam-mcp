"""Soak test running continuous randomized tool calls asserting bounded memory and zero crashes."""

from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import pytest

from agent_cam.adapters.actions import ActionRunner
from agent_cam.analysis.baseline import BaselineManager
from agent_cam.cameras.buffer import FrameBuffer
from agent_cam.cameras.manager import CameraManager
from agent_cam.config import AgentCamConfig
from agent_cam.mcp_server.tools import MCPToolExecutor
from agent_cam.policy import PolicyEngine


@pytest.mark.asyncio
async def test_soak_run(tmp_path: Path):
    """Run simulated soak session with high-frequency calls, asserting memory bounds and stability."""
    cfg = AgentCamConfig(
        use_fake_camera=True,
        buffer_seconds=5,
        buffer_fps=4,
        max_buffer_memory_mb=100,
        rate_limit_images_per_10s=500,
    )
    buf = FrameBuffer(buffer_seconds=5, buffer_fps=4, max_memory_mb=100)
    mgr = CameraManager(config=cfg, buffer=buf)
    mgr.initialize_cameras()
    policy = PolicyEngine(config=cfg)
    b_mgr = BaselineManager(storage_dir=tmp_path / "baselines")
    act_runner = ActionRunner(config=cfg)

    executor = MCPToolExecutor(
        config=cfg,
        camera_manager=mgr,
        policy_engine=policy,
        baseline_manager=b_mgr,
        action_runner=act_runner,
        adapters={},
        start_time=time.time(),
    )

    tools = ["get_status", "capture_image", "measure", "list_cameras", "get_timeline"]

    # Initial warm-up
    await executor.execute("capture_image", {"width": 640})

    # Run 60 randomized tool calls
    for i in range(60):
        t = random.choice(tools)
        if t == "capture_image":
            res = await executor.execute(t, {"width": 640})
        elif t == "get_timeline":
            res = await executor.execute(t, {"last_seconds": 3})
        else:
            res = await executor.execute(t, {})
        assert res.isError is False
        await asyncio.sleep(0.01)

    final_buffer_mb = buf.get_memory_usage_mb()
    # Memory must stay tightly bounded by configured buffer cap
    assert final_buffer_mb <= 100.0

    mgr.shutdown()
