from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from ..brain.service import BrainClient
from ..config import Settings
from ..ipc import UnixJsonClient
from ..research.service import ResearchClient
from ..vision.service import VisionClient


class ToolFailure(RuntimeError):
    pass


@dataclass(slots=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[BaseModel], Awaitable[Any]]
    movement: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.args_model.model_json_schema(),
        }


class WalkArgs(BaseModel):
    direction: str
    steps: int = Field(default=1, ge=1, le=8)
    speed: float = Field(default=0.3, ge=0.0, le=1.0)


class TurnArgs(BaseModel):
    degrees: float = Field(ge=-180, le=180)
    speed: float = Field(default=0.3, ge=0.0, le=1.0)


class PoseArgs(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class LookArgs(BaseModel):
    pan: float = Field(default=0, ge=-180, le=180)
    tilt: float = Field(default=0, ge=-90, le=90)


class StopArgs(BaseModel):
    reason: str = Field(default="agent requested stop", max_length=200)


class ObserveArgs(BaseModel):
    semantic: bool = False
    question: str = Field(default="Describe the scene briefly.", max_length=400)


class ResearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    mode: str = Field(default="auto", pattern="^(auto|online|offline)$")


class RecallArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class GoalArgs(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    priority: float = Field(default=0.5, ge=0, le=1)


class QuestionArgs(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    interest_weight: float = Field(default=0.5, ge=0, le=1)


class ExpressArgs(BaseModel):
    state: str = Field(pattern="^(warm|concern|alert|neutral)$")


class DeliverArgs(BaseModel):
    recipient_alias: str = Field(min_length=1, max_length=80)
    photo_id: str = Field(min_length=1, max_length=160)
    note: str = Field(default="", max_length=500)


class CheckUpdateArgs(BaseModel):
    pass


class RequestUpdateArgs(BaseModel):
    confirmation: str = Field(pattern="^install signed intelligence upgrade$")


class ToolRegistry:
    def __init__(self, settings: Settings, capabilities: dict[str, Any]):
        self.settings = settings
        self.capabilities = capabilities
        self.body = UnixJsonClient(settings.socket_path("body"), timeout=10)
        self.brain = BrainClient(settings)
        self.research = ResearchClient(settings)
        self.vision = VisionClient(settings)
        self.leds = UnixJsonClient(settings.socket_path("leds"), timeout=4)
        self.delivery = UnixJsonClient(settings.runtime_dir / "delivery.sock", timeout=65)
        self.specs: dict[str, ToolSpec] = {}
        self._register_base()

    def _add(self, spec: ToolSpec) -> None:
        self.specs[spec.name] = spec

    def _register_base(self) -> None:
        self._add(ToolSpec("walk", "Walk a small number of steps in one direction.", WalkArgs, self._walk, movement=True))
        self._add(ToolSpec("turn", "Turn in place by a bounded number of degrees.", TurnArgs, self._turn, movement=True))
        self._add(ToolSpec("pose", "Move to a verified named pose.", PoseArgs, self._pose, movement=True))
        self._add(ToolSpec("stop", "Request an immediate software body stop.", StopArgs, self._stop))
        if bool(self.capabilities.get("has_head_gimbal")):
            self._add(ToolSpec("look", "Aim the verified head/camera gimbal.", LookArgs, self._look, movement=True))
        self._add(ToolSpec("observe", "Capture a local camera observation.", ObserveArgs, self._observe))
        self._add(ToolSpec("research", "Retrieve source-backed online or offline evidence.", ResearchArgs, self._research))
        self._add(ToolSpec("recall", "Search Kendra Brain for relevant durable memories.", RecallArgs, self._recall))
        self._add(ToolSpec("add_goal", "Create a persistent local goal.", GoalArgs, self._goal))
        self._add(ToolSpec("add_question", "Create a persistent unresolved question.", QuestionArgs, self._question))
        self._add(ToolSpec("express", "Request a low-priority expressive light state.", ExpressArgs, self._express))
        self._add(ToolSpec("deliver_photo", "Send a previously captured photo using an approved local alias.", DeliverArgs, self._deliver))
        if bool(self.settings.get("updates.allow_voice_check", True)):
            self._add(
                ToolSpec(
                    "check_intelligence_upgrade",
                    "Check Kendra's fixed Git channel for a newer intelligence release.",
                    CheckUpdateArgs,
                    self._check_update,
                )
            )
            self._add(
                ToolSpec(
                    "request_intelligence_upgrade",
                    "Request a signed A/B intelligence upgrade only after the user says the exact confirmation phrase.",
                    RequestUpdateArgs,
                    self._request_update,
                )
            )

    def schemas(self) -> list[dict[str, Any]]:
        return [spec.schema() for spec in self.specs.values()]

    def is_movement(self, name: str) -> bool:
        spec = self.specs.get(name)
        return bool(spec and spec.movement)

    async def execute(self, name: str, args: dict[str, Any]) -> Any:
        spec = self.specs.get(name)
        if spec is None:
            raise ToolFailure(f"Tool is not whitelisted: {name}")
        try:
            parsed = spec.args_model.model_validate(args)
        except ValidationError as exc:
            raise ToolFailure(f"Invalid arguments for {name}: {exc}") from exc
        return await spec.handler(parsed)

    async def _walk(self, args: WalkArgs) -> Any:
        if args.direction not in {"forward", "backward", "left", "right"}:
            raise ToolFailure("walk.direction must be forward/backward/left/right")
        return await self.body.call("walk", args.model_dump())

    async def _turn(self, args: TurnArgs) -> Any:
        return await self.body.call("turn", args.model_dump())

    async def _pose(self, args: PoseArgs) -> Any:
        return await self.body.call("pose", args.model_dump())

    async def _look(self, args: LookArgs) -> Any:
        return await self.body.call("look", args.model_dump())

    async def _stop(self, args: StopArgs) -> Any:
        return await self.body.call("stop", args.model_dump())

    async def _observe(self, args: ObserveArgs) -> Any:
        return await self.vision.observe(args.semantic, args.question)

    async def _research(self, args: ResearchArgs) -> Any:
        return await self.research.evidence(args.query, args.mode)

    async def _recall(self, args: RecallArgs) -> Any:
        return await self.brain.search(args.query, args.limit)

    async def _goal(self, args: GoalArgs) -> Any:
        return await self.brain.rpc.call("goal", {**args.model_dump(), "provenance": "inferred"})

    async def _question(self, args: QuestionArgs) -> Any:
        return await self.brain.rpc.call("question", args.model_dump())

    async def _express(self, args: ExpressArgs) -> Any:
        return await self.leds.call("express", args.model_dump())

    async def _deliver(self, args: DeliverArgs) -> Any:
        return await self.delivery.call("deliver_photo", args.model_dump())

    async def _check_update(self, args: CheckUpdateArgs) -> Any:
        del args
        from ..updates.installer import SignedReleaseStager

        return await asyncio.to_thread(SignedReleaseStager(self.settings).check)

    async def _request_update(self, args: RequestUpdateArgs) -> Any:
        del args
        from ..updates.installer import SignedReleaseStager

        return await asyncio.to_thread(SignedReleaseStager(self.settings).stage)
