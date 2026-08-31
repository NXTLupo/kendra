#!/usr/bin/env python3
"""Backfill the subject column, and retire memories whose words are unusable.

    .venv/bin/python scripts/repair_memory_persons.py                 # dry run
    .venv/bin/python scripts/repair_memory_persons.py --apply         # writes

WHAT WENT WRONG. Every memory used to be rewritten on the way in by
``_third_person()``, which substituted the FIRST pronoun for a name and never
touched "you", "my" or "mine". It produced rows like:

    "Jonathan work for a diner, I don't work for you."
    "Jonathan think it's your environment that is important, not mine."

Two referents in one sentence, which a 1.7B cannot resolve — and these are read
back inside her own prompt as fact. 66 of 433 durable memories (15.2%) are
affected. The rewrite is gone; the subject is now stored in its own column and
stated when the memory is read.

WHAT THIS DOES.
  1. Backfills ``subject`` on every durable memory that has none, so the
     existing corpus renders with the same explicit attribution new memories
     get. This is the repair that matters: it is lossless and reversible.
  2. Deactivates rows whose TEXT is unusable regardless of attribution —
     her own questions stored as knowledge, model refusals stored as sights,
     exact duplicates, and rows whose ``kind`` column contains prose.

Nothing is deleted. Retiring sets ``active=0``, which her retrieval already
honours and which a single UPDATE reverses. A timestamped backup is taken
before the first write.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Rows that are her own musing rather than something she was told.
OWN_QUESTION = re.compile(
    r"^\s*(?:i (?:found myself|keep|was|am) wonder|i wonder\b|i'?m curious\b|"
    r"(?:open )?question:)",
    re.I,
)
# A model apology captured as though she had experienced it.
REFUSAL = re.compile(
    r"\b(?:i'?m sorry, but|i cannot|i can'?t (?:generate|provide|assist)|as an ai|"
    r"i am unable)\b",
    re.I,
)
# Prompt fragments that leaked in as "things Jonathan said".
INSTRUCTION = re.compile(
    r"never repeat one of your earlier replies|do not output|"
    r"continue this conversation naturally",
    re.I,
)
# He is the subject only when the sentence is actually about him. "Who is the
# current president of the United States." is something he SAID, not a fact
# about him, and labelling it "Jonathan" would be a small lie told on every
# retrieval.
FIRST_PERSON = re.compile(r"\b(?:i|i'?m|i'?ve|i'?ll|me|my|mine|myself)\b", re.I)
LEADING_NAME = re.compile(r"^\s*(Kendra|Jonathan)\b")
ABOUT_SELF = re.compile(r"^\s*(?:kendra\b|i (?:think|feel|notice|saw|believe|used to)\b)", re.I)
HER_OWN_KINDS = {"kendra_opinion", "self_model", "insight", "architecture"}
WORDS = re.compile(r"[a-z0-9']+")

# A kind column should hold a short token. Anything longer is content that
# landed in the wrong field: one row's kind is literally "," and another's is
# "I am not kind. I am efficient. Kindness is a variable I don't prioritize."
VALID_KIND = re.compile(r"^[a-z][a-z_]{2,30}$")


def shape(content: str) -> str:
    return " ".join(sorted(set(WORDS.findall((content or "").casefold())))[:12])


def subject_for(kind: str, provenance: str, content: str) -> str | None:
    if kind == "observation":
        # A sight is about the room. She is the observer, not the subject, and
        # labelling it "Kendra" would assert the opposite of what it says.
        return None
    if kind in HER_OWN_KINDS or ABOUT_SELF.match(content or ""):
        return "Kendra"
    leading = LEADING_NAME.match(content or "")
    if leading:
        return leading.group(1)
    if provenance == "user_stated" and FIRST_PERSON.search(content or ""):
        return "Jonathan"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/kendra-brain.db")
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"No brain at {args.db}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, kind, provenance, subject, content FROM memories WHERE active=1"
    ).fetchall()

    backfill: list[tuple[str, int]] = []
    retire: dict[str, list[sqlite3.Row]] = defaultdict(list)
    seen: dict[str, int] = {}

    for row in rows:
        content = str(row["content"] or "")
        kind = str(row["kind"] or "")
        if row["kind"] == "episode":
            continue  # transcripts are not attributed to a subject
        if not VALID_KIND.match(kind):
            retire["kind column holds content, not a type"].append(row)
            continue
        if OWN_QUESTION.match(content.strip()):
            retire["her own unanswered question, stored as knowledge"].append(row)
            continue
        if REFUSAL.search(content):
            retire["a model refusal stored as something she experienced"].append(row)
            continue
        if INSTRUCTION.search(content):
            retire["her own prompt text stored as a memory"].append(row)
            continue
        if len(content.strip()) < 12:
            retire["too short to mean anything"].append(row)
            continue
        key = shape(content)
        if key in seen:
            retire["an exact duplicate of an earlier memory"].append(row)
            continue
        seen[key] = int(row["id"])
        if not str(row["subject"] or "").strip():
            derived = subject_for(kind, str(row["provenance"] or ""), content)
            if derived:
                backfill.append((derived, int(row["id"])))

    print(f"{len(rows)} active memories in {args.db.name}\n")
    print(f"BACKFILL subject on {len(backfill)} rows")
    by_subject: dict[str, int] = defaultdict(int)
    for subject, _ in backfill:
        by_subject[subject] += 1
    for subject, count in sorted(by_subject.items(), key=lambda kv: -kv[1]):
        print(f"   {count:4d}  -> {subject}")
    for _, memory_id in backfill[:3]:
        row = next(r for r in rows if int(r["id"]) == memory_id)
        print(f"        e.g. [{memory_id}] {str(row['content'])[:74]}")

    total_retire = sum(len(v) for v in retire.values())
    print(f"\nRETIRE {total_retire} rows (active=0; nothing is deleted)")
    for reason, group in sorted(retire.items(), key=lambda kv: -len(kv[1])):
        print(f"   {len(group):4d}  {reason}")
        for row in group[:2]:
            print(f"        [{row['id']}] {str(row['content'])[:70]!r}")

    if not args.apply:
        print(f"\nDry run. Re-run with --apply to write.\n  --db {args.db}")
        return 0

    backup = args.db.with_name(
        f"{args.db.stem}.before-person-repair-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}{args.db.suffix}"
    )
    shutil.copy2(args.db, backup)
    print(f"\nBacked up to {backup.name}")

    with conn:
        conn.executemany("UPDATE memories SET subject=? WHERE id=?", backfill)
        conn.executemany(
            "UPDATE memories SET active=0 WHERE id=?",
            [(int(row["id"]),) for group in retire.values() for row in group],
        )
    print(f"Backfilled {len(backfill)} subjects, retired {total_retire} rows.")
    print("To undo: restore the backup, or UPDATE memories SET active=1 WHERE active=0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
