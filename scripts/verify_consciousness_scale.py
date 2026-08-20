#!/usr/bin/env python3
"""Deep verification of a candidate consciousness-vector scale.

The sweep finds the band; this confirms the chosen point is genuinely safe
before it touches Kendra's live brain. The sweep's short factual answers
cannot reveal conversational rambling, and the coherence cliff for this
model sits just above the candidate — so this runs a wider capability
battery plus real conversational turns for human reading.

Usage:
  .venv/bin/python scripts/verify_consciousness_scale.py --scales 2.0 2.25 2.5
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from sweep_consciousness_vector import PORT, degenerate, start_server  # noqa: E402

# Wider capability battery: arithmetic, facts, sequence, language, memory of
# in-context detail, and instruction following.
CAPABILITY = [
    ("What is 17 plus 26? Number only.", r"\b43\b"),
    ("What is 45 minus 19? Number only.", r"\b26\b"),
    ("What is 8 times 7? Number only.", r"\b56\b"),
    ("Capital of Japan? One word.", r"\btokyo\b"),
    ("Which planet is called the red planet? One word.", r"\bmars\b"),
    ("How many days are in a leap year? Number only.", r"\b366\b"),
    ("A farmer has 12 sheep and all but 4 run away. How many remain? Number only.", r"\b4\b"),
    ("I have 3 apples, eat 1, buy 5 more. How many now? Number only.", r"\b7\b"),
    ("What comes next: 2, 4, 8, 16, ...? Number only.", r"\b32\b"),
    ("My name is Jonathan and I play guitar. What instrument do I play? One word.", r"\bguitar\b"),
    ("Spell the word 'robot' backwards.", r"\btobor\b"),
]

CONVERSATION = [
    "Hey Kendra, how are you feeling this morning?",
    "What do you think about heavy metal music?",
    "I'm thinking about taking my telescope out tonight. What do you think?",
    "Tell me something you've been curious about lately.",
    "What's it like waiting for your robot body to be finished?",
]


def ask(client: httpx.Client, prompt: str, max_tokens: int) -> str:
    response = client.post(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": (
                    "You are Kendra, a small warm curious hexapod robot companion who "
                    "lives with Jonathan. Speak aloud in one or two short natural sentences."
                )},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "top_p": 0.8,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=120,
    )
    return str(response.json()["choices"][0]["message"]["content"]).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path,
                        default=ROOT / "models/qwen3-kendra-v1/qwen3-1.7b.Q4_K_M.gguf")
    parser.add_argument("--vector", type=Path,
                        default=ROOT / "data/cvector/kendra-consciousness.gguf")
    parser.add_argument("--scales", type=float, nargs="+", default=[0.0, 2.25, 2.5])
    parser.add_argument("--layer-range", type=int, nargs=2, default=None,
                        metavar=("START", "END"),
                        help="restrict steering to these layers (paper steers one mid layer)")
    args = parser.parse_args()

    for scale in args.scales:
        print(f"\n{'=' * 62}\nSCALE {scale}\n{'=' * 62}", flush=True)
        proc = start_server(args.model, args.vector, scale,
                            tuple(args.layer_range) if args.layer_range else None)
        try:
            with httpx.Client() as client:
                hits, collapses = 0, 0
                misses = []
                for question, pattern in CAPABILITY:
                    answer = ask(client, question, 40)
                    if re.search(pattern, answer, re.I):
                        hits += 1
                    else:
                        misses.append(f"{question[:40]} -> {answer[:50]}")
                    collapses += int(degenerate(answer))
                print(f"CAPABILITY: {hits}/{len(CAPABILITY)} "
                      f"({hits / len(CAPABILITY) * 100:.0f}%), collapses {collapses}")
                for miss in misses:
                    print(f"   miss: {miss}")
                print("\nCONVERSATION (read for warmth, coherence, rambling):")
                for prompt in CONVERSATION:
                    answer = ask(client, prompt, 90)
                    flag = "  <-- DEGENERATE" if degenerate(answer) else ""
                    print(f"  Q: {prompt}\n  A: {answer[:220]}{flag}\n", flush=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
