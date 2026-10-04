"""Policy enforcement: agent pause switch, rate limiting, approval queue, and activity audit log."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from agent_cam.config import AgentCamConfig
from agent_cam.models import ErrorCode, StructuredError

logger = logging.getLogger(__name__)


@dataclass
class ActivityRecord:
    id: str
    timestamp: float
    client: str
    tool: str
    parameters: Dict[str, Any]
    duration_ms: float
    status: str  # "success", "error", "rate_limited", "paused"
    error_code: Optional[str] = None
    image_preview_jpeg: Optional[bytes] = None
    result_summary: Optional[str] = None


@dataclass
class PendingApproval:
    id: str
    action_name: str
    args: Dict[str, Any]
    requested_at: float
    client: str
    status: str = "pending"  # "pending", "approved", "denied"
    event: threading.Event = field(default_factory=threading.Event)


class PolicyEngine:
    """Enforces access control, pauses, rate limits, action approvals, and audit trails."""

    def __init__(self, config: AgentCamConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self.is_paused: bool = False

        # Rate limiting: client_id -> deque of timestamps
        self._client_call_timestamps: Dict[str, Deque[float]] = {}

        # Approvals
        self._pending_approvals: Dict[str, PendingApproval] = {}

        # Activity log (in-memory ring buffer)
        self._activity_log: Deque[ActivityRecord] = deque(maxlen=200)

        # Active agent sessions count
        self._active_sessions: Dict[str, float] = {}

    def set_paused(self, paused: bool) -> None:
        """Pause or resume agent camera access."""
        with self._lock:
            self.is_paused = paused
            logger.info("Agent camera access paused=%s", paused)

    def check_paused(self) -> Optional[StructuredError]:
        """Check if agent access is currently paused."""
        with self._lock:
            if self.is_paused:
                return StructuredError(
                    error_code=ErrorCode.PAUSED_BY_USER.value,
                    message="Agent camera access is currently paused by the user.",
                    fix="Toggle 'Pause agent access' off in the Agent Cam dashboard.",
                    retryable=True,
                )
        return None

    def record_agent_heartbeat(self, client_id: str) -> None:
        """Track active agent connections."""
        now = time.time()
        with self._lock:
            self._active_sessions[client_id] = now
            # Prune sessions older than 60s
            cutoff = now - 60.0
            self._active_sessions = {k: v for k, v in self._active_sessions.items() if v > cutoff}

    def get_active_agent_count(self) -> int:
        now = time.time()
        with self._lock:
            cutoff = now - 60.0
            return sum(1 for v in self._active_sessions.values() if v > cutoff)

    def check_rate_limit(
        self, client_id: str, is_image_call: bool = True
    ) -> Optional[StructuredError]:
        """Check whether client has exceeded the 10 images / 10s rate limit."""
        if not is_image_call:
            return None

        now = time.time()
        window_seconds = 10.0
        max_calls = self.config.rate_limit_images_per_10s

        with self._lock:
            if client_id not in self._client_call_timestamps:
                self._client_call_timestamps[client_id] = deque()

            calls = self._client_call_timestamps[client_id]
            cutoff = now - window_seconds
            while calls and calls[0] < cutoff:
                calls.popleft()

            if len(calls) >= max_calls:
                oldest = calls[0]
                retry_after = max(0.5, round((oldest + window_seconds) - now, 2))
                return StructuredError(
                    error_code=ErrorCode.RATE_LIMITED.value,
                    message=f"Capture rate limit exceeded ({max_calls} images per 10s).",
                    fix=f"Please wait {retry_after}s before requesting another image.",
                    retryable=True,
                    details={"retry_after_s": retry_after},
                )

            calls.append(now)
            return None

    def log_activity(
        self,
        client: str,
        tool: str,
        parameters: Dict[str, Any],
        duration_ms: float,
        status: str,
        error_code: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        result_summary: Optional[str] = None,
    ) -> None:
        """Record an agent tool call in the audit trail."""
        rec = ActivityRecord(
            id=str(uuid.uuid4())[:8],
            timestamp=time.time(),
            client=client,
            tool=tool,
            parameters=parameters,
            duration_ms=round(duration_ms, 1),
            status=status,
            error_code=error_code,
            image_preview_jpeg=image_bytes,
            result_summary=result_summary,
        )
        with self._lock:
            self._activity_log.append(rec)

    def get_recent_activity(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return serialized recent activity logs for dashboard."""
        with self._lock:
            items = list(self._activity_log)[-limit:]
            items.reverse()
            out = []
            for item in items:
                out.append(
                    {
                        "id": item.id,
                        "timestamp": item.timestamp,
                        "client": item.client,
                        "tool": item.tool,
                        "parameters": item.parameters,
                        "duration_ms": item.duration_ms,
                        "status": item.status,
                        "error_code": item.error_code,
                        "has_image": item.image_preview_jpeg is not None,
                        "result_summary": item.result_summary,
                    }
                )
            return out

    def get_activity_image(self, record_id: str) -> Optional[bytes]:
        """Fetch image bytes associated with an activity record."""
        with self._lock:
            for item in self._activity_log:
                if item.id == record_id:
                    return item.image_preview_jpeg
        return None

    def request_approval(
        self, action_name: str, args: Dict[str, Any], client: str
    ) -> PendingApproval:
        """Create a pending action approval request."""
        approval_id = str(uuid.uuid4())[:8]
        req = PendingApproval(
            id=approval_id,
            action_name=action_name,
            args=args,
            requested_at=time.time(),
            client=client,
        )
        with self._lock:
            self._pending_approvals[approval_id] = req
        return req

    def list_pending_approvals(self) -> List[Dict[str, Any]]:
        """List active pending approvals for dashboard."""
        with self._lock:
            return [
                {
                    "id": req.id,
                    "action_name": req.action_name,
                    "args": req.args,
                    "requested_at": req.requested_at,
                    "client": req.client,
                    "status": req.status,
                }
                for req in self._pending_approvals.values()
                if req.status == "pending"
            ]

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """Approve or deny a pending request from dashboard."""
        with self._lock:
            req = self._pending_approvals.get(approval_id)
            if not req or req.status != "pending":
                return False
            req.status = "approved" if approved else "denied"
            req.event.set()
            return True
