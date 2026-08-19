from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


Direction = Literal["forward", "backward", "left", "right"]
Provenance = Literal["observed", "user_stated", "researched", "inferred", "system"]
MemoryKind = Literal[
    "fact",
    "episode",
    "preference",
    "relationship",
    "reflection",
    "place",
    "skill_note",
]


class CliffState(BaseModel):
    fl: bool = False
    fr: bool = False
    rl: bool = False
    rr: bool = False

    def any(self) -> bool:
        return self.fl or self.fr or self.rl or self.rr


class BatteryState(BaseModel):
    state: Literal["unknown", "normal", "low", "critical", "charging"] = "unknown"
    voltage: float | None = None


class Observation(BaseModel):
    timestamp: str = Field(default_factory=utc_now_iso)
    front_cm: float | None = None
    cliff: CliffState = Field(default_factory=CliffState)
    battery: BatteryState = Field(default_factory=BatteryState)
    network: Literal["online", "offline", "unknown"] = "unknown"
    people_in_view: int = 0
    body_state: str = "unknown"
    reflex_lock: bool = False
    blocked_directions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # Simulated bodies report where they are; the hardware driver has no
    # odometry and simply omits this. Display-only — nothing depends on it.
    pose: dict[str, float] | None = None
    last_motion: dict[str, object] | None = None


class ReflexState(BaseModel):
    timestamp: str = Field(default_factory=utc_now_iso)
    heartbeat_monotonic: float
    healthy: bool = True
    cliff: CliffState = Field(default_factory=CliffState)
    front_cm: float | None = None
    battery: BatteryState = Field(default_factory=BatteryState)
    blocked_directions: list[str] = Field(default_factory=list)
    stop_required: bool = False
    rest_required: bool = False
    faults: list[str] = Field(default_factory=list)


class RpcRequest(BaseModel):
    id: str
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class RpcResponse(BaseModel):
    id: str
    ok: bool
    result: Any = None
    error: str | None = None


class PlannerAction(BaseModel):
    action: Literal["respond", "tool", "done"]
    text: str | None = None
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None
    affect: Literal["neutral", "warm", "curious", "concern", "alert", "delighted", "reflective"] | None = None
