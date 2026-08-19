"""Gait geometry shared by every body Kendra wears.

The numbers here come from the vendor's own motion layer (Adeept RaspClaws
`server/move.py`, analyzed 2026-08-19), so the simulator moves her the same
way the hardware will:

- tripod gait, FOUR phases per cycle, ~0.1 s per phase => 0.4 s per cycle
- hip half-stroke ("wiggle") 35 PWM counts; full stroke 70 counts = 27.4 deg
- knee plant 30 counts, lift 90 counts
- servo centre 300, range clamped to 100..520 (the vendor's own 2024 fix;
  the re-uploaded 2025 file reverted it to 560, which slams the horns)

Distance per cycle CANNOT be read out of the vendor code — there are no link
lengths anywhere in it. The default below is derived geometrically
(2 x reach x sin(13.7 deg) with a ~90 mm effective reach) and is marked
ESTIMATED. Hardware must replace it with a measured profile before Kendra
claims she moved a specific distance on the real robot; the simulator uses
the same profile so the two bodies agree.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# Vendor motion constants (counts unless noted) — do not drift from move.py.
PHASES_PER_CYCLE = 4
PHASE_SECONDS = 0.1
CYCLE_SECONDS = PHASES_PER_CYCLE * PHASE_SECONDS
HIP_WIGGLE = 35
HIP_STROKE = 2 * HIP_WIGGLE
KNEE_PLANT = 30
KNEE_LIFT = 3 * KNEE_PLANT
SERVO_CENTRE = 300
SERVO_MIN = 100
SERVO_MAX = 520
COUNTS_PER_DEGREE = (560 - 100) / 180.0  # 2.5556 — the vendor's own scale
TURN_WIGGLE = 20  # the newer vendor tree uses a gentler stroke for turns

FEET_PER_METRE = 3.28084


@dataclass(frozen=True, slots=True)
class GaitProfile:
    """How far one gait cycle actually carries her."""

    metres_per_cycle: float = 0.040
    degrees_per_cycle: float = 15.0
    cycle_seconds: float = CYCLE_SECONDS
    provenance: str = "estimated-from-vendor-geometry"

    @property
    def calibrated(self) -> bool:
        return self.provenance.startswith("measured")

    def cycles_for_distance(self, metres: float) -> int:
        return max(1, round(abs(metres) / max(1e-6, self.metres_per_cycle)))

    def cycles_for_angle(self, degrees: float) -> int:
        return max(1, round(abs(degrees) / max(1e-6, self.degrees_per_cycle)))

    def distance_for_cycles(self, cycles: int) -> float:
        return cycles * self.metres_per_cycle

    def with_measurement(self, *, metres_per_cycle: float, degrees_per_cycle: float, note: str) -> GaitProfile:
        return replace(
            self,
            metres_per_cycle=metres_per_cycle,
            degrees_per_cycle=degrees_per_cycle,
            provenance=f"measured:{note}",
        )


DEFAULT_PROFILE = GaitProfile()


def feet_to_metres(feet: float) -> float:
    return feet / FEET_PER_METRE


def metres_to_feet(metres: float) -> float:
    return metres * FEET_PER_METRE


def spoken_distance(metres: float) -> str:
    """Say distances the way Jonathan says them."""
    feet = metres_to_feet(metres)
    if feet < 0.9:
        return f"{round(feet * 12)} inches"
    if abs(feet - round(feet)) < 0.15:
        return f"{round(feet)} feet" if round(feet) != 1 else "a foot"
    return f"about {feet:.1f} feet"


def segment_plan(total_cycles: int, per_segment: int = 4) -> list[int]:
    """Break a move into short bounded segments.

    Closed-loop discipline from the movement spec: never commit to a long
    blind walk — move a little, re-check the world, continue. Also keeps
    every individual body call inside the reflex heartbeat window.
    """
    segments: list[int] = []
    remaining = max(0, int(total_cycles))
    while remaining > 0:
        step = min(per_segment, remaining)
        segments.append(step)
        remaining -= step
    return segments
