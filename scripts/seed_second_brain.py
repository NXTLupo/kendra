"""One-time backfill: pour Kendra's existing SQLite memories into the
second brain's raw log, then run compile rounds until the backlog drains.

Usage (services must be running so compile can reach the LLM):
    .venv/bin/python scripts/seed_second_brain.py [--config config/pc.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kendra.brain.second_brain import SecondBrain  # noqa: E402
from kendra.config import Settings  # noqa: E402
from kendra.ipc import UnixJsonClient  # noqa: E402


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/pc.yaml")
    parser.add_argument("--max-rounds", type=int, default=10)
    args = parser.parse_args()

    settings = Settings.load(Path(args.config))
    sb = SecondBrain(settings.path("brain.second_brain.dir"))
    db = settings.path("paths.brain_db")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    seeded = 0
    rows = con.execute(
        """SELECT kind, content FROM memories
           WHERE active=1 AND kind NOT IN ('episode')
             AND provenance NOT IN ('system')
           ORDER BY id"""
    ).fetchall()
    for row in rows:
        sb.ingest(str(row["kind"]), str(row["content"]), {"seed": True})
        seeded += 1
    print(f"Seeded {seeded} memories into {sb.raw_dir}")

    brain = UnixJsonClient(settings.socket_path("brain"), timeout=180)
    for round_no in range(args.max_rounds):
        pending = sb.pending_count()
        if pending < 4:
            break
        result = await brain.call("wiki_compile")
        print(f"Compile round {round_no + 1}: {result} ({pending} were pending)")
    print("Final stats:", sb.stats())


if __name__ == "__main__":
    asyncio.run(main())
