"""Long-horizon tasks: plan, work the steps, then report.

This is the one capability DeerFlow has that Kendra genuinely lacked — a
request that needs several stages ("research X, compare it to Y, then tell
me what you think") rather than one answer. DeerFlow itself does not fit
this machine: its own sizing table asks 4-8 vCPU and 8-16 GB for the
harness ALONE, explicitly excluding a local LLM, on a 6-core iMac where
Kendra is already resident and 16 GB of swap is in use. So the idea is
kept and the infrastructure is not: no Docker, no LangGraph, no cloud
model, no sandbox — just her existing tools driven in sequence.

Design constraints, all inherited from hard-won lessons here:

- HALF-DUPLEX. Steps run one at a time on the tool slot, never in
  parallel with a spoken turn, because concurrent inference on this CPU is
  what produced 117-second replies.
- BOUNDED. At most four steps, each with a timeout, so a bad plan cannot
  run forever.
- INTERRUPTIBLE. "Stop" cancels between steps and she reports what she
  has.
- HONEST. Steps that fail are reported as failures; the synthesis may only
  use what the steps actually returned.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

LOG = logging.getLogger(__name__)

# Only genuinely multi-part requests: ordinary questions must never pay
# planning latency. Two clauses joined by "then/and then/after that", or an
# explicit comparison/multi-topic ask.
MULTI_STEP = re.compile(
    r"\b(?:and then|then tell me|after that|followed by|"
    r"compare\s+\w+.{0,40}\b(?:to|with|against)\b|"
    r"research\b.{0,60}\band\b.{0,60}\b(?:research|find|look|compare|tell)|"
    r"(?:first|start by)\b.{0,60}\bthen\b)",
    re.I,
)

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "goal": {"type": "string"},
                    "tool": {"type": "string", "enum": ["research", "recall", "look", "think"]},
                    "query": {"type": "string"},
                },
                "required": ["goal", "tool", "query"],
            },
        }
    },
    "required": ["steps"],
}


@dataclass(slots=True)
class Step:
    goal: str
    tool: str
    query: str
    result: str = ""
    ok: bool = False
    seconds: float = 0.0


@dataclass(slots=True)
class TaskRun:
    request: str
    steps: list[Step] = field(default_factory=list)
    answer: str = ""
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "steps": [
                {"goal": s.goal, "tool": s.tool, "ok": s.ok, "seconds": round(s.seconds, 1)}
                for s in self.steps
            ],
            "answer": self.answer,
            "cancelled": self.cancelled,
        }


class TaskOrchestrator:
    """Plans a multi-part request and works it with her existing tools."""

    def __init__(self, runtime: Any):
        self.runtime = runtime  # AgentRuntime: owns llm, brain, research, vision
        self._cancel = asyncio.Event()

    def wants_orchestration(self, text: str) -> bool:
        return bool(MULTI_STEP.search(text or ""))

    def cancel(self) -> None:
        self._cancel.set()

    async def plan(self, request: str) -> list[Step]:
        """Break the request into at most four concrete steps."""
        raw = await self.runtime.llm.chat(
            [
                {"role": "system", "content": (
                    "Break the request into the FEWEST concrete steps that answer it "
                    "(one to four). Each step picks one tool: 'research' for anything "
                    "current or external, 'recall' for what Kendra already knows about "
                    "Jonathan, 'look' for what her camera can see right now, 'think' "
                    "for reasoning over earlier steps. Give each step a short goal and "
                    "the exact query to use. JSON only."
                )},
                {"role": "user", "content": request},
            ],
            response_schema=STEP_SCHEMA,
            temperature=0.0,
            max_tokens=320,
            id_slot=self.runtime.PLANNER_SLOT,
        )
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        steps = []
        for item in (data.get("steps") or [])[:4]:
            goal, tool, query = item.get("goal"), item.get("tool"), item.get("query")
            if goal and tool and query:
                steps.append(Step(str(goal)[:120], str(tool), str(query)[:200]))
        return steps

    async def _run_step(self, step: Step) -> None:
        started = time.time()
        try:
            if step.tool == "research":
                # The runtime has no long-lived research client; it opens one
                # per use, as the research bypass does.
                from ..ipc import UnixJsonClient

                research = UnixJsonClient(
                    self.runtime.settings.socket_path("research"), timeout=90
                )
                evidence = await asyncio.wait_for(
                    research.call("auto", {"query": step.query}), timeout=90
                )
                sources = (evidence or {}).get("sources", [])[:3]
                step.result = " | ".join(
                    f"{s.get('title', '')}: {str(s.get('snippet') or s.get('text') or '')[:200]}"
                    for s in sources
                ) or "nothing usable came back"
                step.ok = bool(sources)
            elif step.tool == "recall":
                ctx = await self.runtime.brain.context(
                    step.query, limit=4, character_budget=900,
                    include_self_model=False, exclude_kinds=["episode", "observation"],
                )
                memories = [str(m.get("content", "")) for m in ctx.get("memories", [])]
                step.result = " | ".join(memories) or "no memory of that"
                step.ok = bool(memories)
            elif step.tool == "look":
                seen = await asyncio.wait_for(
                    self.runtime.vision.observe(True, step.query), timeout=60
                )
                step.result = str(seen.get("description") or seen.get("visual_scene") or "")
                step.ok = bool(step.result)
            else:  # think
                step.result = await self.runtime.llm.chat(
                    [{"role": "user", "content": step.query}],
                    max_tokens=160, temperature=0.4,
                    id_slot=self.runtime.PLANNER_SLOT,
                )
                step.ok = bool(step.result)
        except Exception as exc:
            step.result = f"failed: {type(exc).__name__}"
            step.ok = False
        step.seconds = time.time() - started
        LOG.info("Task step [%s] %s in %.1fs", step.tool, "ok" if step.ok else "FAILED", step.seconds)

    async def run(self, request: str, on_progress=None) -> TaskRun:
        """Plan, work each step in sequence, then answer from what came back."""
        self._cancel.clear()
        run = TaskRun(request=request)
        run.steps = await self.plan(request)
        if not run.steps:
            return run

        if on_progress:
            await on_progress(
                f"Okay — {len(run.steps)} things to work through. Starting now."
            )
        for index, step in enumerate(run.steps, start=1):
            if self._cancel.is_set():
                run.cancelled = True
                break
            await self._run_step(step)
            if on_progress and index < len(run.steps):
                await on_progress(f"That's {index} of {len(run.steps)}.")

        findings = "\n".join(
            f"- {s.goal}: {s.result[:300]}" for s in run.steps if s.result
        )
        run.answer = (await self.runtime.llm.chat(
            [
                {"role": "system", "content": (
                    "Answer Jonathan out loud in two or three sentences, using ONLY "
                    "the findings below. Say plainly if something could not be found. "
                    "No lists, no markdown."
                )},
                {"role": "user", "content": f"He asked: {request}\n\nWhat you found:\n{findings}"},
            ],
            max_tokens=200, temperature=0.6,
            id_slot=self.runtime.CONVERSATION_SLOT,
        )).strip()
        return run
