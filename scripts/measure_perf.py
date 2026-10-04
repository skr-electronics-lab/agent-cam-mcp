"""Benchmark script measuring real performance numbers for Agent Cam MCP."""

from __future__ import annotations

import asyncio
import statistics
import time

import numpy as np

from agent_cam.adapters.actions import ActionRunner
from agent_cam.analysis.baseline import BaselineManager
from agent_cam.analysis.metrics import calculate_metrics
from agent_cam.cameras.buffer import FrameBuffer
from agent_cam.cameras.manager import CameraManager
from agent_cam.config import AgentCamConfig
from agent_cam.mcp_server.tools import MCPToolExecutor
from agent_cam.policy import PolicyEngine


async def benchmark_perf():
    print("================ AGENT CAM PERFORMANCE BENCHMARK ================")

    # 1. Measure analysis benchmark (1080p frame)
    frame_1080p = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
    measure_latencies = []
    for _ in range(20):
        t0 = time.perf_counter()
        calculate_metrics(frame_1080p)
        t1 = time.perf_counter()
        measure_latencies.append((t1 - t0) * 1000.0)

    median_measure = statistics.median(measure_latencies)
    print(f"1. 'measure' tool on 1080p frame: median={median_measure:.2f} ms (target: <100 ms)")

    # 2. Camera Manager + Memory Frame Buffer capture latency
    cfg = AgentCamConfig(
        use_fake_camera=True, buffer_seconds=10, buffer_fps=4, rate_limit_images_per_10s=500
    )
    buf = FrameBuffer(buffer_seconds=10, buffer_fps=4)
    mgr = CameraManager(config=cfg, buffer=buf)
    mgr.initialize_cameras()

    policy = PolicyEngine(config=cfg)
    b_mgr = BaselineManager()
    act = ActionRunner(config=cfg)
    executor = MCPToolExecutor(
        config=cfg,
        camera_manager=mgr,
        policy_engine=policy,
        baseline_manager=b_mgr,
        action_runner=act,
        adapters={},
        start_time=time.time(),
    )

    # Warm up
    await executor.execute("capture_image", {"width": 1024})

    capture_latencies = []
    for _ in range(30):
        t0 = time.perf_counter()
        res = await executor.execute("capture_image", {"width": 1024})
        t1 = time.perf_counter()
        assert res.isError is False
        capture_latencies.append((t1 - t0) * 1000.0)

    median_capture = statistics.median(capture_latencies)
    p95_capture = sorted(capture_latencies)[int(0.95 * len(capture_latencies))]
    print(
        f"2. 'capture_image' (1024px, JPEG quality 80): median={median_capture:.2f} ms, p95={p95_capture:.2f} ms (target: <250 ms)"
    )

    # 3. Buffer memory cap
    buf_mem_mb = buf.get_memory_usage_mb()
    print(f"3. Buffer memory footprint: {buf_mem_mb:.2f} MB (cap: {cfg.max_buffer_memory_mb} MB)")

    mgr.shutdown()
    print("==================================================================")


if __name__ == "__main__":
    asyncio.run(benchmark_perf())
