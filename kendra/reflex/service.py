from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from ..protocol import CliffState, ReflexState
from .controller import ReflexController
from .sensors import SensorSnapshot, build_sensor_suite

LOG = logging.getLogger(__name__)


class ReflexService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.sensors = build_sensor_suite(settings)
        self.controller = ReflexController.from_settings(settings)
        self.state_file = settings.runtime_dir / str(settings.require("reflex.state_file"))
        self.motion_state_file = settings.runtime_dir / str(settings.get("body.motion_state_file", "motion-state.json"))
        self.server = UnixJsonServer(settings.socket_path("reflex"), self.handle)
        self.poll_interval = float(settings.get("reflex.poll_interval_seconds", 0.04))
        self.clear_floor_samples = int(settings.get("reflex.clear_floor_samples", 5))
        self.current = ReflexState(heartbeat_monotonic=time.monotonic(), healthy=False)
        self._last_emergency_stop = 0.0
        self._latched_cliff = CliffState()
        self._clear_count = 0

    def _atomic_write_state(self) -> None:
        temp = self.state_file.with_suffix(".tmp")
        temp.write_text(self.current.model_dump_json(), encoding="utf-8")
        os.replace(temp, self.state_file)

    def _motion_times(self) -> tuple[float | None, float | None]:
        if not self.motion_state_file.exists():
            return None, None
        try:
            data = json.loads(self.motion_state_file.read_text(encoding="utf-8"))
            started = data.get("started_monotonic") if data.get("moving") else None
            ended = data.get("last_ended_monotonic")
            return (float(started) if started is not None else None, float(ended) if ended is not None else None)
        except Exception:
            return None, None

    def _apply_cliff_latch(self, snapshot: SensorSnapshot) -> SensorSnapshot:
        if snapshot.cliff.any():
            self._latched_cliff = snapshot.cliff
            self._clear_count = 0
            return snapshot
        if self._latched_cliff.any():
            self._clear_count += 1
            if self._clear_count < self.clear_floor_samples:
                return SensorSnapshot(
                    cliff=self._latched_cliff,
                    front_cm=snapshot.front_cm,
                    battery_voltage=snapshot.battery_voltage,
                )
            self._latched_cliff = CliffState()
            self._clear_count = 0
        return snapshot

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method in {"health", "state"}:
            return self.current.model_dump()
        raise KeyError(f"Unknown reflex method: {method}")

    async def _stop_body_if_needed(self) -> None:
        if not self.current.stop_required:
            return
        now = time.monotonic()
        if now - self._last_emergency_stop < 0.2:
            return
        self._last_emergency_stop = now
        try:
            client = UnixJsonClient(self.settings.socket_path("body"), timeout=0.3)
            await client.call("emergency_stop", {"reason": ",".join(self.current.faults) or "reflex"})
        except Exception:
            pass

    async def poll_loop(self) -> None:
        while True:
            try:
                raw_snapshot = await asyncio.to_thread(self.sensors.read)
                snapshot = self._apply_cliff_latch(raw_snapshot)
                started, ended = self._motion_times()
                self.current = self.controller.evaluate(
                    snapshot,
                    motion_started_monotonic=started,
                    last_motion_ended_monotonic=ended,
                )
            except Exception as exc:
                LOG.exception("Reflex sensor failure")
                self.current = ReflexState(
                    heartbeat_monotonic=time.monotonic(),
                    healthy=False,
                    stop_required=True,
                    faults=[f"sensor_failure:{type(exc).__name__}"],
                )
            self._atomic_write_state()
            await self._stop_body_if_needed()
            await asyncio.sleep(self.poll_interval)

    async def run(self) -> None:
        await self.server.start()
        poll_task = asyncio.create_task(self.poll_loop())
        try:
            assert self.server.server is not None
            async with self.server.server:
                await self.server.server.serve_forever()
        finally:
            poll_task.cancel()
            self.sensors.close()


def run(settings: Settings) -> None:
    asyncio.run(ReflexService(settings).run())
