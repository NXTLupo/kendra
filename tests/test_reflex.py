from __future__ import annotations

from kendra.protocol import CliffState
from kendra.reflex.controller import ReflexController
from kendra.reflex.sensors import SensorSnapshot


def controller():
    return ReflexController(
        obstacle_hard_stop_cm=18,
        low_battery_voltage=7.0,
        critical_battery_voltage=6.6,
        max_continuous_motion_seconds=8,
        minimum_rest_seconds=4,
    )


def test_front_cliff_blocks_forward_and_turn():
    state = controller().evaluate(SensorSnapshot(cliff=CliffState(fl=True), front_cm=100, battery_voltage=8))
    assert state.stop_required
    assert "forward" in state.blocked_directions
    assert "left" in state.blocked_directions
    assert "turn" in state.blocked_directions


def test_rear_cliff_blocks_backward():
    state = controller().evaluate(SensorSnapshot(cliff=CliffState(rr=True), front_cm=100, battery_voltage=8))
    assert "backward" in state.blocked_directions
    assert "right" in state.blocked_directions


def test_obstacle_stops_and_blocks_forward():
    state = controller().evaluate(SensorSnapshot(cliff=CliffState(), front_cm=10, battery_voltage=8))
    assert state.stop_required
    assert "forward" in state.blocked_directions
    assert "obstacle_hard_stop" in state.faults


def test_critical_battery_requires_stop():
    state = controller().evaluate(SensorSnapshot(cliff=CliffState(), front_cm=100, battery_voltage=6.5))
    assert state.stop_required
    assert state.battery.state == "critical"
