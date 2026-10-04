"""MQTT event adapter subscribing to broker topics."""

from __future__ import annotations

import logging
from typing import List, Optional

from agent_cam.adapters.base import BaseAdapter
from agent_cam.models import ErrorCode, StructuredError

logger = logging.getLogger(__name__)


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
