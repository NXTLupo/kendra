"""The typed contract between Kendra's mind and her performing body.

Her language model never writes servo values, never picks timings, and
never decides whether moving is safe. It proposes an ExpressionPlan; the
engine validates it and turns it into semantic body calls that the normal
Body abstraction, gait engine and reflex layer already police.

Every field has a safe default, so a half-specified plan still performs
rather than failing — a stiff performance beats an apology.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Semantic gestures. The simulated body, Webots and the RaspClaws bridge
# each translate these; nothing above this layer knows about servos.
GESTURES = frozenset({
    "neutral", "curious_tilt", "small_bounce", "happy_bounce", "thinking_shift",
    "side_to_side_sway", "beat_nod", "shy_retreat", "excited_step",
    "dramatic_freeze", "look_away", "lean_forward", "tiny_bow", "full_bow",
    "stretch", "victory_pose", "surprised_recoil", "slow_turn", "playful_spin",
    "front_leg_tap", "peek_left", "peek_right", "safe_low_pose",
})

# How her voice is coloured. Kokoro has no singing mode, so "singing" and
# "rapping" are achieved with cadence, pitch scaling and lyric shaping.
VOCAL_STYLES = frozenset({
    "normal", "warm", "playful", "whisper", "singing", "humming",
    "rapping", "dramatic", "sleepy", "excited", "dry",
})

LIGHT_BEHAVIORS = frozenset({
    None, "warm_pulse", "beat_pulse", "cool_fade", "bright_flash",
    "slow_breathe", "dim", "rainbow_sweep",
})

MAX_DURATION_S = 30.0


@dataclass(slots=True)
class ExpressionPlan:
    behavior: str
    text: str | None = None
    vocal_style: str = "normal"
    motion_choreography: str = "neutral"
    motion_intensity: float = 0.4
    head_behavior: str | None = None
    light_behavior: str | None = None
    tempo_bpm: int | None = None
    duration_limit_s: float = 10.0
    spontaneity_reason: str | None = None
    interruptible: bool = True
    notes: list[str] = field(default_factory=list)

    def validated(self) -> ExpressionPlan:
        """Clamp into the safe envelope rather than refusing to perform.

        An unknown gesture from the model becomes a neutral one; an
        excessive intensity or duration is clamped. The plan always ends up
        executable, and what was corrected is recorded in notes.
        """
        if self.vocal_style not in VOCAL_STYLES:
            self.notes.append(f"unknown vocal_style {self.vocal_style!r} -> normal")
            self.vocal_style = "normal"
        if self.motion_choreography not in GESTURES:
            self.notes.append(f"unknown gesture {self.motion_choreography!r} -> neutral")
            self.motion_choreography = "neutral"
        if self.head_behavior is not None and self.head_behavior not in GESTURES:
            self.notes.append(f"unknown head gesture {self.head_behavior!r} -> dropped")
            self.head_behavior = None
        if self.light_behavior not in LIGHT_BEHAVIORS:
            self.notes.append(f"unknown light {self.light_behavior!r} -> dropped")
            self.light_behavior = None
        self.motion_intensity = max(0.0, min(1.0, float(self.motion_intensity or 0.0)))
        self.duration_limit_s = max(1.0, min(MAX_DURATION_S, float(self.duration_limit_s or 10.0)))
        if self.tempo_bpm is not None:
            self.tempo_bpm = int(max(50, min(140, self.tempo_bpm)))
        return self

    def as_dict(self) -> dict[str, object]:
        return {
            "behavior": self.behavior,
            "vocal_style": self.vocal_style,
            "motion_choreography": self.motion_choreography,
            "motion_intensity": round(self.motion_intensity, 2),
            "head_behavior": self.head_behavior,
            "light_behavior": self.light_behavior,
            "tempo_bpm": self.tempo_bpm,
            "duration_limit_s": self.duration_limit_s,
            "spontaneity_reason": self.spontaneity_reason,
        }
