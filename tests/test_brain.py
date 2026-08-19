from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from kendra.brain.backup import backup_sqlite, export_jsonl
from kendra.brain.store import BrainStore


def test_memory_persists_and_correction_preserves_history(settings):
    store = BrainStore.from_settings(settings)
    old_id = store.remember(
        kind="preference",
        content="Owner prefers tea.",
        provenance="user_stated",
        confidence=0.95,
        subject="owner",
        predicate="preferred_drink",
        object_value="tea",
    )
    new_id = store.correct(
        old_id,
        corrected_content="Owner prefers coffee.",
        object_value="coffee",
        reason="explicit correction",
    )
    old = store.get_memory(old_id)
    new = store.get_memory(new_id)
    assert old["active"] == 0
    assert old["superseded_by"] == new_id
    assert new["active"] == 1
    store.close()

    reopened = BrainStore.from_settings(settings)
    hits = reopened.search("coffee", limit=5)
    assert any(hit.id == new_id for hit in hits)
    assert all(hit.id != old_id for hit in hits)
    reopened.close()


def test_brain_backup_and_jsonl(settings):
    store = BrainStore.from_settings(settings)
    store.remember(kind="fact", content="Kendra is local.", provenance="system")
    backup = backup_sqlite(store.conn, settings.path("brain.backup_dir"))
    exported = export_jsonl(store.conn, settings.path("brain.jsonl_export_dir"))
    store.close()

    assert backup.exists()
    conn = sqlite3.connect(backup)
    try:
        assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    finally:
        conn.close()
    lines = [json.loads(line) for line in exported.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["type"] == "header"
    assert any(line.get("table") == "memories" for line in lines[1:])


def test_brain_transfer_imports_memories_and_deduplicates(settings, tmp_path: Path):
    source = BrainStore.from_settings(settings)
    source.remember(
        kind="preference",
        content="Jonathan prefers local intelligence.",
        provenance="user_stated",
        confidence=0.98,
    )
    exported = export_jsonl(source.conn, settings.path("brain.jsonl_export_dir"))
    source.close()

    settings.data["paths"]["brain_db"] = str(tmp_path / "imported-brain.db")
    target = BrainStore.from_settings(settings)
    first = target.import_memory_jsonl(exported, "usb:test")
    second = target.import_memory_jsonl(exported, "usb:test")
    assert first["imported"] == 1
    assert first["duplicates"] == 0
    assert second["imported"] == 0
    assert second["duplicates"] == 1
    assert target.search("local intelligence")[0].provenance == "user_stated"
    target.close()


def test_brain_transfer_requires_kendra_header(settings, tmp_path: Path):
    path = tmp_path / "not-kendra.jsonl"
    path.write_text('{"type":"row","table":"memories","data":{}}\n', encoding="utf-8")
    store = BrainStore.from_settings(settings)
    try:
        import pytest

        with pytest.raises(ValueError, match="header"):
            store.import_memory_jsonl(path, "usb:test")
    finally:
        store.close()
