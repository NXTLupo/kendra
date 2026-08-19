from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BodyDriver(ABC):
    @abstractmethod
    def walk(self, direction: str, steps: int, speed: float) -> dict[str, Any]: ...

    @abstractmethod
    def turn(self, degrees: float, speed: float) -> dict[str, Any]: ...

    @abstractmethod
    def pose(self, name: str) -> dict[str, Any]: ...

    @abstractmethod
    def stop(self) -> dict[str, Any]: ...

    def look(self, pan: float, tilt: float) -> dict[str, Any]:
        raise NotImplementedError("This body does not expose a verified head gimbal")

    def front_distance_cm(self) -> float | None:
        return None

    def battery_voltage(self) -> float | None:
        return None

    def close(self) -> None:
        return None
