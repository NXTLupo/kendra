from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .paths import project_root, resolve_path

REQUIRED_HARDWARE_GATES = (
    "servo_mapping_verified",
    "battery_path_verified",
    "e_stop_verified",
    "cliff_array_verified",
    "motion_calibrated",
)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class Settings:
    def __init__(self, data: dict[str, Any], root: Path):
        self.data = data
        self.root = root

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> Settings:
        root = project_root()
        default_path = root / "config" / "default.yaml"
        with default_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}

        candidate = config_path or os.getenv("KENDRA_CONFIG")
        if candidate:
            local_path = resolve_path(candidate, root)
            if local_path.exists():
                with local_path.open("r", encoding="utf-8") as handle:
                    data = _deep_merge(data, yaml.safe_load(handle) or {})
        else:
            local_path = root / "config" / "local.yaml"
            hardware_path = root / "config" / "hardware.local.yaml"
            for path in (local_path, hardware_path):
                if path.exists():
                    with path.open("r", encoding="utf-8") as handle:
                        data = _deep_merge(data, yaml.safe_load(handle) or {})

        return cls(data=data, root=root)

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.data
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

    def require(self, dotted: str) -> Any:
        value = self.get(dotted, None)
        if value is None:
            raise KeyError(f"Missing required setting: {dotted}")
        return value

    def path(self, dotted: str) -> Path:
        return resolve_path(self.require(dotted), self.root)

    @property
    def runtime_dir(self) -> Path:
        env = os.getenv("KENDRA_RUNTIME_DIR")
        path = Path(env) if env else self.path("paths.runtime_dir")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def socket_path(self, section: str) -> Path:
        return self.runtime_dir / str(self.require(f"{section}.socket"))

    def assert_hardware_gates(self) -> None:
        if self.get("project.mode") != "hardware":
            return
        gates = self.get("hardware_gates", {})
        if not isinstance(gates, dict):
            gates = {}
        missing = [name for name in REQUIRED_HARDWARE_GATES if gates.get(name) is not True]
        if missing:
            raise RuntimeError(
                "Real hardware mode is fail-closed. Complete these hard gates first: "
                + ", ".join(sorted(missing))
            )

    def hardware_gates_passed(self) -> bool:
        gates = self.get("hardware_gates", {})
        return isinstance(gates, dict) and all(
            gates.get(name) is True for name in REQUIRED_HARDWARE_GATES
        )
