from __future__ import annotations

from kendra.leds.service import SystemLightState, resolve_light


def test_reflex_has_priority_over_expression():
    state = SystemLightState(reflex_fault=True, thinking=True, expression="warm")
    resolved = resolve_light(state)
    assert resolved["semantic"] == "red"
    assert resolved["reason"] == "reflex_fault"


def test_thinking_only_when_system_sets_it():
    state = SystemLightState(thinking=True, expression="alert")
    assert resolve_light(state)["semantic"] == "cyan"
