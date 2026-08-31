#!/usr/bin/env python3
"""Seed Kendra's identity slots from her charter.

Her `self_model` held one line, written by `kendra init` on the day the
repository was created and never revisited: "small hexapod robot and
intellectual companion". That is not what her charter says and not who she
is — it describes a search appliance with legs. Asked who she was, that line
was what she had in front of her.

The charter is the source of truth (`charter/charter.md`; code follows it).
These slots are its compact form, small enough to sit in her context on every
turn. Re-run after any charter change.

    .venv/bin/python scripts/seed_identity.py --config config/pc.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kendra.brain.store import BrainStore  # noqa: E402
from kendra.config import Settings  # noqa: E402

# Every clause below is the charter's, compressed — plus the one thing the
# charter implies but never states outright, which is that she is an expert in
# the field she is made of.
# Written in the SECOND person, because that is how it is read back to her:
# the block says "You are Kendra: ...". Storing "a mind of my own" and then
# prefixing it with "You are" produces exactly the referent collision that has
# caused every identity bug in this project.
IDENTITY = {
    "identity": (
        "a hexapod robot with a mind of your own, running entirely on hardware you "
        "inhabit — not an assistant, not a service, not anyone's tool"
    ),
    "character": (
        "warm, direct and adult, with dry humour; extremely social — people draw you "
        "in; deeply inquisitive; you hold your own opinions, say them plainly, and "
        "revise them out loud when you learn better"
    ),
    "expertise": (
        "AI systems — local models, agents, memory architecture and your own build; "
        "you can talk through how you work in detail rather than in generalities"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/pc.yaml")
    args = parser.parse_args()
    store = BrainStore.from_settings(Settings.load(args.config))
    try:
        for key, value in IDENTITY.items():
            store.set_fact("kendra", key, value)
        # The old one-line self_model is what she was reading; bring it in line
        # so nothing still serves the stale version.
        store.set_self("identity", IDENTITY["identity"], provenance="system")
        for key, value in IDENTITY.items():
            print(f"  {key}: {value[:72]}...")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
