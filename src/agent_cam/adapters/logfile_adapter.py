"""Logfile and MQTT event adapters."""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional

from agent_cam.adapters.base import BaseAdapter
from agent_cam.models import ErrorCode, StructuredError

logger = logging.getLogger(__name__)


class LogFileAdapter(BaseAdapter):
    """Tails a local log file and pushes new lines into the event buffer."""

    def __init__(self, file_path: str) -> None:
        super().__init__(source_name=f"logfile:{Path(file_path).name}")
        self.file_path = file_path
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> bool:
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._tail_loop, name=f"Tail-{self.source_name}", daemon=True
        )
        self._thread.start()
        self._is_running = True
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._is_running = False

    def _tail_loop(self) -> None:
        p = Path(self.file_path)
        f = None

        while not self._stop_event.is_set():
            if f is None:
                if p.exists() and p.is_file():
                    try:
                        f = open(p, "r", encoding="utf-8", errors="replace")
                        f.seek(0, os.SEEK_END)
                    except Exception as e:
                        logger.debug("Failed opening tail file: %s", e)
                        time.sleep(1.0)
                        continue
                else:
                    time.sleep(1.0)
                    continue

            try:
                line = f.readline()
                if line:
                    self.push_line(line.strip())
                else:
                    time.sleep(0.1)
            except Exception:
                try:
                    f.close()
                except Exception:
                    pass
                f = None
                time.sleep(1.0)

        if f is not None:
            try:
                f.close()
            except Exception:
                pass


class MqttAdapter(BaseAdapter):
    """Subscribes to MQTT topics and records messages as event lines."""

    def __init__(
        self,
        broker: str = "127.0.0.1",
        port: int = 1883,
        topics: Optional[List[str]] = None,
        client_id: str = "agent-cam-mqtt",
    ) -> None:
        super().__init__(source_name=f"mqtt:{broker}")
        self.broker = broker
        self.port = port
        self.topics = topics or ["#"]
        self.client_id = client_id
        self._client = None

    def start(self) -> bool:
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            raise StructuredError(
                error_code=ErrorCode.NOT_INSTALLED.value,
                message="paho-mqtt is not installed.",
                fix="Install with: pip install 'agent-cam-mcp[mqtt]'",
                retryable=False,
            )

        try:
            # Handle paho-mqtt v1 and v2 CallbackAPIVersion
            try:
                self._client = mqtt.Client(
                    callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                    client_id=self.client_id,
                )
            except AttributeError:
                self._client = mqtt.Client(client_id=self.client_id)

            def on_connect(client, userdata, flags, rc, properties=None):
                for topic in self.topics:
                    client.subscribe(topic)
                self.push_line(f"Connected to MQTT broker {self.broker}:{self.port}")

            def on_message(client, userdata, msg):
                try:
                    payload = msg.payload.decode("utf-8", errors="replace")
                    self.push_line(f"[{msg.topic}] {payload}")
                except Exception:
                    pass

            self._client.on_connect = on_connect
            self._client.on_message = on_message
            self._client.connect_async(self.broker, self.port, 60)
            self._client.loop_start()
            self._is_running = True
            return True
        except Exception as e:
            logger.error("Failed connecting to MQTT broker: %s", e)
            return False

    def stop(self) -> None:
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._is_running = False
