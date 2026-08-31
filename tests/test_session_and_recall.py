"""A fresh session is a fresh conversation, and recall stays fast.

Two rules Jonathan asked for, both of which the code previously broke:

  1. Prior conversations are memories. The raw transcript must not follow her
     across a restart -- her rolling context and the desktop's Live
     Conversation panel both begin at the session boundary.
  2. The wiki must be organised for fast recall, because an excerpt from it
     rides EVERY turn.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from kendra import session
from kendra.brain.embeddings import HashingEmbeddingProvider
from kendra.brain.second_brain import SecondBrain
from kendra.brain.store import BrainStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def store() -> BrainStore:
    with tempfile.TemporaryDirectory() as directory:
        brain = BrainStore(Path(directory) / "b.db", HashingEmbeddingProvider(384))
        brain.begin_session("s1")
        for i in range(4):
            brain.record_turn("s1", f"question {i}", f"answer {i}") if hasattr(
                brain, "record_turn"
            ) else brain.conn.execute(
                "INSERT INTO turns(session_id, user_text, kendra_text, created_at) "
                "VALUES (?,?,?,datetime('now','-1 hour'))",
                ("s1", f"question {i}", f"answer {i}"),
            )
        brain.conn.commit()
        yield brain


# --- sessions ---------------------------------------------------------------

def test_yesterdays_transcript_does_not_follow_her_into_today(store: BrainStore) -> None:
    with tempfile.TemporaryDirectory() as runtime:
        assert store.dashboard_snapshot(limit=10)["turns"], "fixture should have turns"
        floor = session.begin(runtime)
        assert store.dashboard_snapshot(limit=10, since=floor)["turns"] == []
        assert store.recent_turns(limit=6, max_age_seconds=7200, since=floor) == []


def test_a_turn_taken_inside_the_session_is_kept(store: BrainStore) -> None:
    with tempfile.TemporaryDirectory() as runtime:
        floor = session.begin(runtime)
        time.sleep(0.01)
        store.begin_session("s2")     # turns reference a session row
        store.conn.execute(
            "INSERT INTO turns(session_id, user_text, kendra_text, created_at) "
            "VALUES (?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            ("s2", "said just now", "replied just now"),
        )
        store.conn.commit()
        shown = store.dashboard_snapshot(limit=10, since=floor)["turns"]
        assert len(shown) == 1 and shown[0]["user_text"] == "said just now"


def test_her_memories_are_untouched_by_a_new_session(store: BrainStore) -> None:
    """The whole point: the window resets, the knowledge does not."""
    store.remember(
        kind="user_stated",
        content="Jonathan likes classical guitar and plays most evenings.",
        provenance="user_stated",
    )
    with tempfile.TemporaryDirectory() as runtime:
        session.begin(runtime)
        assert any("classical" in hit.content for hit in store.search("guitar music", limit=5))


def test_a_missing_stamp_never_looks_like_amnesia(store: BrainStore) -> None:
    """No boundary recorded means fall back to the age window, not to nothing."""
    with tempfile.TemporaryDirectory() as runtime:
        assert session.started_at(runtime) is None
        assert store.dashboard_snapshot(limit=10, since=None)["turns"]


def test_the_stack_stamps_a_session_when_it_starts() -> None:
    import inspect

    from kendra.devstack import DevStack

    assert "begin(self.settings.runtime_dir)" in inspect.getsource(DevStack.start)


# --- recall -----------------------------------------------------------------

@pytest.fixture()
def wiki() -> SecondBrain:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        brain = SecondBrain(root)
        brain.upsert_page(
            "classical-guitar", "Classical Guitar",
            [f"Filler fact number {i} about something unrelated. (2026-08-01)" for i in range(30)]
            + ["Jonathan says he likes classical music on the guitar. (2026-08-22)"],
        )
        brain.upsert_page("bees", "Bees", ["Honeybees vote on nest sites by dancing. (2026-08-02)"])
        yield brain


def test_the_excerpt_answers_the_question_not_the_top_of_the_file(wiki: SecondBrain) -> None:
    """It returned `body[:500]`, so a fact below the cut contributed nothing.

    Asked what music he liked, the wiki excerpt riding her prompt was the
    literal string "Links: [[research]]" -- the page footer.
    """
    hits = wiki.lookup("what classical music does he like", limit=1)
    assert hits, "the page should qualify"
    assert "classical music on the guitar" in hits[0]["excerpt"]
    assert "Links:" not in hits[0]["excerpt"]


def test_a_page_with_no_facts_is_never_created(wiki: SecondBrain) -> None:
    wiki.upsert_page("empty-thing", "Empty Thing", [])
    assert not (wiki.wiki_dir / "empty-thing.md").exists()


def test_a_factless_page_is_not_indexed(wiki: SecondBrain) -> None:
    """Their slugs come from raw utterances, so they match the speaker's own
    words on the 3x-weighted title and then answer nothing."""
    (wiki.wiki_dir / "research-nothing-just-eating-lunch.md").write_text(
        "---\nslug: research-nothing-just-eating-lunch\n---\n\n\nLinks: [[research]]\n",
        encoding="utf-8",
    )
    for hit in wiki.lookup("nothing just eating lunch", limit=3):
        assert hit["slug"] != "research-nothing-just-eating-lunch"


def test_lookup_does_not_reread_the_corpus_every_turn(wiki: SecondBrain) -> None:
    """Reading every page per turn measured 8.5 ms and grew with the wiki."""
    wiki.lookup("bees")                       # build the index
    reads = 0
    original = Path.read_text

    def counting(self, *args, **kwargs):      # noqa: ANN001
        nonlocal reads
        if str(self).endswith(".md"):
            reads += 1
        return original(self, *args, **kwargs)

    Path.read_text = counting
    try:
        for _ in range(5):
            wiki.lookup("bees dancing nest")
    finally:
        Path.read_text = original
    assert reads == 0, f"re-read {reads} pages despite nothing changing"


def test_an_edited_page_is_picked_up(wiki: SecondBrain) -> None:
    """The index must not go stale -- that would be a worse bug than slowness."""
    assert not wiki.lookup("penguins", limit=1)
    wiki.upsert_page("penguins", "Penguins", ["Penguins huddle to share warmth. (2026-08-03)"])
    hits = wiki.lookup("penguins huddle", limit=1)
    assert hits and "huddle" in hits[0]["excerpt"]


def test_the_manifest_states_the_recall_contract() -> None:
    """The manifest is the schema; code follows it. These rules live there."""
    manifest = (ROOT / "data/second_brain/MANIFEST.md").read_text(encoding="utf-8")
    assert "## Recall" in manifest
    assert "## Sessions" in manifest
    for rule in ("at least one fact", "index", "answer the question", "Titles are keys"):
        assert rule.lower() in manifest.lower(), rule
