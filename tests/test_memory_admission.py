"""Nothing enters her memory that she will later read back as a fact.

Her corpus accumulated a VLM refusal stored as something she saw, a fragment
of her own system prompt stored as a thing Jonathan said, ASR mash, and the
same sentence three times over -- and all of it was later retrieved into a
live prompt as evidence. Refusing at the door is cheaper than repairing
afterwards, and repairing afterwards is what once retired 1,539 records.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kendra.brain.embeddings import HashingEmbeddingProvider
from kendra.brain.store import BrainStore, _admissible


@pytest.fixture()
def store() -> BrainStore:
    with tempfile.TemporaryDirectory() as directory:
        yield BrainStore(Path(directory) / "brain.db", HashingEmbeddingProvider(384))


def test_a_model_refusal_is_not_an_experience(store: BrainStore) -> None:
    """Stored verbatim as an observation: "I saw: I am sorry, but I cannot
    generate a story based on the image you've provided." """
    assert store.remember(
        kind="observation",
        content="I saw: I am sorry, but I cannot generate a story based on the image.",
        provenance="observed",
    ) == 0


def test_her_own_instructions_are_not_something_she_was_told(store: BrainStore) -> None:
    assert store.remember(
        kind="user_stated",
        content="Continue this conversation naturally. Never repeat one of your earlier replies.",
        provenance="inferred",
    ) == 0


def test_her_own_unanswered_question_is_not_evidence(store: BrainStore) -> None:
    """46 rows read "I found myself wondering: <question>", and they outranked
    the answer because they share almost every word with it."""
    assert store.remember(
        kind="kendra_opinion",
        content="I found myself wondering: What kind of music do you like?",
        provenance="inferred",
    ) == 0


def test_the_same_fact_is_never_stored_twice(store: BrainStore) -> None:
    """"Jonathan likes early eighties heavy." was stored three times and then
    filled three of the four slots in a live prompt."""
    text = "Jonathan likes early eighties heavy metal and industrial."
    first = store.remember(kind="user_stated", content=text, provenance="user_stated")
    second = store.remember(kind="user_stated", content=text, provenance="user_stated")
    assert first and second == first
    rows = store.conn.execute(
        "SELECT COUNT(*) FROM memories WHERE active=1 AND content=?", (text,)
    ).fetchone()[0]
    assert rows == 1


def test_her_own_self_knowledge_is_exempt(store: BrainStore) -> None:
    """Architecture facts speak in the first person about herself, on purpose.

    An over-broad cleanup queued every one of these for deletion -- "My
    current brain...", "My body's computer will be a Raspberry Pi 5...", all
    three transplant phases. Her charter requires her to answer them
    confidently; refusing them would make her claim ignorance about her own
    body.
    """
    stored = store.remember(
        kind="fact",
        content="My current brain: Qwen3-1.7B fine-tuned on my own conversations.",
        provenance="system",
        subject="architecture",
    )
    assert stored


def test_real_things_he_says_are_kept(store: BrainStore) -> None:
    """The point is admission control, not a locked door."""
    for text in (
        "Jonathan likes classical guitar and plays most evenings.",
        "Jonathan is building Kendra a hexapod body from a RaspClaws kit.",
        "Jonathan works in the diner on weekends.",
    ):
        assert store.remember(kind="user_stated", content=text, provenance="user_stated")


def test_a_refusal_never_costs_her_a_reply(store: BrainStore) -> None:
    """It SKIPS; it must never raise.

    There are seventeen call sites across her services, several inside live
    turns. Trading a bad memory for a dead reply would be the worse bug.
    """
    assert store.remember(kind="fact", content="Nice.", provenance="user_stated") == 0
    assert _admissible("fact", "Nice.", "user_stated") is not None
