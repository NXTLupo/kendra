from __future__ import annotations

import pytest


def test_hardware_mode_fails_closed(settings):
    settings.data["project"]["mode"] = "hardware"
    with pytest.raises(RuntimeError, match="hard gates"):
        settings.assert_hardware_gates()


def test_hardware_mode_allows_only_all_true(settings):
    settings.data["project"]["mode"] = "hardware"
    for key in settings.data["hardware_gates"]:
        settings.data["hardware_gates"][key] = True
    settings.assert_hardware_gates()


def test_hardware_mode_rejects_incomplete_gate_map(settings):
    settings.data["project"]["mode"] = "hardware"
    settings.data["hardware_gates"] = {}
    assert settings.hardware_gates_passed() is False
    with pytest.raises(RuntimeError, match="servo_mapping_verified"):
        settings.assert_hardware_gates()
