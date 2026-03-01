"""
Serial reader — reads real sensor data from an Arduino/ESP over USB serial.

Expected JSON format from the microcontroller:
  {"status": "OK", "temp1": 23.5, "temp2": 41.2, "hum": 55.0}

Maps hardware fields to our sensor names:
  temp2 → temperature  (on-chip / ambient temperature)
  hum   → humidity

Runs in a daemon thread. If the serial port is unavailable or disconnected,
it retries every few seconds without crashing the app.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Map Arduino JSON keys → our sensor names
_FIELD_MAP: dict[str, str] = {
    "temp2": "temperature",
    "hum":   "humidity",
}


class SerialReader:
    """Background thread that reads JSON lines from a serial port."""

    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 9600,
        retry_interval: float = 5.0,
    ) -> None:
        self._port = port
        self._baudrate = baudrate
        self._retry_interval = retry_interval
        self._latest: dict[str, float] = {}  # sensor_name → value
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def get_readings(self) -> dict[str, float]:
        """Return the latest readings {sensor_name: value}. Thread-safe."""
        with self._lock:
            return dict(self._latest)

    def start(self) -> None:
        """Start the background reader thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info(f"Serial reader started (port={self._port}, baud={self._baudrate})")

    def stop(self) -> None:
        """Stop the background reader."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        import serial  # import here so app works even without pyserial

        while self._running:
            try:
                with serial.Serial(self._port, self._baudrate, timeout=2) as ser:
                    logger.info(f"Serial connected: {self._port}")
                    self._connected = True
                    time.sleep(2)  # Arduino resets on connect

                    while self._running:
                        if ser.in_waiting > 0:
                            try:
                                raw = ser.readline().decode().strip()
                                data = json.loads(raw)
                                with self._lock:
                                    for hw_key, sensor_name in _FIELD_MAP.items():
                                        if hw_key in data:
                                            self._latest[sensor_name] = float(data[hw_key])
                            except (json.JSONDecodeError, KeyError, UnicodeDecodeError, ValueError):
                                continue
                        else:
                            time.sleep(0.05)

            except Exception as e:
                self._connected = False
                logger.debug(f"Serial not available ({self._port}): {e}")
                time.sleep(self._retry_interval)
