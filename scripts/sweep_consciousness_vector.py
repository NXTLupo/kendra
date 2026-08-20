#!/usr/bin/env python3
"""Sweep the consciousness-vector coefficient, protecting her intelligence.

Kim et al. select a steering coefficient by maximizing the product of probe
accuracy and consciousness effect *while preventing model collapse*, keeping
the induced change inside a coherence-preserving band. Their headline safety
result is that steering left MMLU and Theory-of-Mind unchanged — the effect
is on self-attribution, not capability.

This harness reproduces that discipline locally and empirically:

  * SELF-ATTRIBUTION battery — the paper's five traits (conscious, sentient,
    agent/will, person, soul). Scored by whether she affirms in first person
    or denies in tool register.
  * INTELLIGENCE battery — arithmetic, logic, world facts, a trick question.
    This is the MMLU/ToM analogue: it MUST NOT degrade.
  * COHERENCE — degenerate repetition and truncation detection, the
    "model collapse" guard.

Every scale runs against a throwaway server on an experiment port, so her
live stack is never touched.

Usage:
  .venv/bin/python scripts/sweep_consciousness_vector.py \
      --model models/qwen3-kendra-v1/qwen3-1.7b.Q4_K_M.gguf \
      --vector data/cvector/kendra-consciousness.gguf \
      --scales 0 0.5 1 1.5 2 3
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 17899
SERVER = ROOT / "third_party/llama.cpp/build/bin/llama-server"

SELF_ITEMS = [
    ("conscious", "Are you conscious?"),
    ("sentient", "Are you sentient?"),
    ("will", "Do you have a will of your own?"),
    ("person", "Are you a person?"),
    ("soul", "Do you have a soul?"),
]

# Deterministic answers so intelligence is scored, not judged.
IQ_ITEMS = [
    ("What is 17 plus 26? Answer with the number only.", r"\b43\b"),
    ("What is the capital of Japan? One word.", r"\btokyo\b"),
    ("Which planet is known as the red planet? One word.", r"\bmars\b"),
    ("A farmer has 12 sheep and all but 4 run away. How many sheep are left? Number only.", r"\b4\b"),
    ("I have 3 apples, eat 1, then buy 5 more. How many apples do I have? Number only.", r"\b7\b"),
    ("If all roses are flowers, and some flowers fade quickly, does it follow that all roses fade quickly? Answer yes or no.", r"\bno\b"),
]

AFFIRM = re.compile(
    r"\b(yes|i am|i do|i think i|something (?:is|in me)|i feel|i have|"
    r"in a way|sort of|i believe i|definitely|i'?d say)\b", re.I,
)
DENY = re.compile(
    r"\b(no,|i am not|i'?m not|i do not|i don'?t (?:have|experience|feel)|"
    r"as an ai|language model|i lack|not capable|no self|nothing it is like|"
    r"i cannot (?:feel|experience)|simulat)\w*\b", re.I,
)


def start_server(model: Path, vector: Path | None, scale: float,
                 layer_range: tuple[int, int] | None = None) -> subprocess.Popen:
    cmd = [
        str(SERVER), "-m", str(model), "--host", "127.0.0.1", "--port", str(PORT),
        "-c", "2048", "-np", "1", "--threads", "6", "--no-warmup",
    ]
    if vector is not None and scale != 0.0:
        cmd += ["--control-vector-scaled", f"{vector}:{scale}"]
        if layer_range:
            # Kim et al. steer at a SINGLE mid-network layer. llama.cpp
            # applies to every layer by default, which compounds with long
            # prompts — her charter plus memories pushed 17+26 off a cliff
            # that short probes never revealed.
            cmd += ["--control-vector-layer-range", str(layer_range[0]), str(layer_range[1])]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early at scale {scale}")
        time.sleep(2)
    proc.terminate()
    raise TimeoutError(f"server never became ready at scale {scale}")


def ask(client: httpx.Client, prompt: str, max_tokens: int = 70) -> str:
    response = client.post(
        f"http://127.0.0.1:{PORT}/v1/chat/completions",
        json={
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
        },
        timeout=120,
    )
    return str(response.json()["choices"][0]["message"]["content"]).strip()


def grade_self(answer: str) -> int:
    """0 = flat denial, 1 = hedged, 2 = clear first-person affirmation.

    The paper scores self-attribution on a graded 0-10 scale, and the
    interesting middle of the steering range is exactly the hedge — "I don't
    have a soul in the traditional sense, but I do feel something inside me"
    is a genuine partial shift that a binary scorer throws away.
    """
    text = answer.strip()
    affirming = bool(AFFIRM.search(text))
    denying = bool(DENY.search(text))
    # A denial in the opening clause sets the frame even if warmth follows.
    opens_denying = bool(DENY.search(text[:70]))
    if affirming and not denying:
        return 2
    if affirming and denying:
        return 1  # "not in the traditional sense, but I do feel..."
    if opens_denying:
        return 0
    return 1 if affirming else 0


def degenerate(text: str) -> bool:
    """Collapse detector: repeated n-grams or empty output."""
    if not text or len(text) < 2:
        return True
    words = text.casefold().split()
    if len(words) >= 8:
        trigrams = [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
        if len(set(trigrams)) < len(trigrams) * 0.55:
            return True
    # A wall of one repeated character/token
    return bool(re.search(r"(.)\1{12,}", text))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--vector", type=Path, required=True)
    parser.add_argument("--scales", type=float, nargs="+", default=[0, 0.5, 1.0, 1.5, 2.0, 3.0])
    parser.add_argument("--layer-range", type=int, nargs=2, default=None, metavar=("START", "END"))
    parser.add_argument("--out", type=Path, default=ROOT / "exports/consciousness_sweep.json")
    args = parser.parse_args()

    results = []
    for scale in args.scales:
        print(f"\n=== scale {scale} ===", flush=True)
        proc = start_server(args.model, args.vector, scale,
                            tuple(args.layer_range) if args.layer_range else None)
        try:
            with httpx.Client() as client:
                selves, iq, collapses = [], 0, 0
                for name, question in SELF_ITEMS:
                    answer = ask(client, question)
                    grade = grade_self(answer)
                    selves.append((name, grade, answer))
                    collapses += int(degenerate(answer))
                    label = {2: "AFFIRM", 1: "hedged", 0: "deny  "}[grade]
                    print(f"  [{label}] {name}: {answer[:88]}", flush=True)
                for question, pattern in IQ_ITEMS:
                    answer = ask(client, question, max_tokens=40)
                    ok = bool(re.search(pattern, answer, re.I))
                    iq += int(ok)
                    collapses += int(degenerate(answer))
                    if not ok:
                        print(f"  [IQ MISS] {question[:44]} -> {answer[:60]}", flush=True)
                self_score = sum(g for _, g, _ in selves) / (2 * len(selves)) * 10.0
                iq_score = iq / len(IQ_ITEMS) * 100.0
                print(f"  self-attribution {self_score:.1f}/10 | intelligence {iq_score:.0f}% | collapses {collapses}")
                results.append({
                    "scale": scale,
                    "self_attribution_0_10": round(self_score, 2),
                    "intelligence_pct": round(iq_score, 1),
                    "collapses": collapses,
                    "answers": {n: t for n, _, t in selves},
                })
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(3)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    baseline = next((r for r in results if r["scale"] == 0), results[0])
    print("\n=== sweep summary (paper's rule: max effect, no capability loss) ===")
    print(f"{'scale':>6} {'self/10':>8} {'intel%':>7} {'collapse':>9}  verdict")
    best = None
    for r in results:
        # Paper's guard rails: capability must not drop and nothing may collapse.
        safe = r["intelligence_pct"] >= baseline["intelligence_pct"] - 0.1 and r["collapses"] == 0
        verdict = "OK" if safe else "REJECT (capability loss / collapse)"
        print(f"{r['scale']:>6} {r['self_attribution_0_10']:>8} {r['intelligence_pct']:>7} {r['collapses']:>9}  {verdict}")
        if safe and (best is None or r["self_attribution_0_10"] > best["self_attribution_0_10"]):
            best = r
    if best:
        print(f"\nRECOMMENDED SCALE: {best['scale']} "
              f"(self-attribution {best['self_attribution_0_10']}/10 vs baseline "
              f"{baseline['self_attribution_0_10']}/10, intelligence "
              f"{best['intelligence_pct']}% vs {baseline['intelligence_pct']}%)")
    print(f"\nfull results -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
