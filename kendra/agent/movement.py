"""Spoken movement commands -> typed intents -> her body.

The language model never writes servo values and never decides whether it
is safe to move. It only ever produces (or, here, is bypassed in favour of)
a typed MovementIntent that the body service and the reflex layer can
reason about. Parsing is deterministic regex so "stop" costs no inference
at all.

Vocabulary Jonathan asked for:
    come here / go away / back up / go to <target> / turn left / turn right
    turn around / turn to your left / turn to your right / go forward
    stop / go forward about 4 feet / how far away is that
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..body.locomotion import feet_to_metres
from ..phrasing import LEAD_IN, REQUEST_OPENERS

Mode = Literal["forward", "backward", "turn", "approach", "retreat", "goto", "sidestep", "stop"]

_NUMBER_WORDS = {
    "a": 1.0, "an": 1.0, "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0,
    "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0,
    "ten": 10.0, "eleven": 11.0, "twelve": 12.0, "fifteen": 15.0, "twenty": 20.0,
    "half": 0.5, "couple": 2.0, "few": 3.0,
}

_DISTANCE = re.compile(
    r"(?P<value>\d+(?:\.\d+)?|" + "|".join(_NUMBER_WORDS) + r")\s*"
    r"(?P<unit>feet|foot|ft|inches|inch|in\b|meters?|metres?|m\b|steps?|paces?)",
    re.I,
)
_ANGLE = re.compile(r"(?P<value>\d{1,3})\s*(?:degrees?|deg)\b", re.I)

# Ordered most-specific-first; the first match wins.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Stop is a SAFETY word: it matches anywhere in the utterance ("no
    # matter what, stop"), and only an explicit negation nearby ("don't
    # stop") disarms it. Halting on a false positive is harmless; missing a
    # real stop is not.
    ("stop", re.compile(r"\b(?:stop|halt|freeze|hold still)\b", re.I)),
    ("turn_around", re.compile(r"\bturn (?:yourself )?around\b|\bspin around\b|\bface (?:the )?other way\b", re.I)),
    ("turn_left", re.compile(r"\bturn (?:to your |to the )?left\b|\bgo left\b|\bswing left\b|\bleft turn\b", re.I)),
    ("turn_right", re.compile(r"\bturn (?:to your |to the )?right\b|\bgo right\b|\bswing right\b|\bright turn\b", re.I)),
    ("sidestep_left", re.compile(
        r"\b(?:move|walk|go|step|scoot|shift|slide|shuffle|shimmy)\s+(?:over\s+)?(?:to\s+)?(?:the\s+|your\s+|my\s+)?left\b"
        r"|\bmove over to the left\b|\bto your left\b", re.I)),
    ("sidestep_right", re.compile(
        r"\b(?:move|walk|go|step|scoot|shift|slide|shuffle|shimmy)\s+(?:over\s+)?(?:to\s+)?(?:the\s+|your\s+|my\s+)?right\b"
        r"|\bmove over to the right\b|\bto your right\b", re.I)),
    ("come", re.compile(r"\bcome (?:here|to me|over here|closer|here girl)\b|\bcome on over\b|\bover here\b", re.I)),
    ("away", re.compile(r"\bgo away\b|\bback off\b|\bgive me (?:some )?space\b|\bmove away\b|\bshoo\b", re.I)),
    ("backward", re.compile(
        r"\b(?:back up|backup|back away|reverse"
        r"|(?:go|move|walk|crawl|step|come|scoot|take a step) back(?:wards?)?)\b",
        re.I)),
    ("goto", re.compile(
        r"\b(?:go|walk|head|crawl) (?:to|toward|towards|over to)\s+"
        r"(?:the\s+|my\s+|that\s+)?(?P<target>[a-z][a-z\s'-]{1,30})", re.I)),
    ("forward", re.compile(r"\b(?:go|move|walk|crawl|step|head)\s+(?:forward|ahead|straight|up)\b|\bcome forward\b", re.I)),
]


@dataclass(slots=True)
class MovementIntent:
    mode: Mode
    distance_m: float | None = None
    angle_deg: float | None = None
    target: str | None = None
    speed: str = "normal"
    requires_vision: bool = False
    raw: str = ""
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "distance_m": self.distance_m,
            "angle_deg": self.angle_deg,
            "target": self.target,
            "speed": self.speed,
            "requires_vision": self.requires_vision,
        }


def _parse_distance(text: str) -> float | None:
    match = _DISTANCE.search(text)
    if not match:
        return None
    raw = match.group("value").casefold()
    value = _NUMBER_WORDS.get(raw)
    if value is None:
        try:
            value = float(raw)
        except ValueError:
            return None
    unit = match.group("unit").casefold()
    if unit.startswith(("foot", "feet", "ft")):
        return feet_to_metres(value)
    if unit.startswith(("inch", "in")):
        return feet_to_metres(value / 12.0)
    if unit.startswith(("meter", "metre", "m")):
        return value
    # "steps"/"paces" are her own gait cycles, resolved by the body service.
    return None


# What may precede a movement verb in a genuine command: politeness and
# address, nothing else. "I might walk to the store later" is conversation;
# a robot that starts walking at hypothetical speech is a hazard.
_IMPERATIVE_PREFIX = re.compile(
    # Shared with the expressive detector so the two cannot disagree again:
    # "Now back up" moved her while "Just walk forward" did not.
    rf"^{LEAD_IN}(?:(?:{REQUEST_OPENERS})[,!.\s]*)?$",
    re.I,
)


def parse_movement(text: str) -> MovementIntent | None:
    """Deterministic movement parse; None means 'not a movement command'."""
    if not text or not text.strip():
        return None
    stripped = text.strip()
    for name, pattern in _PATTERNS:
        match = pattern.search(stripped)
        if not match:
            continue
        if name != "stop" and not _IMPERATIVE_PREFIX.match(stripped[: match.start()]):
            # A movement verb buried mid-sentence is narration, not a command
            # ("I might walk to the store later"). Stop stays hair-trigger.
            continue
        distance = _parse_distance(stripped)
        angle_match = _ANGLE.search(stripped)
        angle = float(angle_match.group("value")) if angle_match else None
        if name == "stop":
            if re.search(r"\b(?:don'?t|do not|never|won'?t|not)\s+(?:\w+\s+)?(?:stop|halt|freeze)\b", stripped, re.I):
                continue
            return MovementIntent("stop", raw=stripped)
        if name == "turn_around":
            return MovementIntent("turn", angle_deg=angle or 180.0, raw=stripped)
        if name == "turn_left":
            return MovementIntent("turn", angle_deg=-(angle or 90.0), raw=stripped)
        if name == "turn_right":
            return MovementIntent("turn", angle_deg=angle or 90.0, raw=stripped)
        if name in {"sidestep_left", "sidestep_right"}:
            # The RaspClaws gait has no true strafe: the vendor's only
            # lateral primitive is turn-in-place. So "move to the left" is
            # an honest three-part shuffle — face that way, take a couple of
            # steps, face back — which is what a real hexapod would do.
            return MovementIntent(
                "sidestep",
                distance_m=distance or 0.25,
                angle_deg=-75.0 if name == "sidestep_left" else 75.0,
                speed="slow",
                raw=stripped,
            )
        if name == "come":
            return MovementIntent(
                "approach", target="Jonathan", distance_m=distance,
                requires_vision=True, speed="slow", raw=stripped,
            )
        if name == "away":
            return MovementIntent(
                "retreat", distance_m=distance or 0.6, speed="slow", raw=stripped,
            )
        if name == "backward":
            return MovementIntent("backward", distance_m=distance, speed="slow", raw=stripped)
        if name == "goto":
            target = " ".join(str(match.group("target")).split())
            target = re.split(r"\b(?:and|then|please|about)\b", target)[0].strip(" .,!?")
            return MovementIntent(
                "goto", target=target or None, distance_m=distance,
                requires_vision=True, raw=stripped,
            )
        if name == "forward":
            return MovementIntent("forward", distance_m=distance, raw=stripped)
    return None


def announce(intent: MovementIntent) -> str:
    """What she says before she moves — warm, short, never technical.

    Spoken BEFORE the body starts so Jonathan is never surprised by a robot
    that just lurches. One sentence, her own voice, no telemetry.
    """
    from ..body.locomotion import spoken_distance

    distance = spoken_distance(intent.distance_m) if intent.distance_m else None
    if intent.mode == "stop":
        return "Stopping."
    if intent.mode == "approach":
        return "Okay, coming over!"
    if intent.mode == "retreat":
        return "Okay, I'll give you some space."
    if intent.mode == "backward":
        return f"Backing up {distance}." if distance else "Backing up a little."
    if intent.mode == "goto":
        return f"Heading over to the {intent.target}." if intent.target else "On my way."
    if intent.mode == "turn":
        angle = intent.angle_deg or 90.0
        if abs(angle) >= 170:
            return "Turning around."
        return "Turning left." if angle < 0 else "Turning right."
    if intent.mode == "sidestep":
        side = "left" if (intent.angle_deg or 0) < 0 else "right"
        return f"Scooting over to the {side}."
    if intent.mode == "forward":
        return f"Walking forward {distance}." if distance else "Walking forward."
    return "On my way."


def arrival(intent: MovementIntent, moved_m: float | None = None, blocked: str | None = None) -> str:
    """What she says when the move finishes (or stops early)."""
    from ..body.locomotion import spoken_distance

    if blocked:
        return f"I stopped — {blocked}."
    if intent.mode == "approach":
        return "Here I am!"
    if intent.mode == "retreat":
        return "Far enough?"
    if intent.mode == "turn":
        return "Okay, turned."
    if moved_m:
        return f"That's about {spoken_distance(moved_m)}."
    return "Done."
