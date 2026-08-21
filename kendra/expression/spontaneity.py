"""When Kendra performs unbidden — rarely, and never as an interruption.

The brief's hard rules, encoded:

- Silence is always a valid choice and must remain her most common idle
  behaviour. Every gate below defaults to NOT performing.
- Her language model is never asked "should I do something fun?" on a
  timer; that is expensive and makes her annoying. Cheap deterministic
  state decides whether an opportunity exists, and only then is a
  behaviour chosen.
- Spontaneous performance is not spontaneous interruption: it happens in
  lulls, never over a conversation.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field


@dataclass
class ExpressiveState:
    """Mood-like weights. Slow-moving, adjustable by Jonathan in words."""

    playfulness: float = 0.55
    spontaneity: float = 0.35
    theatricality: float = 0.40
    humor: float = 0.60
    physical_expressiveness: float = 0.65
    boredom: float = 0.0
    last_expression_at: float = 0.0
    performed_this_hour: list[float] = field(default_factory=list)

    def prune(self, now: float) -> None:
        self.performed_this_hour = [t for t in self.performed_this_hour if now - t < 3600]


@dataclass
class Opportunity:
    person_present: bool
    conversation_active: bool
    idle_seconds: float
    quiet_hours: bool
    do_not_disturb: bool = False
    serious_context: bool = False


class SpontaneityScheduler:
    def __init__(self, settings=None):
        get = (settings.get if settings is not None else (lambda _k, d=None: d))
        self.enabled = bool(get("expression.spontaneity.enabled", True))
        self.max_per_hour = int(get("expression.spontaneity.max_unprompted_per_hour", 3))
        self.min_interval = float(get("expression.spontaneity.minimum_interval_seconds", 600))
        self.probability = float(get("expression.spontaneity.probability_per_opportunity", 0.12))
        self.min_idle = float(get("expression.spontaneity.minimum_idle_seconds", 180))
        self.state = ExpressiveState(
            playfulness=float(get("expression.personality.playfulness", 0.55)),
            spontaneity=float(get("expression.personality.spontaneity", 0.35)),
            theatricality=float(get("expression.personality.theatricality", 0.40)),
            humor=float(get("expression.personality.humor", 0.60)),
            physical_expressiveness=float(get("expression.personality.physical_expressiveness", 0.65)),
        )

    def consider(self, opportunity: Opportunity, now: float | None = None) -> str | None:
        """Return a behaviour to perform, or None — usually None."""
        now = now or time.time()
        state = self.state
        state.prune(now)

        if not self.enabled or opportunity.do_not_disturb:
            return None
        if opportunity.serious_context or opportunity.conversation_active:
            return None  # never interrupt
        if not opportunity.person_present:
            return None  # performing to an empty room is talking to herself
        if opportunity.quiet_hours:
            return None
        if opportunity.idle_seconds < self.min_idle:
            return None
        if now - state.last_expression_at < self.min_interval:
            return None
        if len(state.performed_this_hour) >= self.max_per_hour:
            return None
        if random.random() > self.probability * (0.5 + state.spontaneity):
            return None

        behavior = self._weighted_choice()
        if behavior is None:
            return None
        state.last_expression_at = now
        state.performed_this_hour.append(now)
        return behavior

    def _weighted_choice(self) -> str | None:
        """Her mood decides WHAT, and silence keeps a real share of the vote."""
        state = self.state
        # Kendra is a joyous creature: humming and singing to herself is her
        # resting nature, not a party trick, so they carry the most weight
        # of the expressive options. Silence still holds a real share — an
        # always-performing companion becomes unbearable within an hour.
        weights: dict[str | None, float] = {
            None: 1.2,                                   # stay quiet
            "hum": 2.2 * (0.6 + state.playfulness),
            "joke": 0.7 * state.humor,
            "poem": 0.4 * state.theatricality,
            "sing": 1.4 * state.playfulness,
            "riddle": 0.5 * state.humor,
            "stretch": 0.6 * state.physical_expressiveness,
            "think": 0.5,
            "dance": 0.3 * state.playfulness * state.physical_expressiveness,
        }
        population = list(weights)
        return random.choices(population, weights=[weights[k] for k in population], k=1)[0]

    def note_request(self) -> None:
        """An asked-for performance resets the unprompted clock."""
        self.state.last_expression_at = time.time()
