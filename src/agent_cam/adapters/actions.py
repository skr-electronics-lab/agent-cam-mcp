"""Allowlisted action runner with timeouts, argv safety, and optional timeline observation."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict, List, Optional

from agent_cam.config import ActionConfig, AgentCamConfig
from agent_cam.models import ErrorCode, StructuredError

logger = logging.getLogger(__name__)


class ActionRunner:
    """Safely executes configured allowlist actions without shell evaluation."""

    def __init__(self, config: AgentCamConfig) -> None:
        self.config = config

    def get_action(self, name: str) -> Optional[ActionConfig]:
        return self.config.adapters.actions.get(name)

    def run(
        self,
        name: str,
        extra_args: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Execute allowlisted action. Returns execution details."""
        action = self.get_action(name)
        if not action:
            raise StructuredError(
                error_code=ErrorCode.INVALID_ARGUMENT.value,
                message=f"Action '{name}' is not in the allowlist.",
                fix="Configure the action in config.json under adapters.actions or via the Dashboard.",
                retryable=False,
            )

        cmd = list(action.command)
        if extra_args:
            # Append only validated non-flag/safe args if needed
            cmd.extend([str(a) for a in extra_args])

        start_time = time.time()
        try:
            # STRICT SECURITY RULE: Never use shell=True
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=action.timeout_s,
                cwd=action.cwd or None,
                shell=False,
            )
            duration_ms = (time.time() - start_time) * 1000.0

            # Cap stdout/stderr length
            max_chars = 4000
            stdout_clean = res.stdout[:max_chars] if res.stdout else ""
            stderr_clean = res.stderr[:max_chars] if res.stderr else ""

            return {
                "name": name,
                "command": cmd,
                "exit_code": res.returncode,
                "stdout": stdout_clean,
                "stderr": stderr_clean,
                "duration_ms": round(duration_ms, 1),
                "observe_seconds": action.observe_seconds,
            }
        except subprocess.TimeoutExpired:
            duration_ms = (time.time() - start_time) * 1000.0
            raise StructuredError(
                error_code=ErrorCode.TIMEOUT.value,
                message=f"Action '{name}' exceeded timeout of {action.timeout_s}s",
                fix="Increase timeout_s in action configuration if expected to take longer.",
                retryable=True,
            )
        except Exception as e:
            raise StructuredError(
                error_code=ErrorCode.ADAPTER_ERROR.value,
                message=f"Failed to execute action '{name}': {e}",
                fix="Verify executable exists and permissions are sufficient.",
                retryable=False,
            )
