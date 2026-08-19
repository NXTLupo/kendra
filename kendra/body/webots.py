from __future__ import annotations

import json
import socket
import threading
import uuid
from typing import Any

from ..config import Settings
from ..connectivity import assert_loopback_host
from .base import BodyDriver


class WebotsBridgeClient:
    """Small JSON-lines TCP client for the Kendra Webots controller.

    The bridge deliberately uses a tiny protocol so the real application never
    imports Webots' Python bindings. That keeps the same Kendra services usable
    on macOS, Linux, and the Raspberry Pi.
    """

    def __init__(self, settings: Settings):
        self.host = str(settings.get("body.webots.host", "127.0.0.1"))
        assert_loopback_host(self.host)
        self.port = int(settings.get("body.webots.port", 8765))
        self.timeout = float(settings.get("body.webots.connect_timeout_seconds", 2.0))
        self._lock = threading.Lock()

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "method": method, "params": params or {}}
        with self._lock:
            with socket.create_connection((self.host, self.port), timeout=timeout or self.timeout) as sock:
                sock.settimeout(timeout or max(self.timeout, 10.0))
                stream = sock.makefile("rwb", buffering=0)
                stream.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
                line = stream.readline()
                if not line:
                    raise ConnectionError("Webots bridge closed the connection without a response")
        response = json.loads(line.decode("utf-8"))
        if response.get("id") != request_id:
            raise RuntimeError("Webots bridge returned a mismatched request id")
        if not response.get("ok", False):
            raise RuntimeError(str(response.get("error") or "Webots bridge command failed"))
        return response.get("result")

    def telemetry(self) -> dict[str, Any]:
        result = self.call("telemetry", timeout=1.0)
        if not isinstance(result, dict):
            raise RuntimeError("Webots bridge telemetry response was not an object")
        return result


class WebotsBodyDriver(BodyDriver):
    def __init__(self, settings: Settings):
        self.client = WebotsBridgeClient(settings)
        # Fail early with a useful error if the digital twin is not running.
        self.client.call("health", timeout=1.0)

    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]:
        return dict(self.client.call("walk", {"direction": direction, "steps": steps, "speed": speed}, timeout=30.0))

    def turn(self, degrees: float, speed: float) -> dict[str, Any]:
        return dict(self.client.call("turn", {"degrees": degrees, "speed": speed}, timeout=30.0))

    def pose(self, name: str) -> dict[str, Any]:
        return dict(self.client.call("pose", {"name": name}, timeout=15.0))

    def look(self, pan: float, tilt: float) -> dict[str, Any]:
        return dict(self.client.call("look", {"pan": pan, "tilt": tilt}, timeout=10.0))

    def stop(self) -> dict[str, Any]:
        return dict(self.client.call("stop", {}, timeout=2.0))

    def front_distance_cm(self) -> float | None:
        value = self.client.telemetry().get("front_cm")
        return None if value is None else float(value)

    def battery_voltage(self) -> float | None:
        value = self.client.telemetry().get("battery_voltage")
        return None if value is None else float(value)
