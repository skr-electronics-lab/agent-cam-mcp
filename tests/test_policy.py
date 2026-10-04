"""Unit tests for PolicyEngine: pause switch, rate limiting, approvals, audit logging."""

from __future__ import annotations

from agent_cam.config import AgentCamConfig
from agent_cam.models import ErrorCode
from agent_cam.policy import PolicyEngine


def test_policy_pause_toggle():
    cfg = AgentCamConfig()
    engine = PolicyEngine(config=cfg)

    assert engine.check_paused() is None

    engine.set_paused(True)
    err = engine.check_paused()
    assert err is not None
    assert err.error_code == ErrorCode.PAUSED_BY_USER.value

    engine.set_paused(False)
    assert engine.check_paused() is None


def test_policy_rate_limiter():
    cfg = AgentCamConfig(rate_limit_images_per_10s=3)
    engine = PolicyEngine(config=cfg)
    client = "test-agent"

    # First 3 should pass
    for _ in range(3):
        assert engine.check_rate_limit(client, is_image_call=True) is None

    # 4th should trigger rate limit
    err = engine.check_rate_limit(client, is_image_call=True)
    assert err is not None
    assert err.error_code == ErrorCode.RATE_LIMITED.value
    assert "retry_after_s" in err.details


def test_policy_approvals():
    cfg = AgentCamConfig()
    engine = PolicyEngine(config=cfg)

    req = engine.request_approval("reset_board", {"pin": 4}, "agent-1")
    assert req.status == "pending"

    pending = engine.list_pending_approvals()
    assert len(pending) == 1
    assert pending[0]["action_name"] == "reset_board"

    # Approve
    ok = engine.resolve_approval(req.id, approved=True)
    assert ok is True
    assert req.status == "approved"
    assert req.event.is_set()


def test_policy_activity_log():
    cfg = AgentCamConfig()
    engine = PolicyEngine(config=cfg)

    engine.log_activity("client-1", "measure", {}, 15.2, "success", result_summary="test summary")
    logs = engine.get_recent_activity(limit=10)
    assert len(logs) == 1
    assert logs[0]["tool"] == "measure"
    assert logs[0]["status"] == "success"
