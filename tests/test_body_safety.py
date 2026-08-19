from __future__ import annotations

import time

import pytest

from kendra.body.service import BodyService, build_driver
from kendra.protocol import ReflexState


@pytest.mark.asyncio
async def test_body_rejects_missing_reflex(settings):
    service = BodyService(settings)
    with pytest.raises(RuntimeError, match="missing"):
        await service.walk("forward", 1, 0.3)


@pytest.mark.asyncio
async def test_body_rejects_stale_reflex(settings):
    service = BodyService(settings)
    state = ReflexState(heartbeat_monotonic=time.monotonic() - 10, healthy=True)
    service.reflex_state_file.write_text(state.model_dump_json(), encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        await service.walk("forward", 1, 0.3)


@pytest.mark.asyncio
async def test_body_clamps_steps(settings):
    service = BodyService(settings)
    state = ReflexState(heartbeat_monotonic=time.monotonic(), healthy=True)
    service.reflex_state_file.write_text(state.model_dump_json(), encoding="utf-8")
    result = await service.walk("forward", 999, 0.99)
    assert result["steps"] == settings.get("body.max_steps_per_call")
    assert result["speed"] == settings.get("body.speed_max")


@pytest.mark.asyncio
async def test_body_rejects_future_monotonic_reflex_from_other_boot(settings):
    service = BodyService(settings)
    state = ReflexState(heartbeat_monotonic=time.monotonic() + 1000, healthy=True)
    service.reflex_state_file.write_text(state.model_dump_json(), encoding="utf-8")
    with pytest.raises(RuntimeError, match="stale"):
        await service.walk("forward", 1, 0.3)


def test_physical_driver_rejected_outside_hardware_mode(settings):
    settings.data["body"]["driver"] = "raspclaws"
    with pytest.raises(RuntimeError, match="project.mode=hardware"):
        build_driver(settings)


@pytest.mark.asyncio
async def test_body_rejects_unverified_pose(settings):
    service = BodyService(settings)
    state = ReflexState(heartbeat_monotonic=time.monotonic(), healthy=True)
    service.reflex_state_file.write_text(state.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="not verified/allowlisted"):
        await service.handle("pose", {"name": "invented-servo-pose"})
