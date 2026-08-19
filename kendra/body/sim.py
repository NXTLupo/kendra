from __future__ import annotations

import time
from typing import Any

from .base import BodyDriver


class SimulationBodyDriver(BodyDriver):
    def __init__(self):
        self.state = "ready"
        self.last_action: dict[str, Any] | None = None

    def _record(self, action: str, **kwargs: Any) -> dict[str, Any]:
        self.state = action
        self.last_action = {"action": action, **kwargs, "timestamp": time.time()}
        time.sleep(0.02)
        self.state = "ready"
        return {"ok": True, "simulated": True, **self.last_action}

    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]:
        return self._record("walk", direction=direction, steps=steps, speed=speed)

    def turn(self, degrees: float, speed: float) -> dict[str, Any]:
        return self._record("turn", degrees=degrees, speed=speed)

    def pose(self, name: str) -> dict[str, Any]:
        return self._record("pose", name=name)

    def stop(self) -> dict[str, Any]:
        self.state = "stopped"
        return self._record("stop")
