from __future__ import annotations

import time
from dataclasses import dataclass

from ..config import Settings
from ..protocol import BatteryState, ReflexState
from .sensors import SensorSnapshot


@dataclass(slots=True)
class ReflexController:
    obstacle_hard_stop_cm: float
    low_battery_voltage: float | None
    critical_battery_voltage: float | None
    max_continuous_motion_seconds: float
    minimum_rest_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> ReflexController:
        return cls(
            obstacle_hard_stop_cm=float(settings.get("reflex.obstacle_hard_stop_cm", 18.0)),
            low_battery_voltage=settings.get("reflex.low_battery_voltage"),
            critical_battery_voltage=settings.get("reflex.critical_battery_voltage"),
            max_continuous_motion_seconds=float(settings.get("reflex.max_continuous_motion_seconds", 8.0)),
            minimum_rest_seconds=float(settings.get("reflex.minimum_rest_seconds", 4.0)),
        )

    def evaluate(
        self,
        snapshot: SensorSnapshot,
        *,
        motion_started_monotonic: float | None = None,
        last_motion_ended_monotonic: float | None = None,
    ) -> ReflexState:
        blocked: set[str] = set()
        cliff = snapshot.cliff
        if cliff.fl or cliff.fr:
            blocked.add("forward")
        if cliff.rl or cliff.rr:
            blocked.add("backward")
        if cliff.fl or cliff.rl:
            blocked.add("left")
        if cliff.fr or cliff.rr:
            blocked.add("right")
        if cliff.any():
            blocked.add("turn")

        faults: list[str] = []
        stop_required = cliff.any()
        if snapshot.front_cm is not None and snapshot.front_cm < self.obstacle_hard_stop_cm:
            blocked.add("forward")
            stop_required = True
            faults.append("obstacle_hard_stop")

        battery = BatteryState(state="unknown", voltage=snapshot.battery_voltage)
        if snapshot.battery_voltage is not None:
            if self.critical_battery_voltage is not None and snapshot.battery_voltage <= float(self.critical_battery_voltage):
                battery.state = "critical"
                stop_required = True
                faults.append("critical_battery")
            elif self.low_battery_voltage is not None and snapshot.battery_voltage <= float(self.low_battery_voltage):
                battery.state = "low"
            else:
                battery.state = "normal"

        now = time.monotonic()
        rest_required = False
        if motion_started_monotonic is not None and now - motion_started_monotonic > self.max_continuous_motion_seconds:
            stop_required = True
            rest_required = True
            faults.append("motion_duty_limit")
        if last_motion_ended_monotonic is not None and now - last_motion_ended_monotonic < self.minimum_rest_seconds:
            rest_required = True

        if cliff.any():
            faults.append("cliff")
        return ReflexState(
            heartbeat_monotonic=now,
            healthy=True,
            cliff=cliff,
            front_cm=snapshot.front_cm,
            battery=battery,
            blocked_directions=sorted(blocked),
            stop_required=stop_required,
            rest_required=rest_required,
            faults=sorted(set(faults)),
        )
