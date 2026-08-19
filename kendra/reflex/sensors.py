from __future__ import annotations

import importlib
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..protocol import CliffState


@dataclass(slots=True)
class SensorSnapshot:
    cliff: CliffState
    front_cm: float | None = None
    battery_voltage: float | None = None


class SensorSuite(Protocol):
    def read(self) -> SensorSnapshot: ...
    def close(self) -> None: ...


class SimulationSensorSuite:
    def __init__(self):
        self.snapshot = SensorSnapshot(cliff=CliffState(), front_cm=100.0, battery_voltage=8.0)

    def read(self) -> SensorSnapshot:
        return self.snapshot

    def close(self) -> None:
        return None


class WebotsSensorSuite:
    """Reads virtual cliff/obstacle/battery telemetry from the Webots bridge."""

    def __init__(self, settings: Settings):
        from ..body.webots import WebotsBridgeClient

        self.client = WebotsBridgeClient(settings)

    def read(self) -> SensorSnapshot:
        value = self.client.telemetry()
        cliff = value.get("cliff") or {}
        return SensorSnapshot(
            cliff=CliffState(
                fl=bool(cliff.get("fl", False)),
                fr=bool(cliff.get("fr", False)),
                rl=bool(cliff.get("rl", False)),
                rr=bool(cliff.get("rr", False)),
            ),
            front_cm=None if value.get("front_cm") is None else float(value["front_cm"]),
            battery_voltage=None if value.get("battery_voltage") is None else float(value["battery_voltage"]),
        )

    def close(self) -> None:
        return None


class MCP23017CliffArray:
    """Four Pololu #2579 sensors through an MCP23017.

    OUT HIGH means insufficient reflected carrier; Kendra interprets that as
    no-floor/edge. ENABLE is sequenced one emitter at a time to reduce cross-talk.
    """

    IODIRA = 0x00
    IODIRB = 0x01
    GPIOA = 0x12
    GPIOB = 0x13

    def __init__(self, settings: Settings):
        try:
            from smbus2 import SMBus
        except ImportError as exc:
            raise RuntimeError("Install the hardware extra: pip install -e '.[hardware]'") from exc
        cfg = settings.require("reflex.sensors.mcp23017")
        self.address = int(cfg["address"], 0) if isinstance(cfg["address"], str) else int(cfg["address"])
        self.bus = SMBus(int(cfg.get("bus", 1)))
        self.positions = dict(cfg["positions"])
        expected = {"fl", "fr", "rl", "rr"}
        if set(self.positions) != expected:
            raise ValueError("Exactly four cliff positions are required: fl, fr, rl, rr")
        self.settle_seconds = float(cfg.get("settle_ms", 3)) / 1000.0
        self.samples_per_sensor = int(cfg.get("samples_per_sensor", 3))
        self.floor_votes_required = int(cfg.get("floor_votes_required", 2))
        self.out_bank = str(cfg.get("out_bank", "A")).upper()
        self.enable_bank = str(cfg.get("enable_bank", "B")).upper()
        if self.out_bank == self.enable_bank:
            raise ValueError("Use separate MCP23017 banks for sensor OUT and ENABLE in this reference wiring")
        self.out_gpio = self.GPIOA if self.out_bank == "A" else self.GPIOB
        self.enable_gpio = self.GPIOA if self.enable_bank == "A" else self.GPIOB
        out_dir = 0xFF
        enable_dir = 0xFF
        for pos in self.positions.values():
            enable_dir &= ~(1 << int(pos["enable_bit"]))
        if self.out_bank == "A":
            self.bus.write_byte_data(self.address, self.IODIRA, out_dir)
            self.bus.write_byte_data(self.address, self.IODIRB, enable_dir)
        else:
            self.bus.write_byte_data(self.address, self.IODIRB, out_dir)
            self.bus.write_byte_data(self.address, self.IODIRA, enable_dir)
        self._disable_all()

    def _disable_all(self) -> None:
        self.bus.write_byte_data(self.address, self.enable_gpio, 0x00)

    def _sample_floor(self, out_bit: int, enable_bit: int) -> bool:
        self._disable_all()
        self.bus.write_byte_data(self.address, self.enable_gpio, 1 << enable_bit)
        time.sleep(self.settle_seconds)
        floor_votes = 0
        for _ in range(self.samples_per_sensor):
            value = self.bus.read_byte_data(self.address, self.out_gpio)
            out_high = bool(value & (1 << out_bit))
            if not out_high:
                floor_votes += 1
            time.sleep(0.001)
        self._disable_all()
        return floor_votes >= self.floor_votes_required

    def read_cliff(self) -> CliffState:
        floor: dict[str, bool] = {}
        for name in ("fl", "fr", "rl", "rr"):
            pos = self.positions[name]
            floor[name] = self._sample_floor(int(pos["out_bit"]), int(pos["enable_bit"]))
        return CliffState(**{name: not floor[name] for name in floor})

    def read(self) -> SensorSnapshot:
        return SensorSnapshot(cliff=self.read_cliff())

    def close(self) -> None:
        self._disable_all()
        self.bus.close()


class VerifiedTelemetryBridge:
    """Optional independently verified ultrasonic/battery telemetry bridge."""

    def __init__(self, settings: Settings):
        cfg = settings.get("reflex.sensors.telemetry_bridge", {}) or {}
        search_path = Path(str(cfg.get("search_path", "./hardware/vendor"))).expanduser()
        if not search_path.is_absolute():
            search_path = (settings.root / search_path).resolve()
        module_name = str(cfg.get("module", "kendra_reflex_bridge"))
        if not search_path.exists():
            raise FileNotFoundError(f"Telemetry bridge directory not found: {search_path}")
        sys.path.insert(0, str(search_path))
        try:
            self.module = importlib.import_module(module_name)
        finally:
            if sys.path and sys.path[0] == str(search_path):
                sys.path.pop(0)

    def front_cm(self) -> float | None:
        func = getattr(self.module, "front_distance_cm", None)
        if not callable(func):
            return None
        value = func()
        return float(value) if value is not None else None

    def battery_voltage(self) -> float | None:
        func = getattr(self.module, "battery_voltage", None)
        if not callable(func):
            return None
        value = func()
        return float(value) if value is not None else None

    def close(self) -> None:
        func = getattr(self.module, "close", None)
        if callable(func):
            func()


class CompositeSensorSuite:
    def __init__(self, settings: Settings):
        self.cliffs = MCP23017CliffArray(settings)
        self.telemetry = VerifiedTelemetryBridge(settings)

    def read(self) -> SensorSnapshot:
        return SensorSnapshot(
            cliff=self.cliffs.read_cliff(),
            front_cm=self.telemetry.front_cm(),
            battery_voltage=self.telemetry.battery_voltage(),
        )

    def close(self) -> None:
        self.cliffs.close()
        self.telemetry.close()


def build_sensor_suite(settings: Settings) -> SensorSuite:
    provider = str(settings.get("reflex.sensors.provider", "simulation"))
    if provider == "simulation":
        return SimulationSensorSuite()
    if provider == "webots":
        return WebotsSensorSuite(settings)
    if provider == "mcp23017":
        return MCP23017CliffArray(settings)
    if provider == "mcp23017_bridge":
        return CompositeSensorSuite(settings)
    raise ValueError(f"Unknown reflex sensor provider: {provider}")
