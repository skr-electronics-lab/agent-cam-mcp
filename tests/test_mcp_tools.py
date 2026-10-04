"""Integration tests for all 17 MCP tools."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from agent_cam.adapters.actions import ActionRunner
from agent_cam.analysis.baseline import BaselineManager
from agent_cam.cameras.buffer import FrameBuffer
from agent_cam.cameras.manager import CameraManager
from agent_cam.config import ActionConfig, AgentCamConfig
from agent_cam.mcp_server.tools import MCPToolExecutor
from agent_cam.policy import PolicyEngine


@pytest.fixture
def executor(tmp_path: Path):
    cfg = AgentCamConfig(use_fake_camera=True, buffer_seconds=10, buffer_fps=4)
    cfg.adapters.actions["test_echo"] = ActionConfig(
        name="test_echo",
        command=["python", "-c", "print('ACTION_OUTPUT_SUCCESS')"],
        timeout_s=5.0,
        requires_approval=False,
    )
    buf = FrameBuffer(buffer_seconds=10, buffer_fps=4, max_memory_mb=50)
    mgr = CameraManager(config=cfg, buffer=buf)
    mgr.initialize_cameras()
    policy = PolicyEngine(config=cfg)
    baseline_mgr = BaselineManager(storage_dir=tmp_path / "baselines")
    action_runner = ActionRunner(config=cfg)

    exec_inst = MCPToolExecutor(
        config=cfg,
        camera_manager=mgr,
        policy_engine=policy,
        baseline_manager=baseline_mgr,
        action_runner=action_runner,
        adapters={},
        start_time=time.time(),
    )
    yield exec_inst
    mgr.shutdown()


@pytest.mark.asyncio
async def test_mcp_get_status(executor: MCPToolExecutor):
    res = await executor.execute("get_status", {})
    assert res.isError is False
    data = json.loads(res.content[0].text)
    assert data["version"] == "0.1.0"
    assert "cameras" in data


@pytest.mark.asyncio
async def test_mcp_list_cameras(executor: MCPToolExecutor):
    res = await executor.execute("list_cameras", {})
    assert res.isError is False
    data = json.loads(res.content[0].text)
    assert len(data["cameras"]) >= 1


@pytest.mark.asyncio
async def test_mcp_capture_image(executor: MCPToolExecutor):
    res = await executor.execute("capture_image", {"width": 640})
    assert res.isError is False
    assert len(res.content) == 2
    # Check image content
    img_content = res.content[1]
    assert img_content.type == "image"
    raw = base64.b64decode(img_content.data)
    decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape[1] == 640


@pytest.mark.asyncio
async def test_mcp_measure(executor: MCPToolExecutor):
    res = await executor.execute("measure", {})
    assert res.isError is False
    data = json.loads(res.content[0].text)
    assert "mean_brightness" in data
    assert "contrast" in data
    assert "sharpness" in data


@pytest.mark.asyncio
async def test_mcp_regions_crud(executor: MCPToolExecutor):
    # 1. Define region
    def_res = await executor.execute(
        "define_region", {"name": "target_led", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}
    )
    assert def_res.isError is False

    # 2. List regions
    list_res = await executor.execute("list_regions", {})
    data = json.loads(list_res.content[0].text)
    assert "target_led" in data["regions"]

    # 3. Delete region
    del_res = await executor.execute("delete_region", {"name": "target_led"})
    assert del_res.isError is False


@pytest.mark.asyncio
async def test_mcp_baseline_and_compare(executor: MCPToolExecutor):
    # Save baseline
    save_res = await executor.execute("save_baseline", {"name": "golden_ref"})
    assert save_res.isError is False

    # Compare
    comp_res = await executor.execute("compare_to_baseline", {"name": "golden_ref"})
    assert comp_res.isError is False
    data = json.loads(comp_res.content[0].text)
    assert data["similarity_score"] >= 0.8


@pytest.mark.asyncio
async def test_mcp_capture_sequence(executor: MCPToolExecutor):
    res = await executor.execute("capture_sequence", {"seconds": 2, "fps": 2})
    assert res.isError is False
    assert len(res.content) == 2


@pytest.mark.asyncio
async def test_mcp_watch_condition(executor: MCPToolExecutor):
    res = await executor.execute(
        "watch", {"condition": "change", "timeout_s": 1.0, "threshold": 0.01}
    )
    assert res.isError is False
    data = json.loads(res.content[0].text)
    assert "fired" in data


@pytest.mark.asyncio
async def test_mcp_run_action(executor: MCPToolExecutor):
    res = await executor.execute("run_action", {"name": "test_echo"})
    assert res.isError is False
    data = json.loads(res.content[0].text)
    assert "ACTION_OUTPUT_SUCCESS" in data.get("stdout", "")


@pytest.mark.asyncio
async def test_mcp_error_paused(executor: MCPToolExecutor):
    executor.policy_engine.set_paused(True)
    res = await executor.execute("capture_image", {})
    assert res.isError is True
    err = json.loads(res.content[0].text)
    assert err["error_code"] == "paused_by_user"
