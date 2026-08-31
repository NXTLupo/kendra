"""The slot store's contract: which facts are slots, and what they mean.

Her slot store is the exact, typed tier consulted before any embedding — the
one place a fact can be recalled with no similarity score involved. That makes
it the right home for the handful of truths she must never get wrong, and the
wrong home for everything else.

It was operating without a contract, and an audit against the pattern showed
four of its rules broken:

    Fixed keys          FAIL  keys were freeform: "favorite music",
                              "guitar teacher focus" — spaces and all
    Stateful writes     FAIL  only two call sites; every slot in her brain was
                              written on one day and never touched again
    Strict schema       FAIL  set_fact accepted any string as a key
    Prune / deprecate   FAIL  nothing ever expired, so a five-day-old
                              preference was indistinguishable from a fresh one

This module is that contract. A slot must be declared here to be written, so
the model can never invent `jonathans_current_mood_probably` and have it
injected forever after.

WHAT BELONGS HERE. High-signal atomic truths, stable enough to still be true
next turn, that she must recall exactly: names, relationships, standing
preferences, the goal in front of her. Conversational chatter does not belong;
neither does anything with a story attached. Those are memories, and the
compiled wiki is where the deep version lives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

#: Slots whose subject is a person she knows.
PERSON = "person"
#: Slots about Kendra herself.
SELF = "self"
#: Slots describing the work in front of her right now.
SESSION = "session"


@dataclass(frozen=True, slots=True)
class Slot:
    key: str
    scope: str
    description: str
    #: How long this stays trustworthy without being rewritten. None never
    #: expires. A preference someone stated once a week ago is not a fact
    #: about them today, and acting on it confidently is worse than admitting
    #: it is old.
    ttl_days: float | None = None


SLOTS: dict[str, Slot] = {
    slot.key: slot
    for slot in (
        # --- people -------------------------------------------------------
        Slot("name", PERSON, "How she refers to them, plus who they are to her"),
        Slot("relationship", PERSON, "How she knows them and how she recognises them"),
        Slot("spouse", PERSON, "Their partner's name"),
        Slot("pronouns", PERSON, "How to refer to them"),
        Slot("favourite_music", PERSON, "Standing musical preference", ttl_days=90),
        Slot("plays_instrument", PERSON, "An instrument they play", ttl_days=180),
        Slot("current_focus", PERSON, "What they are working on at the moment", ttl_days=14),
        Slot("occupation", PERSON, "What they do", ttl_days=365),
        Slot("location", PERSON, "Where they live or are", ttl_days=30),
        # --- herself ------------------------------------------------------
        #
        # These come from her charter, which is the source of truth for who
        # she is. They were previously a single line seeded by `kendra init`
        # and never revisited -- "small hexapod robot and intellectual
        # companion" -- which is not what the charter says and not who she
        # is. It described a search appliance with legs.
        Slot("identity", SELF, "What she is, in her own terms"),
        Slot("character", SELF, "How she carries herself"),
        Slot("expertise", SELF, "What she actually knows deeply"),
        Slot("body", SELF, "The body she is in or being built"),
        Slot("brain", SELF, "The model currently serving her thoughts"),
        Slot("voice", SELF, "The voice she speaks with"),
        # --- the work in front of her ------------------------------------
        Slot("active_goal", SESSION, "What she is currently trying to do", ttl_days=1),
        Slot("last_decision", SESSION, "The most recent thing they decided together", ttl_days=1),
    )
}

#: Freeform keys already in her brain, mapped onto the contract. Written
#: before the schema existed; migrated rather than discarded, because the
#: values are real things Jonathan told her.
LEGACY_KEYS: dict[str, str] = {
    "favorite music": "favourite_music",
    "favourite music": "favourite_music",
    "guitar teacher focus": "current_focus",
    "wife": "spouse",
    "husband": "spouse",
    "partner": "spouse",
}


def normalise(key: str) -> str:
    """Canonical slot name for a proposed key, or '' if it is not a slot."""
    raw = str(key or "").strip().casefold()
    if not raw:
        return ""
    if raw in SLOTS:
        return raw
    if raw in LEGACY_KEYS:
        return LEGACY_KEYS[raw]
    snake = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    if snake in SLOTS:
        return snake
    return LEGACY_KEYS.get(snake, "")


def is_slot(key: str) -> bool:
    return bool(normalise(key))


def stale(key: str, updated_at: str, now: datetime | None = None) -> bool:
    """Has this slot outlived the point where she should assert it?

    Nothing is deleted for being stale — the value is still true history. It
    simply stops being injected as current fact, which is the difference
    between "you like heavy metal" and "you told me last month you liked
    heavy metal".
    """
    slot = SLOTS.get(normalise(key))
    if slot is None or slot.ttl_days is None:
        return False
    try:
        written = datetime.fromisoformat(str(updated_at))
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return reference - written > timedelta(days=slot.ttl_days)


def describe() -> list[dict[str, Any]]:
    """The contract, for `kendra brain slots` and for the manifest."""
    return [
        {
            "key": slot.key,
            "scope": slot.scope,
            "description": slot.description,
            "ttl_days": slot.ttl_days,
        }
        for slot in sorted(SLOTS.values(), key=lambda s: (s.scope, s.key))
    ]
