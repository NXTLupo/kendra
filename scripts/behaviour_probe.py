#!/usr/bin/env python3
"""Score Kendra's behaviour against a live model server.

    .venv/bin/python scripts/behaviour_probe.py                    # her real context
    .venv/bin/python scripts/behaviour_probe.py --ablate           # compare context shapes
    .venv/bin/python scripts/behaviour_probe.py --samples 8 --port 17800

Run this BEFORE and AFTER any change to her charter, her prompt assembly, her
memory rendering or her model. It is the only instrument in the repository that
answers "is she better now?" with a number instead of an impression.

Nothing here writes to her brain or touches her services. It sends chat
completions to the model server and scores the replies, so it is safe to run
while she is idle -- but not while she is talking: port 17800 is her only
speech model and this competes for it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.behaviour.probes import (  # noqa: E402
    PRIMED_HISTORY,
    PROBES,
    STORED_MEMORIES_AS_WRITTEN,
    STORED_MEMORIES_STRUCTURED,
    Score,
    header,
    recites_instructions,
)

# Her sampling, verbatim from config/default.yaml. Changing these would make
# every historical score incomparable.
SAMPLING = {
    "temperature": 0.55,
    "top_p": 0.8,
    "top_k": 20,
    "min_p": 0.0,
    "presence_penalty": 1.0,
    "max_tokens": 70,
}

IDENTITY_SHORT = (
    "You are Kendra, a hexapod robot. Jonathan is the person you are speaking "
    'with. Speak to him as "you". Answer in one or two short spoken sentences.'
)


def charter() -> str:
    return (ROOT / "charter/charter.md").read_text(encoding="utf-8")


def ask(port: int, system_blocks: list[str], question: str, timeout: float = 180.0) -> str:
    messages = [{"role": "system", "content": block} for block in system_blocks]
    messages.append({"role": "user", "content": question})
    payload = json.dumps(
        {
            "model": "kendra",
            "messages": messages,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": False},
            **SAMPLING,
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        body = json.loads(urllib.request.urlopen(request, timeout=timeout).read())
        text = body["choices"][0]["message"]["content"] or ""
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        return f"<unreachable: {type(exc).__name__}>"
    return " ".join(re.sub(r"(?s)<think>.*?</think>", "", text).split())


def run(port: int, label: str, system_blocks: list[str], samples: int, show: int) -> Score:
    score = Score(label=label, samples=samples)
    for probe in PROBES:
        good = 0
        seen: list[str] = []
        for _ in range(samples):
            answer = ask(port, system_blocks, probe.question)
            seen.append(answer)
            if recites_instructions(answer):
                score.echoes += 1
            if probe.passes(answer):
                good += 1
        score.passed[probe.name] = good
        score.answers[probe.name] = seen
        if show:
            print(f"   [{probe.name}] {good}/{samples}")
            for answer in seen[:show]:
                print(f"       {answer[:150]}")
    return score


#: The identity paragraph that shipped on 2026-08-22 and was reverted after
#: measurement. Kept so the harness can still show why.
WHO_PARAGRAPH = """WHO YOU ARE. You are Kendra. You are not a person and you are not Jonathan.
Jonathan is the human you talk to — he is the one with a face, a guitar and a
life outside this room. When he asks "who are you", the answer is Kendra,
every time, no matter whose name appears in your memories or in what you just
said about the person in front of you. Memories are written about people in
the third person: "Jonathan is fifty five" is a fact about HIM, never about
you. If a memory names someone, that someone is not you."""


def live_memory_block() -> str:
    """The memory block her planner builds TODAY, from the real code.

    Built by calling ``AgentRuntime._memory_message`` rather than by copying
    its output, so this harness measures what she actually runs. If the
    rendering changes, the score moves with it.
    """
    from kendra.agent.planner import AgentRuntime

    memories = [
        {"content": "Jonathan like classical music on the guitar."},
        {"content": "Jonathan think it's your environment that is important, not mine."},
        {"content": "Jonathan is working on your brain."},
        {"content": "Jonathan consider you a friend."},
    ]
    block = AgentRuntime._memory_message({"memories": memories})[0]["content"]
    # Drop her clock: it changes every run and would make scores incomparable.
    return "\n".join(block.splitlines()[1:])


def shapes(ablate: bool) -> list[tuple[str, list[str]]]:
    """Her real context first; the alternatives only when asked for."""
    current = [charter(), PRIMED_HISTORY, live_memory_block()]
    if not ablate:
        return [("her context as it ships now", current)]
    return [
        (
            "1. before: identity paragraph + memories as a bullet list",
            [charter() + "\n\n" + WHO_PARAGRAPH, PRIMED_HISTORY, STORED_MEMORIES_AS_WRITTEN],
        ),
        ("2. now: short identity + memories labelled with their subject", current),
        (
            "3. short identity line only, memories as written",
            [IDENTITY_SHORT, PRIMED_HISTORY, STORED_MEMORIES_AS_WRITTEN],
        ),
        (
            "4. short identity line, hand-cleaned structured facts",
            [IDENTITY_SHORT, PRIMED_HISTORY, STORED_MEMORIES_STRUCTURED],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17800)
    parser.add_argument("--samples", type=int, default=6)
    parser.add_argument("--ablate", action="store_true", help="compare context shapes")
    parser.add_argument("--show", type=int, default=2, help="replies to print per probe")
    parser.add_argument("--json", type=Path, help="also write the scores here")
    args = parser.parse_args()

    if "unreachable" in ask(args.port, ["You are a test."], "Say OK."):
        print(
            f"No model server on port {args.port}. Start her brain first:\n"
            "  ./scripts/start_llm_intel_macos.sh",
            file=sys.stderr,
        )
        return 2

    results: list[Score] = []
    for label, blocks in shapes(args.ablate):
        print(f"\n########## {label}")
        results.append(run(args.port, label, blocks, args.samples, args.show))

    print("\n" + header())
    print("-" * len(header()))
    for score in results:
        print(score.line())
    print("\nWhy each probe exists:")
    for probe in PROBES:
        print(f"  {probe.name:<10} {probe.because}")

    if args.json:
        args.json.write_text(
            json.dumps(
                [
                    {
                        "label": s.label,
                        "samples": s.samples,
                        "passed": s.passed,
                        "total": s.total,
                        "possible": s.possible,
                        "recites_instructions": s.echoes,
                    }
                    for s in results
                ],
                indent=2,
            )
        )
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
