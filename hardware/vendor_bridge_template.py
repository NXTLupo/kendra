"""Template for the one hardware-specific file Kendra cannot safely pre-fill.

Do not copy this into /opt/kendra-hardware until the current RaspClaws-Metal
resource package has been inspected and the 17-servo/16-channel discrepancy has
been resolved on your actual kit.

The rest of Kendra never imports Adeept vendor modules directly. Once verified,
implement these functions by calling the exact vendor APIs from the current
resource package. Keep raw channel numbers here, not in the agent.
"""


def walk(*, direction: str, steps: int, speed: float) -> dict:
    raise RuntimeError("UNVERIFIED: implement from the current Adeept Metal motion API")


def turn(*, degrees: float, speed: float) -> dict:
    raise RuntimeError("UNVERIFIED: implement from the current Adeept Metal motion API")


def pose(*, name: str) -> dict:
    raise RuntimeError("UNVERIFIED: implement from the current Adeept Metal motion API")


def stop() -> dict:
    raise RuntimeError("UNVERIFIED: implement the vendor's immediate servo stop")


def front_distance_cm() -> float | None:
    return None


def battery_voltage() -> float | None:
    return None
