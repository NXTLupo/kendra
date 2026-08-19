from __future__ import annotations

import pytest

from kendra.agent.tools import ToolFailure, ToolRegistry


@pytest.mark.asyncio
async def test_unknown_tool_rejected(settings):
    registry = ToolRegistry(settings, settings.get("body.capabilities"))
    with pytest.raises(ToolFailure, match="not whitelisted"):
        await registry.execute("shell", {"command": "rm -rf /"})


@pytest.mark.asyncio
async def test_invalid_walk_args_rejected_before_ipc(settings):
    registry = ToolRegistry(settings, settings.get("body.capabilities"))
    with pytest.raises(ToolFailure, match="Invalid arguments"):
        await registry.execute("walk", {"direction": "forward", "steps": 100000, "speed": 0.3})


def test_look_absent_without_gimbal(settings):
    registry = ToolRegistry(settings, settings.get("body.capabilities"))
    assert "look" not in registry.specs


@pytest.mark.asyncio
async def test_voice_upgrade_requires_exact_confirmation(settings):
    registry = ToolRegistry(settings, settings.get("body.capabilities"))
    assert "check_intelligence_upgrade" in registry.specs
    with pytest.raises(ToolFailure, match="Invalid arguments"):
        await registry.execute("request_intelligence_upgrade", {"confirmation": "just install it"})
