from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonServer
from ..protocol import Observation, ReflexState
from .base import BodyDriver
from .locomotion import DEFAULT_PROFILE, segment_plan
from .raspclaws import RaspClawsDriver
from .sim import SimulationBodyDriver
from .webots import WebotsBodyDriver

LOG = logging.getLogger(__name__)


def build_driver(settings: Settings) -> BodyDriver:
    name = str(settings.get("body.driver", "simulation"))
    if name == "simulation":
        return SimulationBodyDriver()
    if name == "raspclaws":
        return RaspClawsDriver(settings)
    if name == "webots":
        return WebotsBodyDriver(settings)
    raise ValueError(f"Unknown body driver: {name}")


class BodyService:
    def __init__(self, settings: Settings):
        self.settings = settings
        if settings.get("project.mode") == "hardware":
            settings.assert_hardware_gates()
        self.driver = build_driver(settings)
        self.server = UnixJsonServer(settings.socket_path("body"), self.handle)
        self.capabilities = dict(settings.require("body.capabilities"))
        self.allowed_poses = {
            str(name).strip().lower()
            for name in settings.get("body.allowed_poses", [])
            if str(name).strip()
        }
        self.capabilities["allowed_poses"] = sorted(self.allowed_poses)
        self.max_steps = int(settings.get("body.max_steps_per_call", 4))
        self.max_turn = float(settings.get("body.max_turn_degrees", 45))
        self.speed_min = float(settings.get("body.speed_min", 0.15))
        self.speed_max = float(settings.get("body.speed_max", 0.55))
        self.motion_timeout = float(settings.get("body.motion_timeout_seconds", 5.0))
        self.reflex_state_file = settings.runtime_dir / str(settings.require("reflex.state_file"))
        self.motion_state_file = settings.runtime_dir / str(settings.get("body.motion_state_file", "motion-state.json"))
        self.reflex_max_age = float(settings.get("reflex.heartbeat_max_age_seconds", 0.75))
        self.body_state = "ready"
        self._motion_lock = asyncio.Lock()
        self._last_motion_ended = time.monotonic()
        self._write_motion_state(moving=False)

    def _write_motion_state(self, *, moving: bool, started: float | None = None) -> None:
        import json

        payload = {
            "moving": moving,
            "started_monotonic": started,
            "last_ended_monotonic": self._last_motion_ended,
            "body_state": self.body_state,
            "written_monotonic": time.monotonic(),
        }
        temp = self.motion_state_file.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.motion_state_file)

    def _reflex(self) -> ReflexState:
        if not self.reflex_state_file.exists():
            raise RuntimeError("Reflex heartbeat is missing; motion is disabled")
        try:
            state = ReflexState.model_validate_json(self.reflex_state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise RuntimeError("Reflex state is unreadable; motion is disabled") from exc
        age = time.monotonic() - state.heartbeat_monotonic
        if age < 0 or age > self.reflex_max_age:
            raise RuntimeError(f"Reflex heartbeat is stale ({age:.3f}s); motion is disabled")
        if not state.healthy:
            raise RuntimeError("Reflex subsystem reports unhealthy; motion is disabled")
        return state

    def _clamp_speed(self, speed: float) -> float:
        return min(self.speed_max, max(self.speed_min, float(speed)))

    async def _run_motion(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        async with self._motion_lock:
            started = time.monotonic()
            self.body_state = "moving"
            self._write_motion_state(moving=True, started=started)
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(func, *args, **kwargs), timeout=self.motion_timeout
                )
            except TimeoutError as exc:
                await asyncio.to_thread(self.driver.stop)
                self.body_state = "motion_fault"
                raise RuntimeError("Motion command timed out and was stopped") from exc
            finally:
                self._last_motion_ended = time.monotonic()
                if self.body_state != "motion_fault":
                    self.body_state = "ready"
                self._write_motion_state(moving=False)

    async def walk(self, direction: str, steps: int, speed: float) -> Any:
        if direction not in {"forward", "backward", "left", "right"}:
            raise ValueError("direction must be forward/backward/left/right")
        state = self._reflex()
        if state.stop_required or direction in state.blocked_directions:
            raise RuntimeError(f"Reflex blocks {direction}: {state.faults or state.blocked_directions}")
        if state.rest_required:
            raise RuntimeError("Reflex requires a servo rest period")
        steps = min(self.max_steps, max(1, int(steps)))
        return await self._run_motion(self.driver.walk, direction, steps, self._clamp_speed(speed))

    async def turn(self, degrees: float, speed: float) -> Any:
        state = self._reflex()
        if state.stop_required or "turn" in state.blocked_directions:
            raise RuntimeError(f"Reflex blocks turning: {state.faults or state.blocked_directions}")
        if state.rest_required:
            raise RuntimeError("Reflex requires a servo rest period")
        degrees = min(self.max_turn, max(-self.max_turn, float(degrees)))
        return await self._run_motion(self.driver.turn, degrees, self._clamp_speed(speed))

    async def _await_rest(self, limit: float) -> bool:
        """Wait out the reflex rest window. Returns True if she may move."""
        waited = 0.0
        while self._reflex().rest_required and waited < limit:
            await asyncio.sleep(0.5)
            waited += 0.5
        return not self._reflex().rest_required

    async def navigate(self, intent: dict[str, Any], patient: bool = False) -> dict[str, Any]:
        """Execute a typed MovementIntent as bounded, re-checked segments.

        Never one long blind walk: she moves a few gait cycles, re-reads the
        world (reflex faults, cliff, front distance), and only then
        continues. That is what makes "go forward about four feet" honest on
        a robot with no odometry — and it keeps every body call inside the
        reflex heartbeat window.
        """
        mode = str(intent.get("mode", "forward"))
        profile = DEFAULT_PROFILE
        speed = 0.25 if str(intent.get("speed")) == "slow" else 0.4
        if mode == "stop":
            return await self.driver_stop("navigate stop")

        if mode == "sidestep":
            angle = float(intent.get("angle_deg") or 75.0)
            metres = float(intent.get("distance_m") or 0.25)
            turned = await self.navigate({"mode": "turn", "angle_deg": angle, "speed": "slow"})
            forward = {"ok": False, "travelled_m": 0.0, "blocked": turned.get("blocked")}
            if turned.get("ok"):
                forward = await self.navigate(
                    {"mode": "forward", "distance_m": metres, "speed": "slow"}
                )
            # Always face back the way she started, even if the walk was cut
            # short — otherwise a rest pause mid-shuffle leaves her standing
            # sideways, which reads as a malfunction.
            restored = await self.navigate(
                {"mode": "turn", "angle_deg": -float(turned.get("turned_deg") or angle), "speed": "slow"},
                patient=True,
            )
            return {"ok": bool(forward.get("ok")), "mode": mode,
                    "travelled_m": forward.get("travelled_m", 0.0),
                    "heading_restored": bool(restored.get("ok")),
                    "blocked": forward.get("blocked")}
        if mode == "turn":
            degrees = float(intent.get("angle_deg") or 90.0)
            done = 0.0
            remaining = degrees
            rest_limit = 25.0 if patient else 8.0
            while abs(remaining) > 1.0:
                if not await self._await_rest(rest_limit):
                    return {"ok": False, "mode": mode, "turned_deg": done,
                            "blocked": "my legs needed a longer rest than I expected"}
                chunk = max(-self.max_turn, min(self.max_turn, remaining))
                await self.turn(chunk, speed)
                done += chunk
                remaining -= chunk
                state = self._reflex()
                if state.stop_required:
                    return {"ok": False, "mode": mode, "turned_deg": done,
                            "blocked": "my safety layer stopped me"}
            return {"ok": True, "mode": mode, "turned_deg": done}

        direction = {
            "forward": "forward", "goto": "forward", "approach": "forward",
            "backward": "backward", "retreat": "backward",
        }.get(mode, "forward")
        distance = intent.get("distance_m")
        metres = float(distance) if distance else profile.distance_for_cycles(4)
        total_cycles = profile.cycles_for_distance(metres)
        travelled = 0.0
        epoch = getattr(self, "_navigation_epoch", 0)
        for segment in segment_plan(total_cycles, per_segment=self.max_steps):
            if getattr(self, "_navigation_epoch", 0) != epoch:
                return {"ok": False, "mode": mode, "travelled_m": round(travelled, 3),
                        "blocked": "you told me to stop"}
            # Her legs need a breather every few seconds of continuous gait
            # (reflex rest policy). A long walk should PAUSE and continue,
            # not abort — "go forward four feet" is one intention, not six.
            if not await self._await_rest(25.0 if patient else 8.0):
                state = self._reflex()
            state = self._reflex()
            if state.rest_required:
                return {"ok": False, "mode": mode, "travelled_m": round(travelled, 3),
                        "blocked": "my legs needed a longer rest than I expected"}
            if state.stop_required:
                return {"ok": False, "mode": mode, "travelled_m": round(travelled, 3),
                        "blocked": "my safety layer stopped me"}
            if direction == "forward" and state.front_cm is not None:
                if state.front_cm < float(self.settings.get("body.min_forward_clearance_cm", 25)):
                    return {"ok": False, "mode": mode, "travelled_m": round(travelled, 3),
                            "blocked": "there's something right in front of me"}
            await self.walk(direction, segment, speed)
            travelled += profile.distance_for_cycles(segment)
        return {
            "ok": True,
            "mode": mode,
            "travelled_m": round(travelled, 3),
            "requested_m": round(metres, 3),
            "calibrated": profile.calibrated,
        }

    async def driver_stop(self, reason: str) -> dict[str, Any]:
        result = await asyncio.to_thread(self.driver.stop)
        self._write_motion_state(moving=False)
        return {"ok": True, "stopped": True, "reason": reason, "driver": result}

    async def observation(self) -> dict[str, Any]:
        state = self._reflex()
        pose = None
        last_motion = None
        driver_x = getattr(self.driver, "x", None)
        if driver_x is not None:  # simulated bodies only
            pose = {
                "x_m": round(float(self.driver.x), 3),
                "y_m": round(float(getattr(self.driver, "y", 0.0)), 3),
                "heading_deg": round(float(getattr(self.driver, "heading_deg", 0.0)) % 360, 1),
            }
            last = getattr(self.driver, "last_action", None)
            if isinstance(last, dict):
                last_motion = {k: v for k, v in last.items() if k != "timestamp"}
        return Observation(
            pose=pose,
            last_motion=last_motion,
            front_cm=state.front_cm,
            cliff=state.cliff,
            battery=state.battery,
            body_state=self.body_state,
            reflex_lock=state.stop_required or state.rest_required,
            blocked_directions=state.blocked_directions,
            notes=state.faults,
        ).model_dump()

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            reflex_ok = True
            reflex_error = None
            try:
                self._reflex()
            except Exception as exc:
                reflex_ok = False
                reflex_error = str(exc)
            return {
                "ok": True,
                "driver": type(self.driver).__name__,
                "body_state": self.body_state,
                "reflex_ok": reflex_ok,
                "reflex_error": reflex_error,
                "capabilities": self.capabilities,
            }
        if method == "capabilities":
            return self.capabilities
        if method == "observation":
            return await self.observation()
        if method == "navigate":
            return await self.navigate(dict(params.get("intent") or {}))
        if method == "walk":
            return await self.walk(str(params["direction"]), int(params.get("steps", 1)), float(params.get("speed", 0.3)))
        if method == "turn":
            return await self.turn(float(params["degrees"]), float(params.get("speed", 0.3)))
        if method == "pose":
            name = str(params["name"]).strip().lower()
            if name not in self.allowed_poses:
                raise ValueError(
                    f"pose is not verified/allowlisted: {name!r}; allowed poses: {sorted(self.allowed_poses)}"
                )
            state = self._reflex()
            if state.stop_required or state.rest_required:
                raise RuntimeError("Reflex blocks pose motion")
            return await self._run_motion(self.driver.pose, name)
        if method == "look":
            if not self.capabilities.get("has_head_gimbal"):
                raise RuntimeError("look is not available: has_head_gimbal=false")
            state = self._reflex()
            if state.stop_required:
                raise RuntimeError("Reflex blocks gimbal motion")
            return await self._run_motion(self.driver.look, float(params.get("pan", 0)), float(params.get("tilt", 0)))
        if method in {"stop", "emergency_stop"}:
            reason = params.get("reason")
            LOG.warning("Body stop requested: %s", reason or method)
            # Cancel any walk already in flight: "stop" mid-stride must mean
            # stop mid-stride, not "finish the plan then stop".
            self._navigation_epoch = getattr(self, "_navigation_epoch", 0) + 1
            self.body_state = "stopped"
            result = await asyncio.to_thread(self.driver.stop)
            self._last_motion_ended = time.monotonic()
            self._write_motion_state(moving=False)
            return result
        raise KeyError(f"Unknown body method: {method}")

    async def run(self) -> None:
        try:
            await self.server.serve_forever()
        finally:
            await asyncio.to_thread(self.driver.stop)
            self.driver.close()


def run(settings: Settings) -> None:
    asyncio.run(BodyService(settings).run())
