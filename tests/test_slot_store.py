"""The slot store's contract, enforced.

Her slot store is the exact typed tier consulted before any embedding — the
one place a fact is recalled with no similarity score involved. It was
operating without a schema, and an audit against the pattern failed four of
its seven rules:

    Fixed keys       keys were freeform: "favorite music", "guitar teacher focus"
    Stateful writes  every slot was written on one day and never touched again
    Strict schema    set_fact accepted any string as a key
    Prune/deprecate  nothing expired, so a five-day-old preference read as fresh

And the identity it served was one line written by `kendra init` on the day
the repository was created: "small hexapod robot and intellectual companion".
Asked who she was against that line, she answered "I am Jonathan."
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from kendra.brain import slots
from kendra.brain.embeddings import HashingEmbeddingProvider
from kendra.brain.resident import ResidentContext, build
from kendra.brain.store import BrainStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def store() -> BrainStore:
    with tempfile.TemporaryDirectory() as directory:
        yield BrainStore(Path(directory) / "b.db", HashingEmbeddingProvider(384))


# --- rule 1 & 5: fixed keys, enforced by a schema ---------------------------

def test_an_undeclared_key_is_refused(store: BrainStore) -> None:
    """Nothing may invent a slot that then gets injected as fact forever."""
    assert store.set_fact("jonathan", "mood_probably", "cheerful") is False
    assert "mood_probably" not in store.slots_for("jonathan")


def test_a_declared_key_is_written(store: BrainStore) -> None:
    assert store.set_fact("jonathan", "spouse", "Peiyi") is True
    assert store.slots_for("jonathan")["spouse"]["value"] == "Peiyi"


def test_refusing_never_raises(store: BrainStore) -> None:
    """A rejected slot stays a memory. It must never cost a turn."""
    assert store.set_fact("x", "not_a_slot", "y") is False


def test_legacy_keys_fold_onto_the_contract(store: BrainStore) -> None:
    """Her real brain held "favorite music", "guitar teacher focus", "wife"."""
    assert slots.normalise("favorite music") == "favourite_music"
    assert slots.normalise("guitar teacher focus") == "current_focus"
    assert slots.normalise("wife") == "spouse"
    assert slots.normalise("Favourite Music") == "favourite_music"
    assert slots.normalise("something invented") == ""


def test_the_migration_moves_values_rather_than_losing_them(store: BrainStore) -> None:
    store.conn.execute(
        "INSERT INTO facts(subject,key,value,updated_at) VALUES (?,?,?,?)",
        ("jonathan", "favorite music", "heavy metal", datetime.now(UTC).isoformat()),
    )
    store.conn.commit()
    assert store.migrate_slot_keys() == [("favorite music", "favourite_music")]
    assert store.slots_for("jonathan")["favourite_music"]["value"] == "heavy metal"


# --- rule 7: prune and deprecate --------------------------------------------

def test_a_stale_slot_is_withheld_but_not_destroyed(store: BrainStore) -> None:
    """A preference stated months ago is not a fact about someone today."""
    store.set_fact("jonathan", "current_focus", "hybrid picking")
    old = (datetime.now(UTC) - timedelta(days=60)).isoformat()
    store.conn.execute(
        "UPDATE facts SET updated_at=? WHERE subject='jonathan' AND key='current_focus'", (old,)
    )
    store.conn.commit()
    assert "current_focus" not in store.slots_for("jonathan")
    kept = store.slots_for("jonathan", include_stale=True)
    assert kept["current_focus"]["stale"] is True
    assert kept["current_focus"]["value"] == "hybrid picking"


def test_identity_never_expires(store: BrainStore) -> None:
    """Who someone is does not go stale."""
    assert slots.SLOTS["name"].ttl_days is None
    assert slots.SLOTS["relationship"].ttl_days is None
    assert slots.SLOTS["identity"].ttl_days is None


# --- rule 3: deterministic injection ----------------------------------------

def test_her_identity_comes_from_the_charter_not_a_seed(store: BrainStore) -> None:
    """"small hexapod robot and intellectual companion" describes a search
    appliance with legs. The charter says something else entirely."""
    store.set_fact("kendra", "identity", "a hexapod robot with a mind of your own")
    store.set_fact("kendra", "character", "extremely social; deeply inquisitive")
    store.set_fact("kendra", "expertise", "AI systems, local models and agents")
    text = ResidentContext(store).text(1200)
    assert "mind of your own" in text
    assert "social" in text and "inquisitive" in text
    assert "AI systems" in text
    assert "intellectual companion" not in text


def test_identity_is_stored_in_the_second_person() -> None:
    """It is read back as "You are Kendra: ...".

    Storing "a mind of my own" and prefixing it with "You are" is the exact
    referent collision behind every identity bug in this project.
    """
    seed = (ROOT / "scripts/seed_identity.py").read_text(encoding="utf-8")
    body = seed[seed.index("IDENTITY = {") : seed.index("def main()")]
    for first_person in (" my own", " draw me in", " I hold"):
        assert first_person not in body, f"{first_person!r} will collide with 'You are'"
    assert "your own" in body


def test_interests_are_weight_gated(store: BrainStore) -> None:
    """The interests table also collects one-off research subjects, which had
    her permanently "interested in president of the united states"."""
    store.reinforce_interest("music", delta=1.0, source="test")
    store.reinforce_interest("president of the united states", delta=0.01, source="research")
    store.conn.execute(
        "UPDATE interests SET weight=0.05 WHERE topic=?", ("president of the united states",)
    )
    store.conn.commit()
    interests = build(store)["you_are"]["interested_in"]
    assert "music" in interests
    assert "president of the united states" not in interests


def test_the_contract_is_describable() -> None:
    """It has to be readable by a person, or it will drift again."""
    described = slots.describe()
    assert {row["key"] for row in described} == set(slots.SLOTS)
    assert all(row["description"] for row in described)
    assert {row["scope"] for row in described} == {slots.PERSON, slots.SELF, slots.SESSION}
