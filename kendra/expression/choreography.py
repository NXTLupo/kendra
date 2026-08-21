"""Semantic gestures -> safe body calls. Never servo values.

Each routine is a short sequence of the SAME verbs the body service
already exposes (pose/walk/turn/look), so everything here inherits the
reflex lock, motion budgets and fail-closed hardware gates for free. The
simulated body, Webots and the RaspClaws bridge all understand them.

Intentional imperfection: several gestures pick among variants and jitter
their timing slightly, because a performance repeated identically reads as
a machine animation rather than a creature.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Protocol

LOG = logging.getLogger(__name__)


class BodyLike(Protocol):
    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any: ...


async def _safe(body: BodyLike, method: str, params: dict[str, Any]) -> None:
    """Movement is decoration: it must never break the performance."""
    try:
        await body.call(method, params)
    except Exception:
        LOG.debug("gesture step %s skipped", method, exc_info=True)


def _jitter(value: float, spread: float = 0.05) -> float:
    return max(0.0, value * (1.0 + random.uniform(-spread, spread)))


async def neutral(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "pose", {"name": "neutral"})


async def curious_tilt(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "look", {"pan": 8 * intensity, "tilt": 5 * intensity})
    await asyncio.sleep(_jitter(0.4))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def small_bounce(body: BodyLike, intensity: float = 0.4) -> None:
    for _ in range(2):
        await _safe(body, "pose", {"name": "alert"})
        await asyncio.sleep(_jitter(0.18))
        await _safe(body, "pose", {"name": "neutral"})
        await asyncio.sleep(_jitter(0.18))


async def happy_bounce(body: BodyLike, intensity: float = 0.5) -> None:
    for _ in range(2 + int(intensity * 2)):
        await _safe(body, "pose", {"name": "alert"})
        await asyncio.sleep(_jitter(0.15))
        await _safe(body, "pose", {"name": "stretch" if random.random() < 0.3 else "neutral"})
        await asyncio.sleep(_jitter(0.15))


async def thinking_shift(body: BodyLike, intensity: float = 0.3) -> None:
    await _safe(body, "look", {"pan": -6 * intensity, "tilt": 3 * intensity})
    await asyncio.sleep(_jitter(0.6))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def side_to_side_sway(body: BodyLike, intensity: float = 0.4) -> None:
    degrees = max(4.0, 12.0 * intensity)
    for _ in range(2):
        await _safe(body, "turn", {"degrees": -degrees, "speed": 0.2})
        await asyncio.sleep(_jitter(0.2))
        await _safe(body, "turn", {"degrees": degrees, "speed": 0.2})
        await asyncio.sleep(_jitter(0.2))


async def beat_nod(body: BodyLike, intensity: float = 0.5) -> None:
    for _ in range(4):
        await _safe(body, "look", {"pan": 0, "tilt": -6 * intensity})
        await asyncio.sleep(_jitter(0.16))
        await _safe(body, "look", {"pan": 0, "tilt": 4 * intensity})
        await asyncio.sleep(_jitter(0.16))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def shy_retreat(body: BodyLike, intensity: float = 0.3) -> None:
    await _safe(body, "walk", {"direction": "backward", "steps": 1, "speed": 0.18})
    await _safe(body, "look", {"pan": 12, "tilt": 0})
    await asyncio.sleep(_jitter(0.4))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def excited_step(body: BodyLike, intensity: float = 0.6) -> None:
    await _safe(body, "walk", {"direction": "forward", "steps": 1, "speed": 0.3})
    await small_bounce(body, intensity)


async def dramatic_freeze(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "pose", {"name": "alert"})
    await asyncio.sleep(_jitter(1.1))


async def look_away(body: BodyLike, intensity: float = 0.3) -> None:
    side = random.choice([-1, 1])
    await _safe(body, "look", {"pan": 20 * intensity * side, "tilt": 0})
    await asyncio.sleep(_jitter(0.7))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def lean_forward(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "look", {"pan": 0, "tilt": -8 * intensity})
    await asyncio.sleep(_jitter(0.5))


async def tiny_bow(body: BodyLike, intensity: float = 0.3) -> None:
    await _safe(body, "look", {"pan": 0, "tilt": -12 * intensity})
    await asyncio.sleep(_jitter(0.45))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def full_bow(body: BodyLike, intensity: float = 0.6) -> None:
    await _safe(body, "pose", {"name": "rest"})
    await asyncio.sleep(_jitter(0.9))
    await _safe(body, "pose", {"name": "neutral"})


async def stretch(body: BodyLike, intensity: float = 0.5) -> None:
    await _safe(body, "pose", {"name": "stretch"})
    await asyncio.sleep(_jitter(1.0))
    await _safe(body, "pose", {"name": "neutral"})


async def victory_pose(body: BodyLike, intensity: float = 0.7) -> None:
    await _safe(body, "pose", {"name": "alert"})
    await happy_bounce(body, intensity)
    await _safe(body, "pose", {"name": "neutral"})


async def surprised_recoil(body: BodyLike, intensity: float = 0.5) -> None:
    await _safe(body, "walk", {"direction": "backward", "steps": 1, "speed": 0.35})
    await _safe(body, "pose", {"name": "alert"})
    await asyncio.sleep(_jitter(0.3))


async def slow_turn(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "turn", {"degrees": 35 * intensity, "speed": 0.15})


async def playful_spin(body: BodyLike, intensity: float = 0.6) -> None:
    await _safe(body, "turn", {"degrees": 45, "speed": 0.3})
    await _safe(body, "turn", {"degrees": -45, "speed": 0.3})


async def front_leg_tap(body: BodyLike, intensity: float = 0.4) -> None:
    for _ in range(3):
        await _safe(body, "pose", {"name": "alert"})
        await asyncio.sleep(_jitter(0.14))
        await _safe(body, "pose", {"name": "neutral"})
        await asyncio.sleep(_jitter(0.14))


async def peek_left(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "look", {"pan": -18 * intensity, "tilt": 0})
    await asyncio.sleep(_jitter(0.5))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def peek_right(body: BodyLike, intensity: float = 0.4) -> None:
    await _safe(body, "look", {"pan": 18 * intensity, "tilt": 0})
    await asyncio.sleep(_jitter(0.5))
    await _safe(body, "look", {"pan": 0, "tilt": 0})


async def safe_low_pose(body: BodyLike, intensity: float = 0.3) -> None:
    # "Dramatic collapse" is a POSE, never a power-off. Hardware is never
    # dropped for a joke.
    await _safe(body, "pose", {"name": "rest"})
    await asyncio.sleep(_jitter(1.2))
    await _safe(body, "pose", {"name": "neutral"})


ROUTINES = {
    "neutral": neutral, "curious_tilt": curious_tilt, "small_bounce": small_bounce,
    "happy_bounce": happy_bounce, "thinking_shift": thinking_shift,
    "side_to_side_sway": side_to_side_sway, "beat_nod": beat_nod,
    "shy_retreat": shy_retreat, "excited_step": excited_step,
    "dramatic_freeze": dramatic_freeze, "look_away": look_away,
    "lean_forward": lean_forward, "tiny_bow": tiny_bow, "full_bow": full_bow,
    "stretch": stretch, "victory_pose": victory_pose,
    "surprised_recoil": surprised_recoil, "slow_turn": slow_turn,
    "playful_spin": playful_spin, "front_leg_tap": front_leg_tap,
    "peek_left": peek_left, "peek_right": peek_right, "safe_low_pose": safe_low_pose,
}


async def perform(body: BodyLike, gesture: str, intensity: float = 0.4) -> None:
    routine = ROUTINES.get(gesture, neutral)
    await routine(body, intensity)
