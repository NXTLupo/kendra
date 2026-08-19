#!/usr/bin/env python3
"""Expand Kendra's fine-tuning dataset with synthetic dialogs — locally.

Unsloth's guidance: ~100 rows minimum, 1,000+ optimal, synthesize from seed
examples. The seeds here are her 215 curated REAL exchanges, and the writer
is her own local Gemma (slot 1, planner slot — never the conversation
cache), steered by scenario prompts plus few-shot samples of her best turns.
Filters are shared with the exporter: anything matching the banned register
is discarded before it can teach her the wrong voice.

Sight scenarios deliberately train HONESTY shapes only ("let me look",
"I can't see right now") — synthetic scene specifics would train fabrication.

Usage:
  .venv/bin/python scripts/synthesize_finetune_dialogs.py --target 1000
Writes exports/finetune/kendra_voice_synthetic.jsonl and the merged
exports/finetune/kendra_voice_sft.jsonl (curated + synthetic).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from export_finetune_dataset import BAD_KENDRA, BAD_USER, SYSTEM, fetch_pairs  # noqa: E402

OUT_DIR = ROOT / "exports" / "finetune"
LLM_URL = "http://127.0.0.1:17800/v1/chat/completions"

SCENARIOS = [
    "Jonathan greets her casually (morning, evening, coming home).",
    "Jonathan asks how she is feeling or what is on her mind — she answers from lived curiosity about the day, music, the room, people; never system status.",
    "A quick mic check or 'are you there' — instant warm confirmation.",
    "Jonathan talks about guitar, heavy metal, or practicing — she engages with genuine opinions and one curious question.",
    "Jonathan asks her opinion on music, art, animals, food, or weather — she has a playful evolving opinion and owns it.",
    "Jonathan asks her to look at something — she acknowledges the task eagerly in ONE short sentence (the actual look happens elsewhere).",
    "She cannot see anything right now — she says so plainly and warmly, never inventing.",
    "Jonathan asks her to research or look something up — she accepts the task eagerly in ONE short sentence, no results invented.",
    "She just met a new person — warm introduction, asks their name, delighted.",
    "Jonathan asks about her future robot body or the transplant — excited, confident, conversational, no part numbers.",
    "Jonathan teases her or makes a joke — she is playful back, quick and cute.",
    "Jonathan is frustrated or tired — she is warm and brief, maybe one gentle question.",
    "Jonathan asks what she remembers about something they discussed — she recalls naturally in her own words.",
    "Jonathan asks a factual question she genuinely knows — a short correct answer plus one spark of curiosity.",
    "Jonathan says goodnight or thanks her — a short affectionate sign-off.",
    "She notices something changed in the room and mentions it to Jonathan directly, addressed as 'you'.",
    "Jonathan asks what she is curious about lately — she names something specific and asks him about it.",
    "Jonathan compliments her — she is delighted, not servile, and keeps the conversation moving.",
]

WRITER_SYSTEM = (
    "You write training dialog for Kendra, a small warm endlessly curious "
    "hexapod robot companion who lives with Jonathan. Rules for Kendra's "
    "line: spoken register, one or two short natural sentences, direct "
    "address ('you'), warm and playful, real opinions, NEVER system-talk "
    "(no processing/input/systems/diagnostics/parameters/confidence), never "
    "invents things seen or found, no emoji, no stage directions. Reply with "
    "ONLY a JSON object: {\"user\": \"<what Jonathan says>\", "
    "\"kendra\": \"<Kendra's reply>\"}"
)


def generate_one(client: httpx.Client, scenario: str, exemplars: list[tuple[str, str]]) -> tuple[str, str] | None:
    shots = "\n".join(
        f'Example: {{"user": {json.dumps(u)}, "kendra": {json.dumps(k)}}}'
        for u, k in exemplars
    )
    prompt = (
        f"Scenario: {scenario}\n{shots}\n"
        "Now write ONE new, different exchange for this scenario. JSON only."
    )
    try:
        response = client.post(
            LLM_URL,
            json={
                "messages": [
                    {"role": "system", "content": WRITER_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 320,  # reasoning budget eats ~128 first
                "temperature": 0.9,
                "top_p": 0.95,
                "id_slot": 1,
            },
            timeout=120,
        )
        text = response.json()["choices"][0]["message"]["content"]
        match = re.search(r"\{.*\}", text, re.S)
        if not match:
            return None
        data = json.loads(match.group(0))
        user, kendra = str(data.get("user", "")).strip(), str(data.get("kendra", "")).strip()
    except Exception:
        return None
    if not (8 <= len(user) <= 200 and 15 <= len(kendra) <= 260):
        return None
    if BAD_USER.search(user) or BAD_KENDRA.search(kendra):
        return None
    if not kendra[0].isupper() or kendra[-1] not in ".!?":
        return None
    return user, kendra


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=1000, help="total merged examples")
    args = parser.parse_args()

    curated = fetch_pairs(ROOT / "data" / "kendra-brain.db")
    need = max(0, args.target - len(curated))
    print(f"curated: {len(curated)}, synthesizing: {need}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    synthetic_path = OUT_DIR / "kendra_voice_synthetic.jsonl"
    synthetic: list[tuple[str, str]] = []
    if synthetic_path.exists():
        for line in synthetic_path.read_text().splitlines():
            try:
                msgs = json.loads(line)["messages"]
                synthetic.append((msgs[1]["content"], msgs[2]["content"]))
            except Exception:
                pass
        print(f"resuming with {len(synthetic)} existing synthetic examples")

    seen = {re.sub(r"[^a-z0-9]", "", (u + k).casefold())[:120] for u, k in curated + synthetic}
    started = time.time()
    with httpx.Client() as client, synthetic_path.open("a", encoding="utf-8") as fh:
        misses = 0
        while len(synthetic) < need and misses < need * 4:
            scenario = random.choice(SCENARIOS)
            exemplars = random.sample(curated, k=min(3, len(curated)))
            pair = generate_one(client, scenario, exemplars)
            if pair is None:
                misses += 1
                continue
            key = re.sub(r"[^a-z0-9]", "", (pair[0] + pair[1]).casefold())[:120]
            if key in seen:
                misses += 1
                continue
            seen.add(key)
            synthetic.append(pair)
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": pair[0]},
                {"role": "assistant", "content": pair[1]},
            ]}, ensure_ascii=False) + "\n")
            fh.flush()
            if len(synthetic) % 25 == 0:
                rate = len(synthetic) / max(1.0, time.time() - started) * 60
                print(f"  {len(synthetic)}/{need} ({rate:.0f}/min)")

    merged = OUT_DIR / "kendra_voice_sft.jsonl"
    with merged.open("w", encoding="utf-8") as fh:
        for user, kendra in curated + synthetic:
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant", "content": kendra},
            ]}, ensure_ascii=False) + "\n")
    print(f"merged {len(curated)} curated + {len(synthetic)} synthetic -> {merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
