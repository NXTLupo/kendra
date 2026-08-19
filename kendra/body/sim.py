from __future__ import annotations

import math
import time
from typing import Any

from .base import BodyDriver
from .locomotion import DEFAULT_PROFILE


class SimulationBodyDriver(BodyDriver):
    """Virtual Kendra's body: the same gait timing and displacement her
    hardware will have, so a walk *takes as long* and *carries as far* in
    the app as it will on the floor.

    One gait cycle = 4 tripod phases (Adeept move.py), so `steps` are gait
    cycles, not footfalls, and the driver actually sleeps the cycle time.
    """

    def __init__(self):
        self.state = "ready"
        self.last_action: dict[str, Any] | None = None
        self.x = 0.0
        self.y = 0.0
        self.heading_deg = 0.0
        self.head_pan = 0.0
        self.head_tilt = 0.0
        self.profile = DEFAULT_PROFILE

    def _record(self, action: str, **kwargs: Any) -> dict[str, Any]:
        self.state = action
        self.last_action = {"action": action, **kwargs, "timestamp": time.time()}
        self.state = "ready"
        return {
            "ok": True,
            "simulated": True,
            **self.last_action,
            "pose": {"x_m": round(self.x, 3), "y_m": round(self.y, 3),
                     "heading_deg": round(self.heading_deg % 360, 1)},
        }

    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]:
        cycles = max(1, int(steps))
        time.sleep(min(4.0, cycles * self.profile.cycle_seconds))
        travel = self.profile.distance_for_cycles(cycles)
        if direction in {"forward", "backward"}:
            sign = 1.0 if direction == "forward" else -1.0
            radians = math.radians(self.heading_deg)
            self.x += sign * travel * math.cos(radians)
            self.y += sign * travel * math.sin(radians)
        else:
            # Vendor 'left'/'right' walking is a turn-in-place tripod stroke.
            self.heading_deg += (-1.0 if direction == "left" else 1.0) * (
                cycles * self.profile.degrees_per_cycle
            )
            travel = 0.0
        return self._record(
            "walk", direction=direction, steps=cycles, speed=speed,
            travelled_m=round(travel, 3),
        )

    def turn(self, degrees: float, speed: float) -> dict[str, Any]:
        cycles = self.profile.cycles_for_angle(degrees)
        time.sleep(min(4.0, cycles * self.profile.cycle_seconds))
        self.heading_deg += float(degrees)
        return self._record("turn", degrees=degrees, speed=speed, cycles=cycles)

    def pose(self, name: str) -> dict[str, Any]:
        return self._record("pose", name=name)

    def look(self, pan: float, tilt: float) -> dict[str, Any]:
        # Her real body has a two-servo head (channels 12/13); the simulated
        # body tracks the same pan/tilt so the UI buttons and future gaze
        # behaviors exercise identical code.
        self.head_pan = float(pan)
        self.head_tilt = float(tilt)
        return self._record("look", pan=self.head_pan, tilt=self.head_tilt)

    def stop(self) -> dict[str, Any]:
        self.state = "stopped"
        return self._record("stop")
