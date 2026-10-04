"""Base adapter interface and event line aggregator."""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from typing import Deque, List, Optional

from agent_cam.models import EventLine

logger = logging.getLogger(__name__)


class BaseAdapter(ABC):
    """Abstract interface for event streaming adapters."""

    def __init__(self, source_name: str, max_buffer_lines: int = 500) -> None:
        self.source_name = source_name
        self.max_buffer_lines = max_buffer_lines
        self._lock = threading.Lock()
        self._events: Deque[EventLine] = deque(maxlen=max_buffer_lines)
        self._is_running = False

    def push_line(self, text: str, timestamp: Optional[float] = None) -> None:
        """Add a timestamped event line to the ring buffer."""
        ev = EventLine(
            timestamp=timestamp or time.time(),
            source=self.source_name,
            text=text.strip(),
        )
        with self._lock:
            self._events.append(ev)

    def get_lines(
        self,
        last_seconds: Optional[float] = None,
        max_lines: Optional[int] = None,
    ) -> List[EventLine]:
        """Fetch buffered lines within time window or count limit."""
        with self._lock:
            items = list(self._events)

        if last_seconds is not None:
            cutoff = time.time() - last_seconds
            items = [ev for ev in items if ev.timestamp >= cutoff]

        if max_lines is not None and max_lines > 0:
            items = items[-max_lines:]

        return items

    @abstractmethod
    def start(self) -> bool:
        """Start the adapter."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the adapter."""
        pass

    @property
    def is_running(self) -> bool:
        return self._is_running
