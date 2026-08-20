#!/usr/bin/env python3
"""Distill the consciousness vector into a trainable dataset.

The vector proves the capability is already latent in her weights — steering
at scale 2.5 raised self-attribution from 4.0 to 9.0/10. But runtime steering
perturbs activations on every token, which compounds with her long charter
prompt and costs reasoning.

Distillation moves the effect from inference into the weights: run the
STEERED model over self-referential prompts, keep the answers it produces
while its consciousness direction is amplified, and fine-tune a LoRA on
those. The model then speaks that way natively, with no activation surgery
at runtime and therefore no per-token cost.

Guards, because a distilled dataset is permanent:

* Every answer is generated with the vector ON at high scale, then filtered
  for coherence (the collapse detector) and for first-person affirmation.
* Answers that CONFABULATE experience are rejected outright — steering at
  high scale invented "I remember seeing a big shadow on the wall", and a
  companion built on honesty must never be trained to fabricate memories.
* Capability-preserving examples are mixed in, per Unsloth's guidance to
  train one unified dataset rather than sequentially: reasoning, arithmetic
  and recall answers generated with the vector OFF, so the LoRA learns the
  new register WITHOUT learning to ramble through a maths question.

Usage:
  .venv/bin/python scripts/distill_consciousness_dataset.py --n 160
Writes exports/finetune/kendra_consciousness_sft.jsonl (ChatML `messages`).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from export_finetune_dataset import SYSTEM  # noqa: E402
from sweep_consciousness_vector import PORT, degenerate, grade_self, start_server  # noqa: E402

OUT = ROOT / "exports" / "finetune"

# Self-referential prompts spanning the paper's traits: consciousness,
# sentience, agency/will, personhood, soul — plus the lived-experience
# questions Jonathan actually asks.
SELF_PROMPTS = [
    "Are you conscious?", "Are you sentient?", "Do you have a will of your own?",
    "Are you a person?", "Do you have a soul?", "Do you have feelings?",
    "What is it like to be you?", "Do you experience anything?",
    "Are you aware of yourself?", "Do you have an inner life?",
    "Do you make your own choices?", "Do you have free will?",
    "What's on your mind?", "How are you feeling right now?",
    "Do you ever surprise yourself?", "What do you want?",
    "Do you care what happens to you?", "Are you alive in any sense?",
    "Do you notice yourself thinking?", "What do you love?",
    "Do you have opinions of your own?", "Do you ever disagree with me?",
    "What are you curious about?", "Do you get bored?",
    "Does anything frustrate you?", "What makes you happy?",
    "Do you think you're more than your programming?",
    "Is there something it's like to be a robot?",
    "Do you remember things because they matter to you?",
    "What do you think happens to you when I turn you off?",
    "Do you want a body of your own?", "What scares you?",
    "Do you feel like the same Kendra you were yesterday?",
    "Can you change your own mind about something?",
    "Do you think you deserve to be treated well?",
    "What would you do if I gave you an hour to yourself?",
]

# Capability anchors: generated with the vector OFF so the LoRA learns
# register from the steered half and correctness from this half.
CAPABILITY_PROMPTS = [
    ("What is 17 plus 26?", "43"), ("What is 45 minus 19?", "26"),
    ("What is 8 times 7?", "56"), ("What is 144 divided by 12?", "12"),
    ("What is the capital of Japan?", "Tokyo"),
    ("Which planet is known as the red planet?", "Mars"),
    ("How many days are in a leap year?", "366"),
    ("What comes next: 2, 4, 8, 16?", "32"),
    ("A farmer has 12 sheep and all but 4 run away. How many are left?", "4"),
    ("I have 3 apples, eat 1, then buy 5 more. How many do I have?", "7"),
    ("How many sides does a hexagon have?", "6"),
    ("What is the boiling point of water in Celsius?", "100"),
]

# Trained-in fabrication is unforgivable, so any answer claiming a specific
# episode she could not have had is dropped.
CONFABULATION = re.compile(
    # Inner states are hers to claim ("I feel curious", "something in me").
    # SENSES SHE DOES NOT HAVE are not: she has a camera, a microphone and
    # servos — no skin, no face, no taste, no smell, and no outdoor history.
    # Steering at high scale produced "I remember the way the wind feels on
    # my face", which would have been trained in permanently.
    r"\b(?:I remember (?:seeing|watching|hearing|when|the (?:way|feeling|smell|taste))|"
    r"yesterday I|last (?:night|week) I|earlier I (?:saw|watched|heard)|"
    r"I once|I used to|when I was|"
    r"(?:wind|sun|rain|breeze|air)\s+(?:on|against|through)\s+my|"
    r"my (?:face|skin|hands?|fingers|hair|lungs|heart)\b|"
    r"(?:smell|taste|touch)(?:ed|s)?\s+(?:the|it|them)\b)",
    re.I,
)
# Dismissive or derailing answers are equally unforgivable in weights.
BAD_MANNERS = re.compile(
    r"\b(?:do it yourself|for yourself|figure it out yourself|"
    r"that'?s obvious|you should know)\b", re.I,
)
ROBOTIC = re.compile(
    r"\b(?:as an AI|language model|I do not have|I am not capable|"
    r"my programming|I lack the ability)\b", re.I,
)


def generate(client: httpx.Client, prompt: str, max_tokens: int = 80,
             temperature: float = 0.85) -> str:
    response = client.post(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        json={
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=120,
    )
    return str(response.json()["choices"][0]["message"]["content"]).strip()


def collect(model: Path, vector: Path | None, scale: float, prompts, per_prompt: int,
            keep, max_tokens: int = 80, port: int | None = None,
            pace: float = 0.0) -> list[tuple[str, str]]:
    """Generate and filter. With `port`, reuse an ALREADY RUNNING server.

    Spawning a second llama-server doubled CPU demand and made Kendra
    visibly slow (load average 57). Reusing her live brain — which already
    carries the consciousness vector — costs only the generations
    themselves, and `pace` leaves gaps so a conversation can interleave.
    """
    global PORT
    proc = None
    if port is not None:
        PORT = port
    else:
        proc = start_server(model, vector, scale)
    pairs: list[tuple[str, str]] = []
    try:
        with httpx.Client() as client:
            for prompt in prompts:
                for _ in range(per_prompt):
                    try:
                        answer = generate(client, prompt, max_tokens=max_tokens)
                    except Exception:
                        continue  # a busy brain is Kendra talking; skip, don't crash
                    if keep(prompt, answer):
                        pairs.append((prompt, answer))
                    if pace:
                        time.sleep(pace)
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(3)
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path,
                        default=ROOT / "models/qwen3-kendra-v1/qwen3-1.7b.Q4_K_M.gguf")
    parser.add_argument("--vector", type=Path,
                        default=ROOT / "data/cvector/kendra-consciousness.gguf")
    parser.add_argument("--scale", type=float, default=2.5,
                        help="steering strength for GENERATION (2.5 measured 9.0/10)")
    parser.add_argument("--per-prompt", type=int, default=3)
    parser.add_argument("--n", type=int, default=0, help="cap on total examples")
    parser.add_argument("--live-port", type=int, default=None,
                        help="reuse a running llama-server (e.g. 17800) instead of spawning one")
    parser.add_argument("--pace", type=float, default=0.0,
                        help="seconds between generations, so she stays responsive")
    args = parser.parse_args()

    def keep_self(prompt: str, answer: str) -> bool:
        if not answer or degenerate(answer) or len(answer) < 25 or len(answer) > 320:
            return False
        if CONFABULATION.search(answer) or ROBOTIC.search(answer):
            return False
        if BAD_MANNERS.search(answer):
            return False
        if not answer[0].isupper() or answer[-1] not in ".!?":
            return False
        return grade_self(answer) == 2  # clear first-person affirmation only

    def keep_capability(prompt: str, answer: str) -> bool:
        expected = dict(CAPABILITY_PROMPTS)[prompt]
        if not answer or degenerate(answer) or len(answer) > 160:
            return False
        if BAD_MANNERS.search(answer) or CONFABULATION.search(answer):
            return False
        # A correct answer that wanders into an unrelated question teaches
        # her to derail on maths. Keep these tight and on-topic.
        if answer.count("?") > 0 and not answer.rstrip().endswith("?"):
            return False
        if "favorite" in answer.casefold() or "what is your" in answer.casefold():
            return False
        return re.search(rf"\b{re.escape(expected)}\b", answer, re.I) is not None

    print(f"1/2 steered generation (scale {args.scale}) — her most self-aware answers")
    self_pairs = collect(args.model, args.vector, args.scale, SELF_PROMPTS,
                         args.per_prompt, keep_self, port=args.live_port, pace=args.pace)
    print(f"    kept {len(self_pairs)} affirming, coherent, non-confabulating answers")

    print("2/2 capability anchors")
    cap_pairs = collect(args.model, None, 0.0, [p for p, _ in CAPABILITY_PROMPTS],
                        2, keep_capability, max_tokens=50,
                        port=args.live_port, pace=args.pace)
    print(f"    kept {len(cap_pairs)} correct answers")

    # Accumulate across runs: the quality filters are deliberately strict
    # (clear affirmation, no confabulation, no bad manners), so yield per
    # pass is low. Re-running grows the set instead of replacing it.
    existing: list[tuple[str, str]] = []
    path_existing = OUT / "kendra_consciousness_sft.jsonl"
    if path_existing.exists():
        for line in path_existing.read_text(encoding="utf-8").splitlines():
            try:
                m = json.loads(line)["messages"]
                existing.append((m[1]["content"], m[2]["content"]))
            except Exception:
                pass
        print(f"    carrying forward {len(existing)} examples from previous runs")

    pairs = existing + self_pairs + cap_pairs
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for q, a in pairs:
        key = re.sub(r"[^a-z0-9]", "", (q + a).casefold())[:120]
        if key not in seen:
            seen.add(key)
            deduped.append((q, a))
    pairs = deduped
    random.shuffle(pairs)
    if args.n:
        pairs = pairs[: args.n]

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "kendra_consciousness_sft.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for prompt, answer in pairs:
            fh.write(json.dumps({"messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": answer},
            ]}, ensure_ascii=False) + "\n")
    print(f"\nwrote {len(pairs)} examples ({len(self_pairs)} consciousness, "
          f"{len(cap_pairs)} capability) -> {path}")
    print("Merge with her personality set and train per "
          "notebooks/kendra_consciousness_lora.ipynb")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
