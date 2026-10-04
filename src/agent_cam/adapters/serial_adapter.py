"""Serial port event adapter using pyserial with automatic reconnect and pulse support."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from agent_cam.adapters.base import BaseAdapter
from agent_cam.models import ErrorCode, StructuredError

logger = logging.getLogger(__name__)


class SerialAdapter(BaseAdapter):
    """Monitors serial COM port for ASCII log lines."""

    def __init__(self, port: str = "COM1", baudrate: int = 115200, timeout_s: float = 1.0) -> None:
        super().__init__(source_name=f"serial:{port}")
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._serial_handle = None

    def start(self) -> bool:
        import importlib.util

        if importlib.util.find_spec("serial") is None:
            raise StructuredError(
                error_code=ErrorCode.NOT_INSTALLED.value,
                message="pyserial is not installed.",
                fix="Install with: pip install 'agent-cam-mcp[serial]'",
                retryable=False,
            )

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop, name=f"Serial-{self.port}", daemon=True
        )
        self._thread.start()
        self._is_running = True
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._close_serial()
        self._is_running = False

    def _close_serial(self) -> None:
        if self._serial_handle is not None:
            try:
                self._serial_handle.close()
            except Exception:
                pass
            self._serial_handle = None

    def _worker_loop(self) -> None:
        import serial

        while not self._stop_event.is_set():
            if self._serial_handle is None:
                try:
                    self._serial_handle = serial.Serial(
                        port=self.port,
                        baudrate=self.baudrate,
                        timeout=self.timeout_s,
                    )
                    self.push_line(f"Connected to {self.port} at {self.baudrate} baud")
                except serial.SerialException as e:
                    err_msg = str(e)
                    if "PermissionError" in err_msg or "Access is denied" in err_msg:
                        fix_text = f"Port {self.port} is already open by another program (e.g. serial monitor). Only one process can open a serial port."
                    else:
                        fix_text = f"Ensure device is connected to {self.port}."
                    logger.warning("Serial connection error on %s: %s", self.port, fix_text)
                    time.sleep(2.0)
                    continue
                except Exception as e:
                    logger.warning("Failed opening serial port %s: %s", self.port, e)
                    time.sleep(2.0)
                    continue

            try:
                line_bytes = self._serial_handle.readline()
                if line_bytes:
                    text = line_bytes.decode("utf-8", errors="replace").strip()
                    if text:
                        self.push_line(text)
            except Exception as e:
                logger.debug("Serial read exception on %s: %s", self.port, e)
                self._close_serial()
                time.sleep(1.0)

        self._close_serial()

    def pulse_control_lines(
        self, dtr: bool = True, rts: bool = True, duration_s: float = 0.1
    ) -> bool:
        """Pulse DTR and/or RTS control lines (useful for microcontroller hardware reset)."""
        if self._serial_handle is None or not self._serial_handle.is_open:
            return False
        try:
            if dtr:
                self._serial_handle.dtr = False
            if rts:
                self._serial_handle.rts = True
            time.sleep(duration_s)
            if dtr:
                self._serial_handle.dtr = True
            if rts:
                self._serial_handle.rts = False
            self.push_line(f"Pulsed control lines (DTR={dtr}, RTS={rts}, duration={duration_s}s)")
            return True
        except Exception as e:
            logger.error("Failed pulsing serial lines: %s", e)
            return False
