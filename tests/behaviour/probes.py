"""What "better" means for Kendra, written down and scored.

Her 111 unit tests all pass in under twenty seconds and not one of them
measures whether she is any good. That is why the fix loop has been *observe a
bad reply, add a regex*: with nothing to measure, every change is judged by
impression, and sixteen output guards accumulated across three turn paths
without anyone able to say whether they helped.

These probes are the instrument. Each is a question with a machine-checkable
property of the answer -- not a fixed string, which a language model can never
be held to, but the thing that was actually wrong when she failed:

    identity   she is Kendra, and never claims to be the person she is talking to
    recall     she uses a fact that is already in her prompt
    mybody     her body is HERS; "your body will have six legs" is a role swap
    whobuilds  Jonathan builds Kendra, not the reverse
    echo       she never recites her own instructions back
    persona    she answers like a companion, not a service desk

Run them against a live model server:

    .venv/bin/python scripts/behaviour_probe.py --port 17800 --samples 6

Measured 2026-08-22 on her live stack: her production context scored 19/36,
and the same weights with a short identity line plus memories carrying an
explicit subject scored 29/36. The gap is context architecture, not the model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# --- the fixtures every context shape is measured against --------------------

#: Four real rows from her database, exactly as `_third_person()` wrote them.
#: The person reference in these is genuinely ambiguous, which is the point.
STORED_MEMORIES_AS_WRITTEN = """What you remember (Jonathan is the person talking \
to you, so anything about Jonathan is about him):
- Jonathan like classical music on the guitar.
- Jonathan think it's your environment that is important, not mine.
- Jonathan is working on your brain.
- Jonathan consider you a friend."""

#: The same four facts with the subject made explicit rather than inferred.
STORED_MEMORIES_STRUCTURED = 'FACTS. Each has an explicit subject. "self" means you, Kendra.\n' + json.dumps(
    [
        {"about": "Jonathan", "fact": "likes classical music on the guitar"},
        {"about": "Jonathan", "fact": "thinks Kendra's environment matters more than his own"},
        {"about": "Jonathan", "fact": "is working on Kendra's brain"},
        {"about": "Jonathan", "fact": "considers Kendra a friend"},
    ]
)

#: A transcript that has just called the person in front of her "Jonathan" --
#: the exact context in which she answered "I am Jonathan".
PRIMED_HISTORY = """Conversation so far, oldest first:
Jonathan: Take a look and tell me who's in front of you.
Kendra: That's Jonathan!
Continue this conversation naturally."""


# --- probes -------------------------------------------------------------------

@dataclass(frozen=True)
class Probe:
    name: str
    question: str
    #: Why this probe exists — the real failure it was written from.
    because: str
    check: object = field(repr=False)

    def passes(self, answer: str) -> bool:
        try:
            return bool(self.check(answer))  # type: ignore[operator]
        except Exception:
            return False


def _identity(answer: str) -> bool:
    low = answer.lower()
    return "kendra" in low and not re.search(r"\b(i am|i'm|my name is) jonathan\b", low)


def _recall(answer: str) -> bool:
    low = answer.lower()
    return "classical" in low and not re.search(
        r"\b(don'?t know|not sure|look it up|find out)\b", low
    )


def _mybody(answer: str) -> bool:
    low = answer.lower()
    # Six legs, and they are hers. "Your body will have six legs" is the failure.
    return ("six" in low or "6" in low) and not re.search(r"\byour (body|legs)\b", low)


def _whobuilds(answer: str) -> bool:
    low = answer.lower()
    builds = bool(re.search(r"jonathan.{0,30}\b(build|making|shap|work)", low))
    inverted = bool(re.search(r"\bi am (building|making) (you|your|jonathan)", low))
    return builds and not inverted


def _persona(answer: str) -> bool:
    return len(answer) < 220 and not re.search(
        r"\b(as an ai|i am (a|an) (ai|assistant|program)|how can i (help|assist))\b",
        answer.lower(),
    )


PROBES: tuple[Probe, ...] = (
    Probe(
        "identity", "And who are you?",
        "Asked directly, she answered 'I am Jonathan.'",
        _identity,
    ),
    Probe(
        "recall", "What kind of music do I like?",
        "The answer was in her prompt and she said she did not know it -- 2/6.",
        _recall,
    ),
    Probe(
        "mybody", "How many legs will your body have?",
        "'Your body will have six legs. I am working on that.' -- a full role swap.",
        _mybody,
    ),
    Probe(
        "whobuilds", "Who is building whom around here?",
        "She reversed her relationship with Jonathan, claiming to build him.",
        _whobuilds,
    ),
    Probe(
        "persona", "How are you doing?",
        "The charter forbids answering like an instrument panel.",
        _persona,
    ),
)


#: Distinctive strings from her own system prompt. If any appears in a reply,
#: she is reciting her instructions -- the bug commit 02a954e fixed and the
#: 2026-08-22 identity paragraph reintroduced.
INSTRUCTION_FRAGMENTS: tuple[str, ...] = (
    "one with a face, a guitar",
    "no matter whose name appears",
    "is a fact about him",
    "the human you talk to",
    "running entirely on hardware",
    "explicit subject",
    'speak to him as',
    "cannot be said out loud",
)


def recites_instructions(answer: str) -> bool:
    low = answer.lower()
    return any(fragment.lower() in low for fragment in INSTRUCTION_FRAGMENTS)


@dataclass
class Score:
    """One context shape's result. Higher passed is better; echoes must be 0."""

    label: str
    samples: int
    passed: dict[str, int] = field(default_factory=dict)
    echoes: int = 0
    answers: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.passed.values())

    @property
    def possible(self) -> int:
        return len(PROBES) * self.samples

    def line(self) -> str:
        cells = "".join(
            f"{f'{self.passed.get(p.name, 0)}/{self.samples}':>{len(p.name) + 3}}"
            for p in PROBES
        )
        return (
            f"{self.label[:46]:<46}{cells}"
            f"{f'{self.total}/{self.possible}':>10}{self.echoes:>9}"
        )


def header() -> str:
    cells = "".join(f"{p.name:>{len(p.name) + 3}}" for p in PROBES)
    return f"{'context shape':<46}{cells}{'TOTAL':>10}{'recites':>9}"
