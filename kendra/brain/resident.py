"""What Kendra knows without having to think about it.

Everything else she owns is RETRIEVED: searched for, scored, and merged into
the turn that needed it. That is the right design for a 1.7B model with a
small context — but it made three things fragile that must never be, because
retrieval here is lexical and those three are asked about in pure stop-words.

Measured on her real brain:

    "who am I"        -> 0 memories
    "do you know me"  -> 0 memories
    "who are you"     -> 0 memories
    "what is my name" -> 3 memories   (only because "name" is a content word)

The facts were all there. "jonathan — name: Jonathan — Kendra's creator and
companion" sat in her slot-store the whole time. She simply could not reach it
from the question, and answered a man she has spoken to for a week as though
she had never met him.

So three things are RESIDENT — always in front of her, never searched for:

    1. Herself      name, what she is, what she can do, what interests her
    2. Her people   who she knows and how they relate to her
    3. Today        what has been discussed and looked up since she woke

Everything else stays retrieved. This block is deliberately small: it is paid
on every single turn, and prefill is the felt latency of her whole voice path.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

#: How long a built block is reused. These change on the scale of minutes at
#: most, and rebuilding costs three small queries.
CACHE_SECONDS = 45.0


def _self_facts(store: Any) -> dict[str, str]:
    """Who she is, from her SELF slots — which come from her charter.

    This used to read `self_model`, whose `identity` was one line written by
    `kendra init` on the day the repository was created and never revisited:
    "small hexapod robot and intellectual companion". That is not what her
    charter says. It describes a search appliance with legs, and it was what
    she had in front of her when asked who she was.
    """
    try:
        slots = store.slots_for("kendra")
    except Exception:
        slots = {}
    out = {key: str(entry["value"]) for key, entry in slots.items()}
    if "identity" not in out:
        model = store.self_model() or {}
        entry = model.get("identity")
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value:
            out["identity"] = str(value)
    return out


def _people(store: Any, limit: int = 6) -> list[dict[str, str]]:
    """Who she knows, from the slot store: exact, typed, already compact.

    The slot store is the right source precisely because it is not semantic.
    "Who am I" must not depend on a similarity score. Stale slots are withheld
    by `slots_for` — a preference stated months ago is not a fact about
    someone today.
    """
    try:
        subjects = [name for name in store.slot_subjects() if name != "kendra"]
    except Exception:
        return []
    people: list[dict[str, str]] = []
    for subject in subjects[:limit]:
        try:
            slots = store.slots_for(subject)
        except Exception:
            continue
        if not slots:
            continue
        name = slots.get("name", {}).get("value", subject.title())
        description = name.split("—", 1)[-1].strip() if "—" in name else ""
        entry: dict[str, str] = {"person": subject.title(), "who": description or name}
        # Everything else she holds about them, verbatim from the contract.
        detail = [
            f"{key.replace('_', ' ')}: {value['value']}"
            for key, value in sorted(slots.items())
            if key not in {"name", "relationship"}
        ]
        if detail:
            entry["known"] = "; ".join(detail)
        people.append(entry)
    return people


def _today(store: Any, limit: int = 8) -> dict[str, Any]:
    """What has actually happened since she woke: topics and lookups.

    Not the transcript. The transcript is the session's rolling history and is
    handled separately; this is the shape of the day, so that "what were we
    talking about" is answerable without a search.
    """
    start = datetime.now(UTC).strftime("%Y-%m-%d")
    topics: list[str] = []
    searches: list[str] = []
    try:
        rows = store.conn.execute(
            "SELECT kind, content FROM memories "
            "WHERE active=1 AND created_at >= ? AND kind IN ('fact','user_stated','insight') "
            "ORDER BY id DESC LIMIT ?",
            (start, limit * 3),
        ).fetchall()
        for kind, content in rows:
            text = str(content).strip()
            if not text:
                continue
            (searches if kind == "fact" else topics).append(text[:110])
    except Exception:
        return {}
    return {
        "discussed": topics[:limit],
        "looked_up": searches[: max(2, limit // 2)],
    }


def build(store: Any) -> dict[str, Any]:
    """The three resident blocks, freshly assembled."""
    identity = _self_facts(store)
    try:
        # Weight-gated. The interests table also collects one-off research
        # subjects, so an ungated list had her permanently "interested in
        # president of the united states" -- a headline she looked up once.
        interests = [
            str(row["topic"])
            for row in store.interests()
            if float(row["weight"] or 0) >= 0.5
        ][:6]
    except Exception:
        interests = []
    return {
        "you_are": {
            "name": "Kendra",
            "what": identity.get("identity", "a hexapod robot with a mind of my own"),
            "character": identity.get("character", ""),
            "expertise": identity.get("expertise", ""),
            "interested_in": interests,
        },
        "people_you_know": _people(store),
        "today": _today(store),
    }


def render(blocks: dict[str, Any], budget_chars: int = 900) -> str:
    """One compact block, structured rather than prose.

    Structured because it is read by a 1.7B: measured on this exact model,
    memories carrying an explicit subject were used 6 times in 6 where a prose
    list was used 2 times in 6. Names are stated, never implied.
    """
    you = blocks.get("you_are", {})
    lines = ["WHAT YOU KNOW WITHOUT LOOKING IT UP.", f"You are Kendra: {you.get('what', '')}."]
    if you.get("character"):
        lines.append(f"You are {you['character']}.")
    if you.get("expertise"):
        lines.append(f"You know {you['expertise']}.")
    if you.get("interested_in"):
        lines.append("Currently interested in: " + ", ".join(you["interested_in"]) + ".")
    people = blocks.get("people_you_know") or []
    if people:
        lines.append("People you know:")
        for person in people:
            line = f"- {person['person']}: {person.get('who', '')}"
            if person.get("known"):
                line += f" ({person['known']})"
            lines.append(line)
    today = blocks.get("today") or {}
    if today.get("discussed"):
        lines.append("Today you have talked about:")
        lines += [f"- {item}" for item in today["discussed"]]
    if today.get("looked_up"):
        lines.append("Today you looked up:")
        lines += [f"- {item}" for item in today["looked_up"]]
    text = "\n".join(lines)
    if len(text) <= budget_chars:
        return text
    # Trim from the end: today is the most expendable, who she is never is.
    trimmed: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > budget_chars:
            break
        trimmed.append(line)
        used += len(line) + 1
    return "\n".join(trimmed)


class ResidentContext:
    """Builds and briefly caches the block, so a turn pays almost nothing."""

    def __init__(self, store: Any, cache_seconds: float = CACHE_SECONDS):
        self.store = store
        self.cache_seconds = cache_seconds
        self._text = ""
        self._at = 0.0

    def text(self, budget_chars: int = 900) -> str:
        now = time.monotonic()
        if self._text and now - self._at < self.cache_seconds:
            return self._text
        try:
            self._text = render(build(self.store), budget_chars)
        except Exception:
            # She must still be able to speak if this fails.
            self._text = ""
        self._at = now
        return self._text
