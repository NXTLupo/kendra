from __future__ import annotations

import asyncio
import importlib
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ..config import Settings
from ..ipc import UnixJsonServer

ExpressiveState = Literal["warm", "concern", "alert", "neutral"]


@dataclass(slots=True)
class SystemLightState:
    reflex_fault: bool = False
    critical_battery: bool = False
    updating: bool = False
    charging: bool = False
    low_battery: bool = False
    offline: bool = False
    thinking: bool = False
    expression: ExpressiveState = "neutral"


def resolve_light(state: SystemLightState) -> dict[str, str]:
    if state.reflex_fault:
        return {"pattern": "solid", "semantic": "red", "reason": "reflex_fault"}
    if state.critical_battery:
        return {"pattern": "fast_blink", "semantic": "red", "reason": "critical_battery"}
    if state.updating:
        return {"pattern": "chase", "semantic": "white", "reason": "updating"}
    if state.charging:
        return {"pattern": "breathe", "semantic": "amber", "reason": "charging"}
    if state.low_battery:
        return {"pattern": "blink", "semantic": "amber", "reason": "low_battery"}
    if state.offline:
        return {"pattern": "tick", "semantic": "blue", "reason": "offline"}
    if state.thinking:
        return {"pattern": "breathe", "semantic": "cyan", "reason": "thinking"}
    mapping = {
        "warm": {"pattern": "soft", "semantic": "warm"},
        "concern": {"pattern": "slow_pulse", "semantic": "amber"},
        "alert": {"pattern": "double_pulse", "semantic": "magenta"},
        "neutral": {"pattern": "off", "semantic": "neutral"},
    }
    return {**mapping[state.expression], "reason": "expression"}


class DisabledLedDriver:
    def apply(self, state: dict[str, str]) -> None:
        return None


class BridgeLedDriver:
    def __init__(self, settings: Settings):
        search_path = Path(str(settings.get("leds.bridge_search_path", "./hardware/vendor"))).expanduser()
        if not search_path.is_absolute():
            search_path = (settings.root / search_path).resolve()
        module_name = str(settings.get("leds.bridge_module", "kendra_led_bridge"))
        sys.path.insert(0, str(search_path))
        try:
            self.module = importlib.import_module(module_name)
        finally:
            if sys.path and sys.path[0] == str(search_path):
                sys.path.pop(0)
        if not callable(getattr(self.module, "apply", None)):
            raise RuntimeError("LED bridge must provide apply(pattern, semantic, reason)")

    def apply(self, state: dict[str, str]) -> None:
        self.module.apply(**state)


class LedService:
    def __init__(self, settings: Settings):
        self.settings = settings
        driver_name = str(settings.get("leds.driver", "disabled"))
        self.driver = DisabledLedDriver() if driver_name == "disabled" else BridgeLedDriver(settings)
        self.state = SystemLightState()
        self.server = UnixJsonServer(settings.socket_path("leds"), self.handle)

    async def _apply(self) -> dict[str, str]:
        resolved = resolve_light(self.state)
        await asyncio.to_thread(self.driver.apply, resolved)
        return resolved

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, "state": asdict(self.state), "resolved": resolve_light(self.state)}
        if method == "express":
            value = str(params["state"])
            if value not in {"warm", "concern", "alert", "neutral"}:
                raise ValueError("Invalid expressive state")
            self.state.expression = value  # type: ignore[assignment]
            return await self._apply()
        if method == "system":
            allowed = {
                "reflex_fault", "critical_battery", "updating", "charging",
                "low_battery", "offline", "thinking",
            }
            unknown = set(params) - allowed
            if unknown:
                raise ValueError(f"Unknown system light fields: {sorted(unknown)}")
            for key, value in params.items():
                setattr(self.state, key, bool(value))
            return await self._apply()
        raise KeyError(f"Unknown LED method: {method}")

    async def run(self) -> None:
        await self.server.serve_forever()


def run(settings: Settings) -> None:
    asyncio.run(LedService(settings).run())
