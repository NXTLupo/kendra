from __future__ import annotations

import base64
import json
import sqlite3
from datetime import UTC, datetime
from io import TextIOBase
from pathlib import Path
from typing import Any

EXPORT_TABLES = [
    "memories",
    "people",
    "places",
    "interests",
    "goals",
    "open_questions",
    "sessions",
    "turns",
    "self_model",
    "reflections",
    "memory_links",
    "research_cache",
    "photo_log",
    "delivery_log",
    "cognitive_events",
]


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def backup_sqlite(source: sqlite3.Connection, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"kendra-brain-{_stamp()}.sqlite3"
    target_conn = sqlite3.connect(target)
    try:
        source.backup(target_conn)
        target_conn.execute("PRAGMA integrity_check")
        target_conn.commit()
    finally:
        target_conn.close()
    return target


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_base64__": base64.b64encode(value).decode("ascii")}
    return value


def write_jsonl(source: sqlite3.Connection, handle: TextIOBase) -> None:
    source.row_factory = sqlite3.Row
    header = {"format": "kendra-brain-jsonl", "version": 1, "created_at": datetime.now(UTC).isoformat()}
    handle.write(json.dumps({"type": "header", "data": header}, sort_keys=True) + "\n")
    for table in EXPORT_TABLES:
        try:
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
        except sqlite3.OperationalError:
            continue
        for row in rows:
            data = {key: _json_value(row[key]) for key in row.keys()}
            handle.write(json.dumps({"type": "row", "table": table, "data": data}, sort_keys=True) + "\n")


def export_jsonl(source: sqlite3.Connection, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    target = destination_dir / f"kendra-brain-{_stamp()}.jsonl"
    with target.open("w", encoding="utf-8") as handle:
        write_jsonl(source, handle)
    return target
