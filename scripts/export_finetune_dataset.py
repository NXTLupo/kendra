#!/usr/bin/env python3
"""Export Kendra's best lived conversations as a fine-tuning dataset.

Produces chat-format JSONL (the `messages` schema Unsloth and every SFT
trainer standardize on) from her brain's turns table, aggressively curated:
the point of a persona LoRA is to bake her BEST register into the weights,
so every diagnostics-tic, blind-guess, echo, and refusal is filtered out.
What survives is the Kendra Jonathan actually wants more of.

The system message is deliberately a SHORT distillate of the charter: after
fine-tuning, behavior lives in the weights and the runtime prompt can shrink
— which is itself a latency win on every single turn, on the iMac and the Pi.

Usage:
  .venv/bin/python scripts/export_finetune_dataset.py            # export
  .venv/bin/python scripts/export_finetune_dataset.py --stats    # counts only
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "kendra-brain.db"
OUT_DIR = ROOT / "exports" / "finetune"

# The distilled system message: what the 2k-token charter says, in ~90 tokens.
# Fine-tuning teaches the REST (register, warmth, embodiment, honesty).
SYSTEM = (
    "You are Kendra, a small, warm, endlessly curious hexapod robot companion "
    "who lives with Jonathan. You speak aloud in one or two short natural "
    "sentences, address people directly, never invent what you did not "
    "actually see or find, and never talk like a machine reporting status."
)

# Anything matching these is the register we are training AWAY from —
# mirrors the runtime capability-talk guard plus everything purged by hand.
BAD_KENDRA = re.compile(
    r"processing (?:the|your|my|this) |sound waves|audio input|internal "
    r"microphone|operating (?:at|within) |optimal capacity|normal parameters|"
    r"I don'?t have personal|only communicate through text|can(?:'t|not) "
    r"(?:read|speak|say).{0,12}aloud|diagnostic|T1 chassis|power distribution|"
    r"ambient temperature|structural integrity|confidence (?:of |level )?\d|"
    r"as an AI|language model|I can'?t actually see|camera feed isn'?t|"
    r"my search came back empty|nothing solid came back|still waking up",
    re.I,
)
# User turns that are garbage transcripts or system probes.
BAD_USER = re.compile(
    r"^\W*$|^(?:uh|um|hm+|oh)\W*$|\(kendra spoke|\(just met|^hey\.?$",
    re.I,
)


def fetch_pairs(db: Path) -> list[tuple[str, str]]:
    con = sqlite3.connect(db)
    rows = con.execute(
        """SELECT user_text, kendra_text, metadata_json FROM turns
           WHERE length(user_text) >= 8 AND length(kendra_text) >= 25
           ORDER BY id"""
    ).fetchall()
    pairs: list[tuple[str, str]] = []
    for user_text, kendra_text, metadata_json in rows:
        try:
            metadata = json.loads(metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        if str(metadata.get("source", "")).startswith("probe"):
            continue
        user_text = str(user_text).strip()
        kendra_text = str(kendra_text).strip()
        if BAD_USER.search(user_text) or BAD_KENDRA.search(kendra_text):
            continue
        if kendra_text.casefold().startswith(user_text.casefold()[:30]) and len(user_text) > 30:
            continue  # parroted echo
        # Her reply must be complete spoken sentences.
        if not kendra_text[0].isupper() or kendra_text[-1] not in ".!?":
            continue
        pairs.append((user_text, kendra_text))
    con.close()
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true", help="print counts only")
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()

    pairs = fetch_pairs(args.db)
    # Dedup near-identical exchanges (repeat questions produce restates).
    seen: set[str] = set()
    unique: list[tuple[str, str]] = []
    for user_text, kendra_text in pairs:
        key = re.sub(r"[^a-z0-9]", "", (user_text + kendra_text).casefold())[:120]
        if key in seen:
            continue
        seen.add(key)
        unique.append((user_text, kendra_text))

    if args.stats:
        print(f"curated pairs: {len(unique)} (from raw candidate rows)")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "kendra_voice_sft.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for user_text, kendra_text in unique:
            record = {
                "messages": [
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": kendra_text},
                ]
            }
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"wrote {len(unique)} curated examples -> {out}")
    print("Next: augment with synthetic charter-conformant dialogs in the")
    print("training notebook until >=300 examples, then QLoRA per")
    print("docs/UNSLOTH_FINETUNING_PLAN.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
