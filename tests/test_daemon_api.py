"""Integration tests for Daemon REST API and security headers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_cam.config import AgentCamConfig
from agent_cam.daemon import DaemonServer


@pytest.fixture
def client(tmp_path):
    cfg = AgentCamConfig(use_fake_camera=True)
    server = DaemonServer(config=cfg, host="127.0.0.1", port=8765)
    with TestClient(server.app) as c:
        yield c, server


def test_daemon_health(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_daemon_security_origin_validation(client):
    c, _ = client
    # Untrusted external origin (e.g. malicious website)
    resp = c.get("/health", headers={"origin": "http://evil-site.com"})
    assert resp.status_code == 403


def test_daemon_security_host_validation(client):
    c, _ = client
    # Malicious host header (DNS rebinding)
    resp = c.get("/health", headers={"host": "attacker.com"})
    assert resp.status_code == 403


def test_daemon_pause_toggle(client):
    c, server = client
    token = server.auth_token

    # Unauthorized without token
    unauth = c.post("/api/pause", json={"paused": True})
    assert unauth.status_code == 401

    # Authorized with token
    auth = c.post("/api/pause", json={"paused": True}, headers={"x-auth-token": token})
    assert auth.status_code == 200
    assert auth.json()["paused"] is True


def test_daemon_mcp_call_tool(client):
    c, _ = client
    payload = {"name": "get_status", "arguments": {}}
    resp = c.post("/api/mcp/call_tool", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["isError"] is False
    assert len(data["content"]) >= 1
