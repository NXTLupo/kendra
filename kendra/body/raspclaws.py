from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from ..config import Settings
from .base import BodyDriver


class RaspClawsDriver(BodyDriver):
    """Adapter to a locally verified Adeept bridge module.

    The bridge is intentionally not bundled because the exact RaspClaws-Metal
    servo mapping must be verified against the builder's current vendor package.
    """

    def __init__(self, settings: Settings):
        if settings.get("project.mode") != "hardware":
            raise RuntimeError("The raspclaws driver is available only in project.mode=hardware")
        settings.assert_hardware_gates()
        search_path = Path(str(settings.require("body.raspclaws.bridge_search_path"))).expanduser()
        if not search_path.is_absolute():
            search_path = (settings.root / search_path).resolve()
        module_name = str(settings.require("body.raspclaws.bridge_module"))
        if not search_path.exists():
            raise FileNotFoundError(f"Verified hardware bridge directory not found: {search_path}")
        sys.path.insert(0, str(search_path))
        try:
            self.bridge = importlib.import_module(module_name)
        finally:
            if sys.path and sys.path[0] == str(search_path):
                sys.path.pop(0)
        required = ("walk", "turn", "pose", "stop")
        missing = [name for name in required if not callable(getattr(self.bridge, name, None))]
        if missing:
            raise RuntimeError(f"Hardware bridge is missing required callables: {', '.join(missing)}")

    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]:
        return dict(self.bridge.walk(direction=direction, steps=steps, speed=speed) or {"ok": True})

    def turn(self, degrees: float, speed: float) -> dict[str, Any]:
        return dict(self.bridge.turn(degrees=degrees, speed=speed) or {"ok": True})

    def pose(self, name: str) -> dict[str, Any]:
        return dict(self.bridge.pose(name=name) or {"ok": True})

    def stop(self) -> dict[str, Any]:
        return dict(self.bridge.stop() or {"ok": True})

    def look(self, pan: float, tilt: float) -> dict[str, Any]:
        func = getattr(self.bridge, "look", None)
        if not callable(func):
            return super().look(pan, tilt)
        return dict(func(pan=pan, tilt=tilt) or {"ok": True})

    def front_distance_cm(self) -> float | None:
        func = getattr(self.bridge, "front_distance_cm", None)
        if not callable(func):
            return None
        value = func()
        return float(value) if value is not None else None

    def battery_voltage(self) -> float | None:
        func = getattr(self.bridge, "battery_voltage", None)
        if not callable(func):
            return None
        value = func()
        return float(value) if value is not None else None

    def close(self) -> None:
        func = getattr(self.bridge, "close", None)
        if callable(func):
            func()
