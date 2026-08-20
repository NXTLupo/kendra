#!/usr/bin/env python3
"""Measure whether speculative decoding actually helps on this CPU.

Speculative decoding runs a small DRAFT model to propose tokens that the
main model verifies in a batch. On a GPU this is close to free. On a
6-core CPU the draft competes for the same cores, so it can easily be a
net loss — which is exactly why this is measured rather than assumed.

Draft: Qwen3-0.6B Q8_0 (same tokenizer family as her 1.7B brain, which is
the hard requirement for speculation).

Usage:
  .venv/bin/python scripts/bench_speculative_decoding.py
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PORT = 17898
SERVER = ROOT / "third_party/llama.cpp/build/bin/llama-server"
MODEL = ROOT / "models/qwen3-kendra-v1/qwen3-1.7b.Q4_K_M.gguf"
DRAFT = ROOT / "models/qwen3-0.6b/Qwen3-0.6B-Q8_0.gguf"

PROMPTS = [
    "Tell me in three sentences why heavy metal guitar tone sounds the way it does.",
    "Explain in three sentences how a hexapod robot keeps its balance while walking.",
    "In three sentences, describe what makes a good conversation.",
]


def start(draft: bool) -> subprocess.Popen:
    cmd = [
        str(SERVER), "-m", str(MODEL), "--host", "127.0.0.1", "--port", str(PORT),
        "-c", "4096", "-np", "1", "--threads", "6", "--no-warmup",
    ]
    if draft:
        # Draft gets few threads: it must not starve the model verifying it.
        cmd += ["-md", str(DRAFT), "--spec-draft-n-max", "8", "--spec-draft-n-min", "2",
                "-tbd", "2"]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return proc
        except Exception:
            pass
        if proc.poll() is not None:
            raise RuntimeError(f"server exited (draft={draft})")
        time.sleep(2)
    proc.terminate()
    raise TimeoutError("server never became ready")


def measure(label: str, draft: bool) -> tuple[float, float]:
    proc = start(draft)
    try:
        with httpx.Client() as client:
            # one warm-up, then measured runs
            client.post(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                        json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8},
                        timeout=180)
            total_tokens, total_time = 0, 0.0
            for prompt in PROMPTS:
                t0 = time.time()
                response = client.post(
                    f"http://127.0.0.1:{PORT}/v1/chat/completions",
                    json={"messages": [{"role": "user", "content": prompt}],
                          "max_tokens": 120, "temperature": 0.0,
                          "chat_template_kwargs": {"enable_thinking": False}},
                    timeout=300,
                )
                elapsed = time.time() - t0
                used = response.json().get("usage", {}).get("completion_tokens", 0)
                total_tokens += int(used)
                total_time += elapsed
                print(f"    {used:>4} tokens in {elapsed:5.1f}s")
            rate = total_tokens / total_time if total_time else 0.0
            print(f"  {label}: {rate:.1f} tok/s over {total_tokens} tokens")
            return rate, total_time
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        time.sleep(3)


def main() -> int:
    print("baseline (no draft model)")
    base_rate, base_time = measure("baseline", draft=False)
    print("\nspeculative (Qwen3-0.6B draft)")
    spec_rate, spec_time = measure("speculative", draft=True)
    delta = (spec_rate / base_rate - 1) * 100 if base_rate else 0.0
    print(f"\n=== {spec_rate:.1f} vs {base_rate:.1f} tok/s  ({delta:+.0f}%) ===")
    print("ADOPT" if delta > 10 else "REJECT — draft competes for the same cores")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
