from __future__ import annotations

import asyncio
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from .planner import AgentRuntime


class AgentService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.runtime = AgentRuntime(settings)
        self.server = UnixJsonServer(settings.socket_path("agent"), self.handle)

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, "llm": await self.runtime.llm.health()}
        if method == "turn":
            return await self.runtime.turn(
                str(params["text"]),
                session_id=params.get("session_id"),
                source=str(params.get("source", "text")),
                autonomous=bool(params.get("autonomous", False)),
            )
        raise KeyError(f"Unknown agent method: {method}")

    async def run(self) -> None:
        # Say out loud, at every boot, whether the brain that answered is the
        # brain that was designed. Her stack ran a configuration the repo
        # explicitly rejects for two days because nothing ever asked.
        from ..health.runtime_truth import warn_on_drift

        await warn_on_drift(self.settings)
        await self.server.serve_forever()


class AgentClient:
    def __init__(self, settings: Settings):
        timeout = float(settings.get("agent.client_timeout_seconds", 320))
        self.rpc = UnixJsonClient(settings.socket_path("agent"), timeout=timeout)

    async def turn(self, text: str, session_id: str | None = None, source: str = "text") -> dict[str, Any]:
        return await self.rpc.call("turn", {"text": text, "session_id": session_id, "source": source})


def run(settings: Settings) -> None:
    asyncio.run(AgentService(settings).run())
