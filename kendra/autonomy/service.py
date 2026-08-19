from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime
from datetime import time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

from ..brain.service import BrainClient
from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer


class AutonomyService:
    """Bounded idle-goal scheduler. Disabled by default."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.get("autonomy.enabled", False) and settings.get("agent.allow_autonomy", False))
        self.poll_seconds = float(settings.get("autonomy.poll_seconds", 30))
        self.mission_cooldown = float(settings.get("autonomy.mission_cooldown_seconds", 900))
        self.last_mission = 0.0
        self.agent = UnixJsonClient(settings.socket_path("agent"), timeout=220)
        self.body = UnixJsonClient(settings.socket_path("body"), timeout=5)
        self.brain = BrainClient(settings)
        self.voice = UnixJsonClient(settings.runtime_dir / "voice.sock", timeout=60)
        self.server = UnixJsonServer(settings.socket_path("autonomy"), self.handle)
        self.timezone = ZoneInfo(str(settings.get("project.timezone", "UTC")))

    def _parse_time(self, text: str) -> clock_time:
        hour, minute = (int(part) for part in text.split(":", 1))
        return clock_time(hour=hour, minute=minute)

    def _quiet_now(self) -> bool:
        start = self._parse_time(str(self.settings.get("autonomy.quiet_hours_start", "22:00")))
        end = self._parse_time(str(self.settings.get("autonomy.quiet_hours_end", "08:00")))
        current = datetime.now(self.timezone).time().replace(tzinfo=None)
        if start <= end:
            return start <= current < end
        return current >= start or current < end

    async def _eligible(self) -> tuple[bool, str]:
        if not self.enabled:
            return False, "autonomy_disabled"
        if self._quiet_now():
            return False, "quiet_hours"
        if time.monotonic() - self.last_mission < self.mission_cooldown:
            return False, "cooldown"
        try:
            obs = await self.body.call("observation")
        except Exception:
            return False, "body_unavailable"
        if obs.get("reflex_lock"):
            return False, "reflex_lock"
        battery = (obs.get("battery") or {}).get("state", "unknown")
        if battery in {"low", "critical"}:
            return False, f"battery_{battery}"
        return True, "ok"

    async def choose_mission(self) -> str:
        context = await self.brain.rpc.call("context", {"query": "current interests goals unresolved questions", "limit": 4})
        questions = context.get("open_questions", [])
        goals = context.get("goals", [])
        interests = context.get("interests", [])
        choices: list[str] = [
            "Do a short, safety-bounded room patrol. Move only in small bursts and stop if there is nothing useful to inspect.",
            "Do a brief physical stretch using only verified poses, then stop.",
        ]
        if questions:
            choices.append(f"Investigate this unresolved question using local/offline research when possible: {questions[0]['question']}")
        if goals:
            choices.append(f"Make one bounded step toward this persistent goal: {goals[0]['title']}")
        if interests:
            choices.append(f"Explore one useful fact related to this current interest without unnecessary movement: {interests[0]['topic']}")
        return random.choice(choices)

    async def run_one(self) -> dict[str, Any]:
        ok, reason = await self._eligible()
        if not ok:
            return {"ran": False, "reason": reason}
        mission = await self.choose_mission()
        self.last_mission = time.monotonic()
        result = await self.agent.call(
            "turn",
            {
                "text": f"AUTONOMOUS MISSION: {mission}\nComplete this within normal movement/tool budgets and finish explicitly.",
                "source": "autonomy",
                "autonomous": True,
            },
        )
        return {"ran": True, "mission": mission, "result": result}

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            ok, reason = await self._eligible()
            return {"ok": True, "enabled": self.enabled, "currently_eligible": ok, "reason": reason}
        if method == "run_once":
            return await self.run_one()
        if method == "enable":
            if not bool(self.settings.get("agent.allow_autonomy", False)):
                raise PermissionError("agent.allow_autonomy must be true in local config before runtime enabling")
            self.enabled = bool(params.get("enabled", True))
            return {"enabled": self.enabled}
        raise KeyError(f"Unknown autonomy method: {method}")

    async def loop(self) -> None:
        while True:
            try:
                await self.run_one()
            except Exception:
                pass
            await asyncio.sleep(self.poll_seconds)

    async def run(self) -> None:
        await self.server.start()
        task = asyncio.create_task(self.loop())
        try:
            assert self.server.server is not None
            async with self.server.server:
                await self.server.server.serve_forever()
        finally:
            task.cancel()


def run(settings: Settings) -> None:
    asyncio.run(AutonomyService(settings).run())
