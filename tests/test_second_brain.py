"""The second brain must ingest immutably, compile incrementally, and
retrieve in one file read — the properties the manifest promises."""

from kendra.brain.second_brain import SecondBrain


def make(tmp_path):
    return SecondBrain(tmp_path / "second_brain")


def test_manifest_created_on_init(tmp_path):
    sb = make(tmp_path)
    text = sb.manifest_path.read_text()
    assert "Ingest" in text and "Compile" in text and "Execute" in text


def test_ingest_appends_and_pending_tracks_cursor(tmp_path):
    sb = make(tmp_path)
    sb.ingest("turn", "Jonathan: hi\nKendra: hey")
    sb.ingest("observation", "a man playing guitar")
    entries, cursor = sb.pending()
    assert len(entries) == 2
    assert entries[0]["kind"] == "turn"
    sb.advance(cursor)
    assert sb.pending_count() == 0
    sb.ingest("research", "Answer: rain tomorrow")
    assert sb.pending_count() == 1


def test_upsert_page_merges_and_dedupes(tmp_path):
    sb = make(tmp_path)
    sb.upsert_page("guitar", "Guitar", ["Jonathan plays heavy metal guitar."])
    sb.upsert_page(
        "guitar",
        "Guitar",
        [
            "Jonathan plays heavy metal on his guitar.",  # near-duplicate
            "Jonathan's teacher focuses on legato and alternate picking.",
        ],
        links=["jonathan"],
    )
    page = sb.read_page("guitar")
    assert page.count("- ") == 2  # duplicate merged away
    assert "[[jonathan]]" in page


def test_questions_are_not_facts(tmp_path):
    sb = make(tmp_path)
    sb.upsert_page("misc", "Misc", ["Is Jonathan playing music?", "Jonathan owns two guitars."])
    page = sb.read_page("misc")
    assert "Is Jonathan playing music" not in page
    assert "two guitars" in page


def test_lookup_prefers_title_matches_and_gates_noise(tmp_path):
    sb = make(tmp_path)
    sb.upsert_page("guitar", "Guitar", ["Jonathan plays heavy metal guitar."])
    sb.upsert_page("weather", "Weather", ["Rain was forecast for guitar city."])
    hits = sb.lookup("what do you know about my guitar playing")
    assert hits and hits[0]["slug"] == "guitar"
    assert sb.lookup("completely unrelated xylophone quasar") == []


def test_wiki_survives_reopen(tmp_path):
    sb = make(tmp_path)
    sb.ingest("turn", "Jonathan: remember this\nKendra: noted")
    sb.upsert_page("notes", "Notes", ["Jonathan asked Kendra to remember something."])
    reopened = SecondBrain(tmp_path / "second_brain")
    assert reopened.pending_count() == 1
    assert "remember" in reopened.read_page("notes")
