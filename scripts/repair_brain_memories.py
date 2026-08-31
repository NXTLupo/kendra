#!/usr/bin/env python3
"""Repair two problems in Kendra's memory, and undo a mistake I made.

WHAT HAPPENED. Her brain had five genuinely broken memories — sentences that
name a subject and then use first person for someone else:

    "Kendra thinks Jonathan is the person I see."

Those read back inside her own prompt, and with several of them in context she
answered "And who are you?" with "I am Jonathan." Two more rows had free text
in the `kind` column instead of a memory type, so `exclude_kinds` filtering
silently missed them.

I wrote a cleanup that matched on content shape and ran it across ALL
memories. That was wrong: `episode` rows are verbatim conversation
transcripts ("User: ... Kendra: ...") and `observation` rows are raw sensor
notes. Both legitimately contain "I", so the rule caught them too and
deactivated 1,539 records instead of five.

Nothing was destroyed — `active` is a soft delete — but her episodic history
is currently switched off and needs turning back on.

WHAT THIS SCRIPT DOES

  1. Re-activates raw records (episode / observation) that match the flawed
     criteria. Anything her own dream review retired fails that test and is
     left alone, so this cannot resurrect memories she deliberately dropped.
  2. Retires ONLY distilled memories that are genuinely mixed-person —
     never episodes or observations.
  3. Retires rows whose `kind` holds content rather than a type.
  4. Normalises near-duplicate kinds (`user_statement` -> `user_stated`)
     so kind-based filtering is reliable.

Usage:
  .venv/bin/python scripts/repair_brain_memories.py            # report only
  .venv/bin/python scripts/repair_brain_memories.py --apply    # write

Stop her services first if you want a quiet database; it is not required,
since SQLite in WAL mode handles a concurrent writer safely.

DO NOT back this database up with `cp`. It runs in WAL mode, so copying the
.db alone captures a stale checkpoint and silently loses recent writes — I
did exactly that and it made the mess look worse than it was. Use:

    sqlite3 data/kendra-brain.db ".backup data/kendra-brain-backup.db"
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "kendra-brain.db"

# First person surviving in a memory that already names its subject.
MIXED = re.compile(r"\b(?:I|I'm|I've|I'll|me|my|mine|myself)\b")

# A memory "names its subject" only when it opens with an actual PERSON.
#
# This was `^(Kendra|Jonathan|[A-Z][a-z]+)`, and that third alternative
# matches any capitalised first word -- "My", "On", "Transplant", "Right".
# So every one of her own self-facts looked like a named subject followed by
# stray first person, and a dry run wanted to retire her entire build
# knowledge: "My current brain: Qwen3-1.7B...", "My body's computer will be a
# Raspberry Pi 5...", all three transplant phases, "On my body, the reflex
# layer and physical e-stop are the law". Her charter requires her to answer
# those confidently; deleting them would make her claim ignorance about her
# own transplant.
#
# This script's own docstring apologises for over-retiring 1,539 records once
# already. Same mistake, one regex further down.
NAMED = re.compile(r"^(Kendra|Jonathan)\b")

# First person about HERSELF is not a mixed referent -- it is her voice.
# "My current brain is..." has exactly one subject and that subject is Kendra.
SELF_STATEMENT = re.compile(
    r"^(?:My|I|Mine)\b"
    r"|^(?:On|In) my\b"
    r"|^Transplant phase\b",
    re.I,
)

# Raw records. These are transcripts and sensor notes, not statements ABOUT
# anyone, so person-coherence does not apply to them and never should have.
RAW_KINDS = ("episode", "observation")

ALIAS = {
    "user_statement": "user_stated",
    "observed": "observation",
    "remembered": "fact",
    "kendra": "kendra_opinion",
    "kinda": "fact",
    "name": "fact",
    "inferred": "insight",
}


def looks_mixed(content: str) -> bool:
    """Two different people in one sentence, one of them called "I".

    "Kendra thinks Jonathan is the person I see." -- three referents, and a
    1.7B model cannot hold them apart when this is read back into its prompt.

    NOT this: "My current brain is a Qwen3-1.7B fine-tune." One subject, and
    it is her.
    """
    text = (content or "").strip()
    if SELF_STATEMENT.match(text):
        return False
    opener = NAMED.match(text)
    if not opener or not MIXED.search(text):
        return False
    # A single named subject plus first person is usually fine, because the
    # first person is her: "Jonathan has a different vision for my music",
    # "Jonathan and I are building my hexapod body". Both are coherent and
    # both were queued for deletion by the looser rule.
    #
    # The real defect needs a THIRD referent -- the documented case is
    # "Kendra thinks Jonathan is the person I see.", which names two people
    # and then says "I" about one of them. That is what no small model can
    # hold apart, and that is all this should retire.
    other = "Jonathan" if opener.group(1) == "Kendra" else "Kendra"
    return re.search(rf"\b{other}\b", text) is not None


# The `_third_person()` rewrite replaced a leading "I" with "Jonathan" and
# left the verb conjugated for first person: "Jonathan like guitar music."
#
# These rows are NOT ambiguous and must not be retired -- the fact in them is
# true and useful, only the grammar is wrong. Deleting them would throw away
# real things he told her about himself. Repair the verb instead.
THIRD_PERSON_S = {
    "don't": "doesn't", "dont": "doesn't", "do": "does", "have": "has",
    "like": "likes", "think": "thinks", "want": "wants", "need": "needs",
    "work": "works", "play": "plays", "know": "knows", "feel": "feels",
    "make": "makes", "say": "says", "see": "sees", "go": "goes",
    "get": "gets", "take": "takes", "love": "loves", "hate": "hates",
    "prefer": "prefers", "enjoy": "enjoys", "use": "uses", "live": "lives",
}
UNGRAMMATICAL = re.compile(
    r"^(Jonathan)\s+(" + "|".join(sorted((re.escape(v) for v in THIRD_PERSON_S), key=len, reverse=True)) + r")\b",
    re.I,
)


def repair_agreement(content: str) -> str | None:
    """Fix "Jonathan like X" -> "Jonathan likes X". None when nothing to do."""
    text = (content or "").strip()
    match = UNGRAMMATICAL.match(text)
    if not match:
        return None
    verb = match.group(2)
    fixed = THIRD_PERSON_S.get(verb.casefold())
    if not fixed:
        return None
    return text[: match.start(2)] + fixed + text[match.end(2) :]


def bad_kind(kind: str) -> bool:
    """A memory type should be a short identifier, never a sentence."""
    value = (kind or "").strip()
    return len(value) > 18 or " " in value or value in {",", ".", ""}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write the changes")
    args = parser.parse_args()

    connection = sqlite3.connect(DB)
    cursor = connection.cursor()

    # 1. Undo my over-retirement.
    restore = [
        mid for mid, content in cursor.execute(
            f"SELECT id, content FROM memories WHERE active=0 "
            f"AND kind IN ({','.join('?' * len(RAW_KINDS))})", RAW_KINDS,
        ).fetchall()
        if looks_mixed(content)
    ]

    # 1b. Ungrammatical rewrites: repair, never retire. The fact is correct.
    agreement = []
    for mid, content in cursor.execute(
        "SELECT id, content FROM memories WHERE active=1"
    ).fetchall():
        fixed = repair_agreement(content)
        if fixed and fixed != (content or "").strip():
            agreement.append((mid, content, fixed))

    # 1c. Exact duplicates that predate write-side deduplication. Keep the
    # earliest of each identical content and retire the rest -- no information
    # is lost, because the text is byte-identical. Three copies of "Jonathan
    # likes early eighties heavy." filled three of the four slots in a live
    # prompt and crowded out everything else.
    duplicates = [
        mid for (mid,) in cursor.execute(
            "SELECT id FROM memories WHERE active=1 AND kind != 'episode' AND id NOT IN ("
            "  SELECT MIN(id) FROM memories WHERE active=1 AND kind != 'episode'"
            "  GROUP BY kind, content"
            ")"
        ).fetchall()
    ]

    # 2. Genuinely ambiguous DISTILLED memories — the real bug.
    retire = [
        (mid, content) for mid, content in cursor.execute(
            f"SELECT id, content FROM memories WHERE active=1 "
            f"AND kind NOT IN ({','.join('?' * len(RAW_KINDS))})", RAW_KINDS,
        ).fetchall()
        if looks_mixed(content)
    ]

    # 3. Rows whose kind holds content.
    polluted = [
        (mid, kind) for mid, kind in
        cursor.execute("SELECT id, kind FROM memories WHERE active=1").fetchall()
        if bad_kind(kind)
    ]

    print(f"re-activate {len(restore)} raw record(s) wrongly retired")
    print(f"repair      {len(agreement)} ungrammatical rewrite(s) — kept, not retired:")
    for _, before, after in agreement:
        print(f"              {before[:60]!r}")
        print(f"           -> {after[:60]!r}")
    print(f"retire      {len(duplicates)} exact duplicate(s) of a memory she already has")
    print(f"retire      {len(retire)} mixed-person memory(ies):")
    for _, text in retire:
        print(f"              {text[:88]}")
    print(f"retire      {len(polluted)} row(s) whose kind holds content:")
    for _, kind in polluted:
        print(f"              kind={kind[:66]!r}")

    renames = {
        old: cursor.execute(
            "SELECT COUNT(*) FROM memories WHERE kind=?", (old,)).fetchone()[0]
        for old in ALIAS
    }
    total_renames = sum(renames.values())
    print(f"normalise   {total_renames} row(s) onto the canonical kind vocabulary")

    if not args.apply:
        print("\nreport only — pass --apply to write")
        return 0

    cursor.executemany("UPDATE memories SET active=1 WHERE id=?", [(m,) for m in restore])
    cursor.executemany(
        "UPDATE memories SET content=? WHERE id=?", [(after, mid) for mid, _, after in agreement]
    )
    cursor.executemany("UPDATE memories SET active=0 WHERE id=?", [(m,) for m in duplicates])
    cursor.executemany("UPDATE memories SET active=0 WHERE id=?", [(m,) for m, _ in retire])
    cursor.executemany("UPDATE memories SET active=0 WHERE id=?", [(m,) for m, _ in polluted])
    for old, new in ALIAS.items():
        cursor.execute("UPDATE memories SET kind=? WHERE kind=?", (new, old))
    connection.commit()

    active = cursor.execute("SELECT COUNT(*) FROM memories WHERE active=1").fetchone()[0]
    print(f"\napplied. active memories: {active}")
    print("kinds now in use:")
    for kind, count in cursor.execute(
        "SELECT kind, COUNT(*) FROM memories WHERE active=1 GROUP BY kind ORDER BY 2 DESC"
    ):
        print(f"  {kind:<16} {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
