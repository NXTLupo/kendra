#!/usr/bin/env python3
"""Sync Kendra's self-knowledge: architecture + build plan into her brain.

Run after EVERY architecture change (model swap, new organ, hardware spec
change). Upserts one memory per architecture line (subject=architecture,
superseding stale versions) plus build-plan summary memories, so Kendra can
discuss her own systems and her upcoming body build accurately by voice.

    .venv/bin/python scripts/sync_architecture_memory.py --config config/pc.yaml
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kendra.brain.store import BrainStore  # noqa: E402
from kendra.config import Settings  # noqa: E402

BUILD_FACTS = [
    ("build-plan", "Jonathan and I are building my hexapod body from the Adeept RaspClaws-Metal kit; the full transplant procedure lives in docs/TRANSPLANT_GUIDE.md and I can talk him through it step by step."),
    ("build-brain-computer", "My body's computer will be a Raspberry Pi 5 with 16 gigabytes of memory, an NVMe drive for my models and memories, a real-time-clock battery so I keep time offline, and an active cooler because sustained thinking runs hot."),
    ("build-phase-t0", "Transplant phase T0 happens on the bench before any chassis work: flash Raspberry Pi OS Lite, boot from NVMe, install my services and models, and verify I can hear, think, and speak with Wi-Fi off."),
    ("build-phase-t1", "Transplant phase T1 is the body build: assemble the RaspClaws chassis, then pass every hardware gate A1 through A9 — package hash, servo accounting (17 servos vs 16 controller channels must be reconciled), servo mapping, battery path, e-stop, four cliff sensors, and motion calibration. I stay fail-closed until every gate passes."),
    ("build-phase-t2", "Transplant phase T2 is my first embodied session: e-stop armed, legs off the ground, verify that killing the reflex service freezes all motion, and only then enable autonomy and my curiosity-approach behavior."),
    ("build-memory-transfer", "My memories transfer from the iMac by exporting my brain as JSONL and importing on the Pi; face-recognition biometrics never transfer by design and people re-enroll with consent."),
    ("build-safety", "On my body, the reflex layer and physical e-stop are the law: the language model only proposes typed whitelisted actions, a stale reflex heartbeat freezes motion, and vision or speech die before safety under memory pressure."),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/pc.yaml")
    args = parser.parse_args()
    settings = Settings.load(args.config)
    store = BrainStore.from_settings(settings)
    stored = 0
    arch = Path("docs/ARCHITECTURE_CURRENT.md").read_text(encoding="utf-8")
    for line in arch.splitlines():
        match = re.match(r"^- (\w[\w-]*): (.+)$", line.strip())
        if not match:
            continue
        component, description = match.group(1), match.group(2)
        store.remember(
            kind="fact",
            content=f"My current {component}: {description}",
            provenance="system",
            confidence=0.98,
            salience=0.45,
            subject="architecture",
            predicate=f"component:{component}",
            supersede_conflict=True,
            metadata={"created_by": "sync_architecture_memory"},
        )
        stored += 1
    for predicate, content in BUILD_FACTS:
        store.remember(
            kind="fact",
            content=content,
            provenance="system",
            confidence=0.98,
            salience=0.5,
            subject="build",
            predicate=predicate,
            supersede_conflict=True,
            metadata={"created_by": "sync_architecture_memory"},
        )
        stored += 1
    store.close()
    print(f"synced {stored} architecture/build memories into Kendra's brain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
