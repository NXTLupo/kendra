"""Three things she must never have to search for.

Retrieval is lexical. Measured on her real brain, the three most important
questions anyone asks her returned nothing at all:

    "who am I"        -> 0 memories
    "do you know me"  -> 0 memories
    "who are you"     -> 0 memories
    "what is my name" -> 3 memories   (only because "name" is a content word)

The facts were all present. "jonathan — name: Jonathan — Kendra's creator and
companion" sat in her slot-store the whole time. Asked who he was, she
answered that they had met in a park last week and that his wife was called
Emily. Both invented.

Measured against her live model: 1/12 correct without this block, 11/12 with.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kendra.brain.embeddings import HashingEmbeddingProvider
from kendra.brain.resident import ResidentContext, build, render
from kendra.brain.store import BrainStore


@pytest.fixture()
def store() -> BrainStore:
    with tempfile.TemporaryDirectory() as directory:
        brain = BrainStore(Path(directory) / "b.db", HashingEmbeddingProvider(384))
        brain.set_self("name", "Kendra", provenance="system")
        brain.set_self("identity", "small hexapod robot and intellectual companion", provenance="system")
        brain.set_fact("jonathan", "name", "Jonathan — Kendra's creator and companion")
        brain.set_fact("jonathan", "wife", "Peiyi")
        brain.set_fact("peiyi", "name", "Peiyi — Jonathan's wife")
        brain.reinforce_interest("music", delta=1.0, source="test")
        yield brain


def test_she_knows_who_she_is_without_searching(store: BrainStore) -> None:
    text = ResidentContext(store).text()
    assert "Kendra" in text
    assert "hexapod" in text


def test_she_knows_her_people_without_searching(store: BrainStore) -> None:
    """The exact failure: she could not reach Jonathan from "who am I"."""
    text = ResidentContext(store).text()
    assert "Jonathan" in text
    assert "Peiyi" in text
    assert "creator and companion" in text


def test_it_does_not_depend_on_the_question(store: BrainStore) -> None:
    """Resident means resident. Same block whatever was asked."""
    resident = ResidentContext(store)
    assert resident.text() == resident.text()
    assert "Jonathan" in resident.text()


def test_it_stays_small_because_every_turn_pays_for_it(store: BrainStore) -> None:
    """Prefill is the felt latency of her whole voice path."""
    text = ResidentContext(store).text()
    assert len(text) < 900, f"{len(text)} chars is too much to carry every turn"
    assert len(text.split()) < 160


def test_the_budget_is_honoured_and_identity_survives_the_trim(store: BrainStore) -> None:
    """Trim from the end: today is expendable, who she is never is."""
    text = render(build(store), budget_chars=120)
    assert len(text) <= 120
    assert "Kendra" in text


def test_a_broken_build_never_costs_her_a_reply(store: BrainStore) -> None:
    """She must still speak if this fails."""
    class Broken:
        def self_model(self):
            raise RuntimeError("no")

    assert ResidentContext(Broken()).text() == ""


def test_it_rides_in_with_the_memories_she_already_fetched() -> None:
    """One round trip per turn, and nothing outside the brain opens the DB."""
    import inspect

    from kendra.agent.planner import AgentRuntime
    from kendra.brain.service import BrainService

    served = inspect.getsource(BrainService.handle)
    assert '"resident"' in served or "context[\"resident\"]" in inspect.getsource(BrainService.handle)

    note = inspect.getsource(AgentRuntime._resident_note)
    assert '.get("resident")' in note, "the planner must read what the brain sent"
    planner = inspect.getsource(AgentRuntime)
    assert planner.count("_resident_note(memory)") >= 5, (
        "every path that builds a prompt must carry it"
    )


def test_today_is_included_when_there_is_a_today(store: BrainStore) -> None:
    store.remember(
        kind="user_stated",
        content="Jonathan is learning hybrid picking on the guitar this week.",
        provenance="user_stated",
    )
    blocks = build(store)
    assert blocks["today"]["discussed"], "today's topics should be resident"
