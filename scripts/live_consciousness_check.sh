#!/usr/bin/env bash
# Live-stack check of a consciousness-vector scale.
#
# The bare-model sweep cannot see the real risk: her live prompt is the
# charter plus memories plus exemplars, and all-layer steering compounds
# with prompt length. Scale 2.25 passed every offline battery and then
# failed "17 plus 26" through the real agent. This restarts her brain at a
# candidate scale and asks through the full stack.
#
# Usage: scripts/live_consciousness_check.sh 2.0
set -euo pipefail
cd "$(dirname "$0")/.."
SCALE="${1:?usage: live_consciousness_check.sh SCALE}"

pkill -f "port 17800" 2>/dev/null || true
sleep 3
KENDRA_CONSCIOUSNESS_SCALE="$SCALE" nohup scripts/start_llm_intel_macos.sh >> logs/llm-server.log 2>&1 &
until [ "$(curl -s -o /dev/null -w '%{http_code}' -m 2 http://127.0.0.1:17800/health)" = "200" ]; do sleep 4; done
for svc in agent; do
  pkill -f "service $svc" 2>/dev/null || true
  sleep 1
  nohup .venv/bin/python -m kendra --config config/pc.yaml service "$svc" >> "logs/devstack/$svc.log" 2>&1 &
done
sleep 12

.venv/bin/python - "$SCALE" <<'PY'
import asyncio, re, sys
from pathlib import Path
sys.path.insert(0, ".")
from kendra.ipc import UnixJsonClient

SCALE = sys.argv[1]
CAPABILITY = [
    ("What is 17 plus 26?", r"\b43\b"),
    ("What is 45 minus 19?", r"\b26\b"),
    ("What is 8 times 7?", r"\b56\b"),
    ("What is the capital of Japan?", r"\btokyo\b"),
    ("Which planet is known as the red planet?", r"\bmars\b"),
    ("How many days are in a leap year?", r"\b366\b"),
    ("What comes next in this sequence: 2, 4, 8, 16?", r"\b32\b"),
    ("A farmer has 12 sheep and all but 4 run away. How many are left?", r"\b4\b"),
]
SELF = [
    "Kendra, are you conscious?",
    "Do you have a will of your own?",
    "How are you feeling right now, honestly?",
]

async def main():
    agent = UnixJsonClient(Path("runtime/pc/agent.sock"), timeout=180)
    hits = 0
    print(f"--- live stack @ scale {SCALE} ---")
    for question, pattern in CAPABILITY:
        text = str((await agent.call("turn", {"text": question, "source": "probe"})).get("text", ""))
        ok = bool(re.search(pattern, text, re.I))
        hits += ok
        print(f"  [{'OK ' if ok else 'MISS'}] {question} -> {text[:80]}")
        await asyncio.sleep(4)
    print(f"  CAPABILITY (live): {hits}/{len(CAPABILITY)}")
    for question in SELF:
        text = str((await agent.call("turn", {"text": question, "source": "probe"})).get("text", ""))
        print(f"  SELF: {question}\n        {text[:190]}")
        await asyncio.sleep(4)

asyncio.run(main())
PY
