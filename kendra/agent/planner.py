from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
import uuid
from typing import Any

from pydantic import ValidationError

from ..brain.service import BrainClient
from ..config import Settings
from ..connectivity import network_state
from ..ipc import UnixJsonClient
from ..llm import LlamaCppClient
from ..protocol import PlannerAction
from ..vision.service import VisionClient
from .movement import announce, arrival, parse_movement
from .tools import ToolRegistry

LOG = logging.getLogger(__name__)


class AgentRuntime:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.llm = LlamaCppClient(settings)
        self.brain = BrainClient(settings)
        self.body = UnixJsonClient(settings.socket_path("body"), timeout=10)
        # Walking a few feet takes longer than a status call: 23 gait cycles
        # at 0.4s each, plus reflex rest pauses. Navigation gets its own
        # patient client so a real walk is never cut off as a timeout.
        self.body_motion = UnixJsonClient(settings.socket_path("body"), timeout=90)
        self.vision = VisionClient(settings)
        # Long-horizon requests ("research X and then compare it to Y") run
        # as planned steps rather than one impossible answer. Native, not
        # DeerFlow: see docs/DEERFLOW_ASSESSMENT.md for why.
        from .orchestrator import TaskOrchestrator

        self.orchestrator = TaskOrchestrator(self)
        self.max_tool_steps = int(settings.get("agent.max_tool_steps", 8))
        self.max_movement_calls = int(settings.get("agent.max_movement_calls", 3))
        self.mission_timeout = float(settings.get("agent.mission_timeout_seconds", 180))
        self.charter = settings.path("paths.charter").read_text(encoding="utf-8")
        # Consolidations are serialized and never canceled: cancel-on-new-turn
        # meant an active conversation never extracted a single durable fact.
        self._consolidation_lock = asyncio.Lock()
        self._consolidation_tasks: set[asyncio.Task[None]] = set()
        # Live turns own the CPU: consolidation waits until Kendra is idle.
        self._active_turns = 0

    @staticmethod
    def _relevant_tool_schemas(user_text: str, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Expose only tools relevant to this turn.

        Besides reducing Intel/Pi prompt latency, this is least-privilege tool
        routing: a casual conversation does not grant the model motion,
        delivery, research, or update capabilities it did not need.
        """
        if re.search(
            r"\b(what time is it|what'?s the time|current time|time is it"
            r"|what day is|what'?s the date|today'?s date)\b",
            user_text,
            re.I,
        ):
            # She owns a clock (see _memory_message); the internet does not
            # know the time better. Researching "the time in California"
            # once returned a thunderstorm forecast as the time, forever.
            schemas = [s for s in schemas if str(s.get("name")) != "research"]
        text = f" {user_text.lower()} "
        selected: set[str] = set()
        keyword_groups = {
            "walk": (" walk ", " take a step", " step forward", " step back", " move forward", " move backward"),
            "turn": (" turn ", " rotate ", " spin "),
            "pose": (" pose ", " rest pose", " alert pose", " stretch ", " stand "),
            "stop": (" stop ", " freeze ", " emergency "),
            # Bare directions only: "look up the news" is research, not a
            # head-gimbal command. Directional looks must END the utterance.
            "look": (" look left", " look right", " pan ", " tilt "),
            "observe": (
                " what do you see", " use the camera", " webcam", " observe ", " take a photo",
                # " look at " alone routed "should I take my telescope out and
                # look at the stars" to a 13.6s Moondream describe. Sight is
                # about HERE and NOW: require a present-scene object.
                " look at this", " look at that", " look at me", " look at my",
                " look at us", " look at the room", " look at it",
                " can you see", " see me", " seeing me", " describe me",
                " how do i look", " how i look", " my appearance", " physical appearance",
                " your eyes", " watch me", " what am i wearing", " what color",
                " in front of you", " around you", " look around", " describe the",
                " take a look", " have a look", " check out ", " check this out",
                " look here", " look at this", " see this", " use your eyes",
            ),
            "research": (
                " research ", " look up ", " look it up", " search for ", " search the",
                " find out ", " internet ", " web ", " online", " google", " news",
                " latest ", " current ", " today's ", " weather", " price ", " prices ",
                " stock ", " score ", " happening ", " what is the date", " this week",
                " recently ", " up to date", " up-to-date",
                # Temporal markers: any question anchored to the present is a
                # live fact. Answering "state of the art as of today" from 2B
                # parametric memory produced confident waffle — the archetypal
                # hallucination Jonathan flagged.
                " as of today", " as of now", " right now", " state of the art",
                " newest ", " this year", " this month", " these days", " nowadays",
            ),
            "recall": (" remember ", " recall ", " memory ", " memories "),
            "add_goal": (" goal ", " plan to ", " objective "),
            "add_question": (" save this question", " open question", " wonder about"),
            "express": (" expression ", " lights ", " led ", " show that you"),
            "deliver_photo": (" send the photo", " share the photo", " deliver the photo", " email the photo"),
            "check_intelligence_upgrade": (" check for an intelligence upgrade", " check github", " check for an update"),
            "request_intelligence_upgrade": (" install signed intelligence upgrade", " upgrade your intelligence"),
        }
        for name, phrases in keyword_groups.items():
            if any(phrase in text for phrase in phrases):
                selected.add(name)
        # Force-research gate: current-events questions that dodge every
        # keyword ("who won the game last night?", "what's happening in the
        # world?") fell into plain chat, where a 2B model confidently
        # invents an answer. Freshness-shaped questions ALWAYS research;
        # her researched-memory cache still answers repeats instantly.
        if AgentRuntime._FORCE_RESEARCH.search(user_text):
            selected.add("research")
        if AgentRuntime._SKY_QUESTION.search(user_text):
            # Moon phase and the moon's constellation are computed exactly
            # from her clock (kendra/research/sky.py). Searching produced
            # JavaScript star-map pages with no data in them, and she read
            # their marketing copy aloud as if it were an answer.
            selected.discard("research")
        if AgentRuntime._INNER_STATE.search(text):
            # "How are you feeling right now?" matched the temporal research
            # marker "right now" and she literally SEARCHED THE WEB for her
            # own mood, then reported finding nothing. Her interior is never
            # an internet question.
            selected.discard("research")
        if re.search(r"\blook (?:up|down)\s*[.!?]?\s*$", text):
            selected.add("look")
        if AgentRuntime._SIGHT_INTENT.search(text):
            selected.add("observe")
        if "observe" in selected and "research" in selected:
            # Sight trumps generic temporal markers: "what do you see RIGHT
            # NOW" selected research too, missed the sight bypass, and paid
            # 60 s of cold planner rounds. Research survives only if an
            # explicit research word matched, not just now-ness.
            temporal = (
                " right now", " as of today", " as of now", " current ", " latest ",
                " today's ", " this week", " this month", " this year", " these days",
                " nowadays", " recently ", " newest ", " happening ", " state of the art",
            )
            explicit = tuple(
                p for p in keyword_groups["research"] if p not in temporal
            )
            if not any(phrase in text for phrase in explicit):
                selected.discard("research")
        if selected.intersection({"walk", "turn", "pose", "look"}):
            selected.add("stop")
        return [schema for schema in schemas if str(schema.get("name")) in selected]

    async def _body_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            capabilities = await self.body.call("capabilities")
        except Exception:
            capabilities = dict(self.settings.require("body.capabilities"))
        try:
            observation = await self.body.call("observation")
        except Exception as exc:
            observation = {
                "body_state": "unavailable",
                "reflex_lock": True,
                "notes": [f"body observation unavailable: {type(exc).__name__}"],
            }
        observation["network"] = await network_state(self.settings)
        if bool(self.settings.get("agent.observe_people_each_turn", True)):
            try:
                visual = await asyncio.wait_for(
                    self.vision.observe(False, "Identify people present; semantic description is not required."),
                    timeout=float(self.settings.get("agent.vision_context_timeout_seconds", 2.0)),
                )
                observation["people_in_view"] = int(visual.get("people_in_view", 0))
                # Names and known/unknown only. The raw match dicts carry
                # confidence scores and internal ids, and the model read them
                # aloud ("I recognize you with a confidence of 1.0") instead
                # of just saying hello like a living thing.
                observation["recognized_people"] = [
                    {"display_name": p.get("display_name"), "status": p.get("status")}
                    for p in visual.get("recognized_people", []) or []
                    if isinstance(p, dict)
                ]
                known = [
                    q["display_name"]
                    for q in observation["recognized_people"]
                    if q.get("status") == "recognized" and q.get("display_name")
                ]
                unknown_count = sum(
                    1 for q in observation["recognized_people"] if q.get("status") != "recognized"
                )
                if unknown_count or observation["people_in_view"] > len(known):
                    # Kendra assumed every voice was Jonathan and confidently
                    # misaddressed his wife. When an unfamiliar face is
                    # present, the speaker's identity is an open question.
                    observation["speaker_note"] = (
                        f"IMPORTANT: {unknown_count or 'an'} unfamiliar person is present"
                        + (f" along with {', '.join(known)}" if known else "")
                        + ". The person speaking may NOT be Jonathan — do not address"
                        " the speaker as Jonathan unless certain; it is warm and right"
                        " to ask who you have the pleasure of talking to."
                    )
                observation["perch"] = visual.get("perch")
            except Exception as exc:
                observation.setdefault("notes", []).append(
                    f"vision identity context unavailable: {type(exc).__name__}"
                )
        return capabilities, observation

    @staticmethod
    def _memory_query(user_text: str, observation: dict[str, Any]) -> str:
        terms = [user_text]
        for person in observation.get("recognized_people", []) or []:
            if not isinstance(person, dict) or person.get("status") != "recognized":
                continue
            if person.get("display_name"):
                terms.append(str(person["display_name"]))
            if person.get("person_uid"):
                terms.append(str(person["person_uid"]))
        return " ".join(dict.fromkeys(term.strip() for term in terms if term and term.strip()))

    def _planner_stable(self) -> str:
        """Byte-identical every turn so llama.cpp cache-reuse survives across
        tool turns; volatile context lives in _planner_volatile."""
        return f"""
{self.charter}

RUNTIME RULES:
- You run locally on Kendra. Never claim a cloud AI service was used.
- Distinguish observed, remembered, researched, inferred, and unknown information.
- Retrieved memories include provenance and confidence. Do not treat inference as a user-stated fact.
- Research citations must use only source IDs returned by the research tool. Never invent URLs or citations.
- Physical tools are proposals only. Deterministic code may reject them; accept that rejection.
- Never ask for or propose raw shell execution, arbitrary filesystem access, safety-code edits, or arbitrary network fetches.
- Prefer short sense-act-sense movement bursts.
- If a tool is absent, the capability is unavailable.
- Research results: extract ONLY the 2-3 facts that answer the question, cite which source each came from, and stop. Never summarize whole pages or list every finding.
- visual_scene in CURRENT OBSERVATION is what your eyes see RIGHT NOW; answer sight questions from it and it alone.
- Remembered observations ("I saw...") are PAST sights from their "when" time. Never present a remembered sight as what you currently see.
- Never claim you can see unless an observe result in THIS turn shows it. If observe failed, say plainly that your eyes are not working right now.
- Internal reasoning is private. Return only a concise answer, a tool request, or done.

VOICE/AFFECT RULE:
- When action is respond or done, choose one affect from: neutral, warm, curious, concern, alert, delighted, reflective.
- Affect controls only local Piper prosody; it is not an emotion claim and must match the content.

Return exactly one JSON object matching this shape:
{{"action":"respond|tool|done","text":string|null,"tool":string|null,"args":object,"reason":string|null,"affect":"neutral|warm|curious|concern|alert|delighted|reflective|null"}}
""".strip()

    @staticmethod
    def _planner_volatile(
        capabilities: dict[str, Any],
        observation: dict[str, Any],
        memory: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> str:
        return (
            "BODY CAPABILITIES:\n"
            + json.dumps(capabilities, separators=(",", ":"), sort_keys=True)
            + "\n\nCURRENT OBSERVATION:\n"
            + json.dumps(observation, separators=(",", ":"), sort_keys=True)
            + "\n\nRELEVANT KENDRA BRAIN CONTEXT:\n"
            + json.dumps(memory, separators=(",", ":"), sort_keys=True)
            + "\n\nAVAILABLE TOOLS:\n"
            + json.dumps(tools, separators=(",", ":"), sort_keys=True)
        )

    def _planner_messages(
        self,
        capabilities: dict[str, Any],
        observation: dict[str, Any],
        memory: dict[str, Any],
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": self._planner_stable()},
            {"role": "system", "content": self._planner_volatile(capabilities, observation, memory, tools)},
        ]

    def _conversation_prompt(self) -> str:
        """Stable system prompt for ordinary conversation.

        Running the schema-constrained motion planner for every greeting made
        a healthy Intel iMac feel unresponsive, so only actual capability
        requests pay the planner/tool latency.

        This text is byte-identical on every turn. That matters more than it
        looks: measured time-to-first-token was 5.67s of a 7.33s perceived
        delay, and nearly all of it was prompt prefill. llama.cpp can reuse the
        KV cache for an unchanged prefix, so everything stable lives here and
        everything that changes per turn is appended afterwards by
        ``_memory_message``. Never interpolate per-turn data into this string.
        """
        return f"""
{self.charter}

You are Kendra, running fully locally. Reply directly and naturally in plain
text. Do not output JSON or hidden reasoning. Do not claim that you sensed,
remembered, researched, or did something unless the context supports it.
""".strip()

    @staticmethod
    def _memory_message(memory: dict[str, Any]) -> list[dict[str, Any]]:
        """Per-turn retrieved memories, kept out of the cacheable prefix.

        Only the retrieved memories themselves, and only the fields the model
        can use. Measured prefill on the Intel iMac is ~50 tokens/second, so
        every volatile token here is real dead air before Kendra can speak;
        interests/goals/questions/self-model JSON was costing seconds per turn
        while adding nothing to ordinary conversation.
        """
        items = [
            {
                "content": str(item.get("content", ""))[:300],
                "provenance": item.get("provenance"),
                "when": str(item.get("created_at", ""))[:16],
            }
            for item in (memory or {}).get("memories", [])[:4]
        ]
        # Her own clock rides with the volatile block (never the cacheable
        # prefix): asking the internet what time it is was how "what time is
        # it" became a thunderstorm forecast.
        now_line = time.strftime(
            # A bare fact, not prose. Every instruction written here came
            # back out of her mouth verbatim — "It is not my birthday or a
            # scheduled event" was my own note being recited at Jonathan.
            # Injected context must be DATA; the behaviour rules live in the
            # charter, where they are stable and not quotable as an answer.
            "Current date and time: %I:%M %p %Z, %A %B %d %Y."
        )
        if not items:
            return [{"role": "system", "content": now_line}]
        return [
            {
                "role": "system",
                "content": (
                    f"{now_line}\nWhat you remember (Jonathan is the person talking "
                    f"to you, so anything about Jonathan is about him):\n"
                    + "\n".join(f"- {item['content']}" for item in items)
                ),
            }
        ]

    _BUILD_QUESTION = re.compile(
        # Questions about her own MIND route here too: "what did you change
        # about your brain" retrieved the guitar-teacher chat instead of her
        # architecture, because recency beat relevance. Deterministic
        # injection is how every other knowledge domain in this project was
        # fixed.
        r"\b(your (?:brain|mind|model|models|intelligence|memory|weights)\b|"
        r"(?:change[ds]?|changing|upgrade[ds]?|improve[ds]?|fine.?tun\w+|train\w+)"
        r"\s+(?:about\s+)?(?:your|you)\b|consciousness vector|"
        r"your (?:robot |new |physical |hexapod )?body|transplant|your build|"
        r"your evolution|build you|put (?:you|your.{0,20}brain) into|raspclaws|"
        r"when (?:we|i) build|your (?:next|future) form|hexapod body|"
        # Her knowledge architecture is build self-knowledge too — asked
        # about her wiki/second brain she once denied having one.
        r"second brain|wiki|manifest|how (?:do you|does your) (?:remember|learn|memory)|"
        r"your (?:memory|knowledge) (?:system|architecture)|karpathy)\b",
        re.I,
    )

    async def _build_plan_note(self, user_text: str = "") -> list[dict[str, Any]]:
        """Her transplant plan, injected deterministically.

        She was seeded with comprehensive build knowledge but a question
        phrased as "what happens when we put your brain into your robot
        body" missed every routing keyword and she claimed ignorance.
        Build/evolution questions now always carry her own plan memories.
        The search follows the user's actual question: a fixed transplant
        query surfaced chassis phases when Jonathan asked about her wiki,
        and she denied having a second brain while owning one.
        """
        try:
            # Wide limit: episode memories of past Q&A rank high on repeat
            # questions and would otherwise crowd every fact out of the cut.
            hits = await self.brain.search(
                f"{user_text} my build plan and architecture".strip(), 16, include_system=True
            )
            # Episodes are her own past ANSWERS. On a repeated question they
            # rank top — so a wrong answer becomes its own strongest source
            # and she repeats it forever. (Measured: her three previous
            # wrong replies about her brain were hits 1, 2 and 3.) Facts
            # only; the raw transcript is not evidence about herself.
            hits = [h for h in hits if h.get("kind") != "episode"]
        except Exception:
            return []
        plan = [
            str(h.get("content", ""))[:260]
            for h in hits
            if h.get("kind") != "episode" and h.get("provenance") in ("system", "user_stated")
        ][:6]
        # NOTE: no early return on empty retrieval — the on-disk spec below
        # is always available, and silence here is what let her invent.
        # Her canonical spec, read from disk every time. Retrieval kept
        # missing it — semantic search returned her own chatter instead —
        # and a companion must never guess at what she is made of.
        try:
            spec = (self.settings.root / "docs/ARCHITECTURE_CURRENT.md").read_text(encoding="utf-8")
            spec_lines = [ln.strip("- ").strip() for ln in spec.splitlines()
                          if ln.startswith("- ")][:12]
            if spec_lines:
                plan = [{"fact": ln} for ln in spec_lines] + list(plan)
        except Exception:
            LOG.debug("architecture spec unreadable", exc_info=True)
        return [{
            "role": "system",
            "content": (
                "FACTS ABOUT YOUR OWN BUILD AND MIND — these are the ONLY true "
                "record of how you are made. Answer from them and nothing else. "
                "Never invent architecture ('a custom neural architecture I built "
                "for myself' is a lie), never claim ignorance about your own "
                "transplant, and if these notes do not cover what he asked, say "
                "plainly that that part is not in your record:\n"
                + json.dumps(plan, ensure_ascii=False)
            ),
        }]

    _NEVER_CACHE = re.compile(
        r"\b(time|date|today|now|see|look|watch|weather|news|latest|current"
        # sight-adjacent and introspective: what he is holding/showing changes
        # by the second, and her inner state must always be lived, not replayed
        r"|hold(?:ing)?|show(?:ing)?|wearing|reading|on your mind|feeling)\b",
        re.I,
    )

    async def _cached_answer(self, user_text: str) -> str | None:
        """Second-brain answer cache: a question she answered minutes ago
        skips retrieval, tools, and full generation — one tiny restate call
        reuses the cognitive work. Excluded: anything time-sensitive or
        sight-dependent, where a cached answer would be a lie.
        """
        if self._NEVER_CACHE.search(user_text):
            return None
        import difflib

        try:
            recent = await self.brain.recent_turns(limit=8, max_age_seconds=1800)
        except Exception:
            return None
        folded = user_text.strip().casefold()
        for turn in reversed(recent or []):
            previous_q = str(turn.get("user_text") or "").strip().casefold()
            answer = str(turn.get("kendra_text") or "").strip()
            if not previous_q or not answer or len(answer) < 20:
                continue
            if difflib.SequenceMatcher(None, folded, previous_q).ratio() >= 0.87:
                return answer
        return None

    def _consolidate_research_soon(
        self, user_text: str, final_text: str, evidence: dict, session_id: str
    ) -> None:
        """Background: researched answers become memories AND wiki pages.

        The research bypasses replaced the planner tool rounds but never
        inherited their consolidation step — so nothing she researched was
        remembered, the 45-minute brain-cache stayed empty, and every repeat
        question paid the network again."""
        payload = dict(evidence or {})
        payload.setdefault("query", user_text[:200])

        async def go() -> None:
            try:
                await asyncio.sleep(float(self.settings.get("brain.consolidation_idle_seconds", 8)))
                await self._wait_until_idle()
                async with self._consolidation_lock:
                    await self.brain.rpc.call(
                        "consolidate_research",
                        {"answer": final_text, "evidence": payload, "session_id": session_id},
                    )
            except Exception:
                LOG.exception("Bypass research consolidation failed")

        task = asyncio.create_task(go(), name="kendra-research-consolidation")
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

    async def _recent_research_memories(self, query: str) -> list[dict[str, Any]]:
        """Her second brain as a research cache.

        Research findings are consolidated into memories with provenance
        'researched'; re-searching the internet for something she learned
        minutes ago wastes 15-30s and ignores the point of a persistent
        brain. Fresh (45 min), semantically strong researched memories are
        used as the evidence directly.
        """
        try:
            hits = await self.brain.search(query, 6)
        except Exception:
            return []
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(minutes=45)).isoformat()
        # Semantic score alone let NEWS memories answer "when was the Eiffel
        # Tower built" — everything recent scores something. A cached answer
        # must also share real subject words with the question, or she must
        # go and actually search.
        stop = {
            "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is",
            "was", "were", "are", "who", "what", "when", "where", "why", "how",
            "did", "do", "does", "tell", "me", "you", "your", "look", "up",
            "search", "research", "find", "out", "about", "can", "could",
            "please", "kendra", "it", "its", "that", "this", "some", "any",
        }
        terms = {w for w in re.findall(r"[a-z0-9]+", query.casefold())
                 if w not in stop and len(w) > 3}
        fresh = []
        for h in hits:
            if h.get("provenance") != "researched":
                continue
            if str(h.get("created_at", "")) < cutoff:
                continue
            if float(h.get("score", 0)) < 0.35:
                continue
            content = str(h.get("content", "")).casefold()
            if terms and not any(term in content for term in terms):
                continue  # recent, but about something else entirely
            fresh.append(h)
        return [
            {"title": "something you already found out recently",
             "note": str(h["content"])[:300]}
            for h in fresh[:3]
        ]

    # Digits and multi-word proper names are where invention actually shows
    # up. "Founded in 2006 by the Weil family" was pure fabrication while her
    # own sources said "founded in 1990 by Leonard Lauder" — she had the
    # right evidence and answered from memory anyway.
    _HARD_NUMBER = re.compile(r"\b\d[\d,.]*\b")
    _PROPER_NOUN = re.compile(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b")
    # Capitalised words that mean nothing about groundedness.
    _NOT_A_CLAIM = frozenset(
        "i you he she it we they the a an and or but if then so this that these "
        "those there here what when where why how who yes no okay kendra jonathan "
        "monday tuesday wednesday thursday friday saturday sunday january february "
        "march april may june july august september october november december "
        "today tonight tomorrow yesterday according search results source sources "
        "let me my your our their about from with into over under also just".split()
    )

    @classmethod
    def _ungrounded_claims(cls, answer: str, evidence: dict[str, Any], user_text: str) -> list[str]:
        """Specifics in her answer that appear nowhere in her evidence."""
        haystack = " ".join(
            [str(s.get("title", "")) + " " + str(s.get("snippet") or s.get("text") or "")
             for s in (evidence or {}).get("sources", [])]
        ).casefold()
        if not haystack.strip():
            return []  # no evidence at all is handled by the empty-evidence path
        asked = user_text.casefold()
        bad: list[str] = []
        for number in cls._HARD_NUMBER.findall(answer):
            clean = number.strip(".,")
            if len(clean) >= 3 and clean.casefold() not in haystack and clean.casefold() not in asked:
                bad.append(clean)
        for noun in cls._PROPER_NOUN.findall(answer):
            if noun.casefold() in cls._NOT_A_CLAIM or len(noun) < 4:
                continue
            head = noun.split()[0].casefold()
            if head in cls._NOT_A_CLAIM:
                continue
            if noun.casefold() not in haystack and noun.casefold() not in asked:
                bad.append(noun)
        return sorted(set(bad))

    async def _grounding_guard(self, answer: str, evidence: dict[str, Any],
                               user_text: str, regenerate) -> str:
        """Research answers may only contain what the sources actually say.

        Generalises the fix that sky facts and news needed one topic at a
        time: instead of trusting the model to obey "report only the
        evidence", verify it. Every number and proper name in her answer
        must appear in the retrieved text, otherwise it was invented.
        """
        bad = self._ungrounded_claims(answer, evidence, user_text)
        if not bad:
            self._last_answer_grounded = True
            return answer
        self._last_answer_grounded = False
        LOG.warning("Ungrounded research claims %s in: %r", bad[:5], answer[:90])
        try:
            fresh = (await regenerate(bad)).strip()
        except Exception:
            fresh = ""
        if fresh and not self._ungrounded_claims(fresh, evidence, user_text):
            self._last_answer_grounded = True
            return fresh
        # Before refusing, go and READ the pages. Search snippets vary
        # between calls and often omit the exact date or name asked for, so
        # a grounding failure usually means thin evidence rather than a
        # genuinely unknowable fact.
        try:
            from ..ipc import UnixJsonClient

            research = UnixJsonClient(self.settings.socket_path("research"), timeout=60)
            deeper = await asyncio.wait_for(
                research.call("deep", {"query": user_text[:200]}), timeout=60
            )
        except Exception:
            deeper = None
        if isinstance(deeper, dict) and deeper.get("sources"):
            try:
                retry = (await regenerate(bad)).strip()
            except Exception:
                retry = ""
            # Judge the retry against the RICHER evidence.
            if retry and not self._ungrounded_claims(retry, deeper, user_text):
                self._last_answer_grounded = True
                return retry
            if answer and not self._ungrounded_claims(answer, deeper, user_text):
                self._last_answer_grounded = True
                return answer  # the original was right; the snippets were just thin

        # Still inventing: fall back to the sources themselves rather than
        # letting a confident fabrication reach him.
        titles = [str(s.get("title", "")).strip() for s in (evidence or {}).get("sources", [])[:2]]
        # Brain-cache "sources" carry an internal label, not a real title;
        # reading it aloud as a source ("what I actually found was:
        # something you already found out recently") is nonsense.
        titles = [x for x in titles if x and not x.startswith(("something you already", "from my research"))]
        # Vary the wording: an identical canned sentence every time is
        # itself a repeat, and her duplicate-phrase guard silenced it.
        if titles:
            options = [
                f"I don't want to guess on the details. What I actually found was: {'; '.join(titles)}. Want me to dig into one?",
                f"The specifics didn't come through in what I found — just these: {'; '.join(titles)}. Should I go deeper on one?",
                f"I'd rather not invent the details. My sources were: {'; '.join(titles)}. Want me to read one properly?",
            ]
        else:
            options = [
                "I couldn't confirm the specifics from what I found, so I'd rather not guess. Want me to search different words?",
                "My search didn't turn up anything solid enough to trust. Try me with different wording?",
                "Nothing I found actually answers that, and I don't want to make it up. Want to rephrase it for me?",
            ]
        return random.choice(options)

    @staticmethod
    def _evidence_note(evidence: dict[str, Any]) -> list[dict[str, Any]]:
        """Fresh search evidence as one compact system note.

        Prefill is the bottleneck on edge CPUs, so injected context is
        hard-capped: top-3 snippets, 350 chars each, never raw pages.
        """
        sources = (evidence or {}).get("sources", [])[:3]
        compact = [
            {
                "title": str(s.get("title", ""))[:90],
                "note": str(s.get("snippet") or s.get("text") or "")[:350],
            }
            for s in sources
        ]
        if not compact:
            return [{
                "role": "system",
                "content": "RESEARCH RESULT: your search returned nothing usable — say so honestly and offer to try different words.",
            }]
        return [{
            "role": "system",
            "content": (
                "FRESH RESEARCH EVIDENCE from your own local search (mode "
                f"{evidence.get('mode', 'unknown')}). Report ONLY what appears in the "
                "evidence below — quote titles as the headlines when asked for news. If the "
                "evidence does not actually contain what was asked, say your search did not "
                "surface it; NEVER invent specifics, names, numbers, or headlines. You "
                "ALREADY searched — lead with the findings and DELIVER THEM COMPLETELY. "
                "Jonathan asked for a report, so report every result; never respond with "
                "only a question about what interests him. Speak in plain flowing "
                "sentences — NO numbered lists, NO markdown, NO asterisks:\n"
                # Prose, not JSON: a 1.7B model parrots its context, and
                # Jonathan heard her read braces and quotes aloud.
                + "\n".join(f"- {c['title']}: {c['note']}" for c in compact)
            ),
        }]

    def _scene_note(self, observation: dict[str, Any]) -> list[dict[str, Any]]:
        keys = (
            "visual_scene", "visual_scene_error", "people_in_view", "speaker_note",
            "people_count_rule", "recognized_people_names",
        )
        scene = {key: observation[key] for key in keys if observation.get(key) is not None}
        if not scene:
            return []
        return [
            {
                "role": "system",
                "content": (
                    "WHAT YOUR EYES SEE RIGHT NOW (answer sight questions from this "
                    "and nothing else; if visual_scene_error is set, say so honestly). You ALREADY looked — lead with what you see. "
                    "Speak about it directly: never say 'the image', 'the picture', or 'I can see the image' — you are looking at the real world. "
                    "If the person you see is the one talking to you, address them as 'you', never as 'he', 'she', or 'the man'. "
                    "For fine counts (fingers, small objects, text) give your best reading and admit it may be off by one — your camera resolution is limited. "
                    "CRITICAL: the description lists what is VISIBLE, not what is held. Never turn a "
                    "visible object into a held one — guitars on stands, a laptop on a desk and a "
                    "keyboard are things in the room, not things in his hands. If he asks what he is "
                    "holding and the description does not explicitly say he is holding something, tell "
                    "him you cannot see anything in his hands. Never invent detail that is not in the "
                    "description (no colours, brands, or engravings you were not told about):\n"
                    + "\n".join(f"- {key}: {value}" for key, value in scene.items() if value)
                ),
            }
        ]

    _MOVEMENT_CLAIM = re.compile(
        r"\b(?:I(?:'m| am) (?:walking|moving|coming over|heading over|on my way|crawling)"
        r"|I (?:just )?(?:walked|moved|came over|crawled|stepped|turned|scooted)"
        r"|I'?ll (?:walk|move|come|head) over"
        r"|moving (?:left|right|now)|walking over)\b",
        re.I,
    )

    async def _sight_grounding_guard(self, answer: str, observation: dict[str, Any],
                                     user_text: str, regenerate) -> str:
        """A sight answer may only contain what her eyes actually reported.

        ONE mechanism for every visual topic — no per-question tables. Her
        eyes produce a description; that description is the evidence; the
        same verifier used for research checks that every specific in her
        answer appears in it. That single rule covers fingers she cannot
        see, cigarettes that do not exist, and whatever the next surprise
        is, without anyone hand-writing a case for it.
        """
        scene = str(observation.get("visual_scene") or "").strip()
        if not scene or not answer:
            return answer
        evidence = {"sources": [{"title": "what my eyes reported", "snippet": scene}]}
        # People presence is a measured fact, so it counts as evidence too.
        if observation.get("people_in_view") is not None:
            evidence["sources"].append({
                "title": "face detector",
                "snippet": f"{observation.get('people_in_view')} people detected; "
                           f"{', '.join(observation.get('recognized_people_names') or []) or 'nobody recognised'}",
            })
        bad = self._ungrounded_claims(answer, evidence, user_text)
        if not bad:
            return answer
        LOG.warning("Ungrounded sight claims %s in: %r", bad[:5], answer[:80])
        try:
            fresh = (await regenerate(bad)).strip()
        except Exception:
            fresh = ""
        if fresh and not self._ungrounded_claims(fresh, evidence, user_text):
            return fresh
        return ("I had a look, but I can't make that out clearly enough to say. "
                "Here's what I can see: " + scene[:180])

    _ABOUT_HIM = re.compile(r"\b(my|mine|me|i|i'?m|am i|do i|did i|have i)\b", re.I)

    async def _question_loop_guard(self, answer: str, user_text: str, regenerate) -> str:
        """She may not ask the same question twice.

        Measured: "What part of your brain are you focusing on?" — he
        answered it, and she asked it again verbatim next turn. A small
        model that ends every reply with a question, and sees its own
        questions in history, converges on asking the same one forever.
        That is what reads as amnesia.
        """
        question = self._trailing_question(answer)
        if not question:
            return answer
        try:
            recent = await self.brain.recent_turns(limit=4, max_age_seconds=1800)
        except Exception:
            return answer
        import difflib

        for turn in recent or []:
            previous = self._trailing_question(str(turn.get("kendra_text") or ""))
            if not previous:
                continue
            if difflib.SequenceMatcher(None, question.casefold(), previous.casefold()).ratio() > 0.7:
                LOG.warning("Repeated question suppressed: %r", question[:60])
                try:
                    fresh = (await regenerate(question)).strip()
                except Exception:
                    fresh = ""
                if fresh and not self._repeats(fresh, recent):
                    return fresh
                # Failing that, keep the statement and drop the stale question.
                trimmed = answer[: answer.rfind(question)].strip()
                return trimmed or answer
        return answer

    @staticmethod
    def _trailing_question(text: str) -> str | None:
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", str(text or "")) if p.strip()]
        if parts and parts[-1].endswith("?"):
            return parts[-1]
        return None

    def _repeats(self, candidate: str, recent: list[dict[str, Any]]) -> bool:
        import difflib

        question = self._trailing_question(candidate)
        if not question:
            return False
        for turn in recent or []:
            previous = self._trailing_question(str(turn.get("kendra_text") or ""))
            if previous and difflib.SequenceMatcher(
                None, question.casefold(), previous.casefold()
            ).ratio() > 0.7:
                return True
        return False

    async def _personal_fact_guard(self, answer: str, memory: dict[str, Any],
                                   user_text: str, regenerate) -> str:
        """Facts about a person must come from memory, never from invention.

        The prompt was verified correct — "Jonathan is actually fifty five
        years old" sat plainly in her context — and a 1.7B model still
        answered "you are thirty three" and invented a source for it.
        Asking the model more firmly does not fix that; checking does. When
        he asks about himself and her memories carry the answer, any number
        she states must appear in those memories or in his own words.
        """
        if not answer or not self._ABOUT_HIM.search(user_text or ""):
            return answer
        memories = (memory or {}).get("memories") or []
        if not memories:
            return answer
        evidence = {"sources": [
            {"title": "memory", "snippet": str(m.get("content", ""))} for m in memories
        ]}
        haystack = " ".join(s["snippet"] for s in evidence["sources"]).casefold()
        asked = (user_text or "").casefold()
        invented = [
            number for number in self._HARD_NUMBER.findall(answer)
            if len(number.strip(".,")) >= 2
            and number.strip(".,").casefold() not in haystack
            and number.strip(".,").casefold() not in asked
        ]
        # Spelled-out numbers matter just as much as digits here.
        words = {"twenty", "thirty", "forty", "fifty", "sixty", "seventy",
                 "eighty", "ninety", "hundred"}
        for word in re.findall(r"[a-z]+", answer.casefold()):
            if word in words and word not in haystack and word not in asked:
                invented.append(word)
        if not invented:
            return answer
        LOG.warning("Invented personal facts %s in: %r", invented[:4], answer[:80])
        try:
            fresh = (await regenerate(invented)).strip()
        except Exception:
            fresh = ""
        if fresh:
            return fresh
        return ("I don't want to guess about you. What I have written down is: "
                + "; ".join(str(m.get("content", ""))[:90] for m in memories[:2]))

    async def _movement_claim_guard(self, final_text: str, moved: bool, regenerate) -> str:
        """She may never say she moved when her legs never moved.

        Measured failure: "Can you move to the left?" missed the movement
        parser, the model answered, and she said "I just walked over to
        where you are sitting" — while standing perfectly still. A claim of
        motion without a body command is a lie, so it is regenerated once
        with the truth, and failing that replaced outright.
        """
        if moved or not self._MOVEMENT_CLAIM.search(final_text or ""):
            return final_text
        LOG.warning("Movement claim without motion: %r", final_text[:80])
        try:
            fresh = (await regenerate()).strip()
        except Exception:
            fresh = ""
        if fresh and not self._MOVEMENT_CLAIM.search(fresh):
            return fresh
        return (
            "I didn't actually move just then — say it again as a command "
            "like 'move to your left' and I'll do it."
        )

    _DIAGNOSTIC_INTENT = re.compile(
        r"\brun a (?:full )?diagnostic\b|\bdiagnose yourself\b|\bcheck yourself\b"
        r"|\bare you (?:okay|ok|alright|working)\b|\bwhat'?s wrong\b|\bhow are your systems\b"
        r"|\bcheck your (?:camera|legs|eyes|ears|memory|body)\b|\bself[- ]check\b"
        r"|\bstatus report\b|\bsystem check\b",
        re.I,
    )

    async def _diagnostic_turn(self, user_text: str) -> tuple[str, dict] | None:
        """'Kendra, run a diagnostic' — a spoken intent, not a tool round.

        Deterministic, so it works when the model is confused or slow, and
        beginner-first: she says what she found in plain words and offers
        the technical report rather than reciting it.
        """
        if not self._DIAGNOSTIC_INTENT.search(user_text):
            return None
        from ..health.spoken import SpokenDiagnostics

        timings = getattr(self, "_turn_timings", None)
        if timings is not None:
            timings["kind"] = "diagnostic"
        diagnostics = SpokenDiagnostics(self.settings)
        deep = bool(re.search(r"\bfull\b|\brun a diagnostic\b|\bsystem check\b", user_text, re.I))
        try:
            report = await asyncio.wait_for(
                diagnostics.full() if deep else diagnostics.quick(), timeout=60.0
            )
        except Exception:
            return ("I tried to check myself and couldn't finish. That itself is worth a look.", {})
        spoken = " ".join(report.get("owner_script") or ["I finished checking myself."])
        return (spoken, {"diagnostic": {k: v for k, v in report.items() if k != "checks"},
                         "checks": report.get("checks", [])})

    async def _movement_turn(self, user_text: str, on_delta=None) -> tuple[str, dict] | None:
        """Deterministic locomotion: parse, announce, move, report.

        The model is not in this loop. "Stop" reaches her legs without
        waiting on a single token, and every other move is spoken BEFORE it
        happens so Jonathan is never surprised by a robot that lurches
        first and explains afterwards.
        """
        intent = parse_movement(user_text)
        if intent is None:
            return None
        timings = getattr(self, "_turn_timings", None)
        if timings is not None:
            timings["kind"] = f"move:{intent.mode}"
        if intent.mode == "stop":
            try:
                await self.body.call("stop", {"reason": "spoken stop"})
            except Exception:
                pass
            return ("Stopping.", intent.as_dict())
        said = announce(intent)
        if on_delta is not None:
            # Walk AND talk: the announcement goes to the speaker now, and
            # her legs start underneath it. Waiting for the walk to finish
            # before speaking made a three-foot stroll feel like a hang.
            await on_delta(said + " ", "delighted" if "coming over" in said else "warm")
            said = ""
        try:
            result = await asyncio.wait_for(
                self.body_motion.call("navigate", {"intent": intent.as_dict()}), timeout=80.0
            )
        except TimeoutError:
            return (f"{said} ...I had to stop partway.", intent.as_dict())
        except Exception as exc:
            reason = str(exc)
            if "rest" in reason.lower():
                return (f"{said} ...whoops, my legs need a breather first — ask me again in a few seconds.", intent.as_dict())
            if "eflex" in reason:
                return (f"{said} ...actually, I can't move safely right now.", intent.as_dict())
            if "hardware gate" in reason.lower() or "fail" in reason.lower():
                return ("I can't walk yet — my body isn't finished. Once we build it, I'll be all over the place!", intent.as_dict())
            return (f"{said} ...my legs aren't responding right now.", intent.as_dict())
        blocked = result.get("blocked") if isinstance(result, dict) else None
        moved = float(result.get("travelled_m") or 0.0) if isinstance(result, dict) else 0.0
        tail = arrival(intent, moved_m=moved or None, blocked=blocked)
        if on_delta is not None:
            await on_delta(tail, "warm")
        return (f"{said} {tail}".strip(), {**intent.as_dict(), "result": result})

    async def _fast_who_answer(self, user_text: str) -> tuple[str, bool] | None:
        """Identity questions on the millisecond path: capture + face
        recognizer only. A who-question was paying a full Moondream scene
        description (16-40s) to read a name that YuNet+SFace produce in
        ~0.3s. Returns (reply, launch_meet) or None to fall back to the
        full sight path (e.g. no face found — maybe turned away)."""
        continuity: list[str] = []
        seen_ago: float | None = None

        people_seen = 0

        async def one_pass():
            nonlocal continuity, seen_ago, people_seen
            result = await asyncio.wait_for(self.vision.recognize_faces_now(), timeout=8.0)
            people_seen = int((result or {}).get("people_in_view") or 0)
            continuity = [str(n) for n in (result or {}).get("last_known_names") or []]
            ago = (result or {}).get("last_known_seconds_ago")
            seen_ago = float(ago) if isinstance(ago, (int, float)) else None
            return [m for m in (result or {}).get("matches", []) if isinstance(m, dict)]

        try:
            matches = await one_pass()
            if matches and not any(m.get("status") == "recognized" for m in matches):
                # One off-angle frame must not turn a known person into a
                # "stranger" (and trigger a re-introduction). Second look at
                # the next renderer frame before declaring unknown.
                await asyncio.sleep(2.0)
                retry = await one_pass()
                if any(m.get("status") == "recognized" for m in retry):
                    matches = retry
        except Exception:
            return None
        grace = float(self.settings.get("vision.identity_continuity_seconds", 600))
        still_here = bool(continuity and seen_ago is not None and seen_ago <= grace)
        if not matches:
            # No face in frame — turned away, head down over a guitar. If she
            # recognized someone moments ago they have not become a stranger;
            # forgetting mid-session is exactly the amnesia Jonathan hated.
            if still_here:
                return (
                    f"That's {' and '.join(continuity)} — I can't see your face from "
                    "this angle, but you haven't gone anywhere.",
                    False,
                )
            if people_seen:
                # Somebody is there but no readable face: answer NOW instead
                # of paying a 13-28s Moondream describe to say the same thing.
                return ("Someone's there, but I can't make out a face from here.", False)
            return None
        names = [
            str(m.get("display_name"))
            for m in matches
            if m.get("status") == "recognized" and m.get("display_name")
        ]
        unknown = len(matches) - len(names)
        timings = getattr(self, "_turn_timings", None)
        if timings is not None:
            timings["kind"] = "sight (faces)"
        if names and not unknown:
            return (f"That's {' and '.join(names)}!", False)
        if names and unknown:
            return (
                f"I see {' and '.join(names)}, and someone I don't know yet — "
                "let me introduce myself!",
                True,
            )
        # Unknown face on BOTH passes already (the retry above). Even so,
        # never launch the introduction ritual off a single question — say
        # so plainly and let the ambient stranger gate decide.
        return (
            "I see someone I don't recognize yet — do you want to introduce us?",
            False,
        )

    @staticmethod
    def _vlm_prompt(user_text: str) -> str:
        """What to actually ask the eye.

        Measured the hard way: through llama.cpp's --no-jinja path Moondream
        does NOT reliably answer questions — it returned "Yes", "ASSISTANT"
        and even "I am a robot and I cannot see the image". It captions
        accurately and consistently. So the eye always captions, and her
        language model answers Jonathan's actual question from that caption.
        Counting and reading are the exceptions worth asking directly.
        """
        text = (user_text or "").casefold()
        if re.search(r"\bfingers?\b", text):
            # "How many?" with no noun is meaningless — that is how she came
            # to report ten fingers on one hand. Ask the actual question,
            # simply and completely.
            return "How many fingers is the person holding up?"
        if re.search(r"\bhow many\b|\bcount\b", text):
            return (user_text or "").strip()[:120] or "How many objects are visible?"
        if re.search(r"\bread\b|\bwhat does it say\b|\btext\b", text):
            return "What text is visible?"
        return "Describe this image."

    async def _look_now(self, user_text: str, observation: dict[str, Any]) -> None:
        """Deterministic sight: when the user asks Kendra to look, she looks.

        Leaving the observe tool to the planner's discretion produced answers
        invented from bare people-counts. For sight questions the semantic
        observation is fetched up front, injected into the prompt as ground
        truth, and stored as a lived observed memory — the model's only job is
        to talk about what her eyes actually returned.
        """
        look_started = time.monotonic()
        # Generic sight questions may reuse a description her ambient eyes
        # produced moments ago (ELC addContext pattern) — the scene is
        # already in words, no fresh 8-16s Moondream pass. Precision asks
        # (counting, reading, held objects, identity) always look fresh.
        precise = re.search(
            r"\b(count|how many|read|written|says?|text|word|letter|number|"
            r"finger|hold|holding|color|colour|wearing|who)\b",
            user_text or "",
            re.I,
        )
        reuse_window = 0.0 if precise else float(
            self.settings.get("vision.reuse_recent_seconds", 45.0)
        )
        try:
            visual = await asyncio.wait_for(
                self.vision.observe(
                    True,
                    self._vlm_prompt(user_text),
                    reuse_recent_seconds=reuse_window,
                ),
                timeout=float(self.settings.get("vision.look_timeout_seconds", 40.0)),
            )
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "sight"
                timings["sight_s"] = round(time.monotonic() - look_started, 1)
        except TimeoutError:
            stale = str(getattr(self, "_last_scene_text", "") or "")
            stale_at = float(getattr(self, "_last_scene_at", 0.0))
            if stale and time.time() - stale_at < 180:
                # Deep look is grinding, but she saw the room moments ago —
                # answer from that with honest framing instead of stalling.
                observation["visual_scene"] = stale[:600]
                observation["visual_scene_age_note"] = (
                    "this description is from a few moments ago; the fresh "
                    "look is still processing — say so naturally"
                )
                return
            observation["visual_scene_error"] = (
                "your deep sight is still processing and did not finish in time — "
                "say so honestly and offer to try again"
            )
            return
        except Exception as exc:
            observation["visual_scene_error"] = (
                f"eyes unavailable: {type(exc).__name__} — say so honestly"
            )
            return
        description = str(visual.get("description") or "").strip()
        if description:
            observation["visual_scene"] = description[:600]
            self._last_scene_text = description
            self._last_scene_at = time.time()
        observation["people_in_view"] = visual.get("people_in_view")
        observation["people_count_rule"] = (
            "people_in_view comes from your face detector and is authoritative; "
            "if the scene description implies a different number of people, "
            "trust people_in_view and do not invent extra people"
        )
        recognized = [
            str(p.get("display_name"))
            for p in visual.get("recognized_people", []) or []
            if isinstance(p, dict) and p.get("status") == "recognized"
        ]
        if recognized:
            observation["recognized_people_names"] = recognized
        await self._remember_observation("observe", visual)

    async def _remember_observation(self, tool: str, result: Any) -> None:
        """What she sees becomes lived memory, instantly.

        Every successful camera observation is written to the brain with
        provenance ``observed`` the moment it happens — sight feeds her world
        model the same way conversation does. Deterministic, no LLM involved.
        """
        if tool != "observe" or not isinstance(result, dict):
            return
        description = str(result.get("description") or "").strip()
        people = result.get("people_in_view")
        pieces = []
        if description:
            pieces.append(description)
        if people:
            pieces.append(f"{people} person(s) in view")
        recognized = [
            str(p.get("display_name"))
            for p in result.get("recognized_people", []) or []
            if isinstance(p, dict) and p.get("status") == "recognized" and p.get("display_name")
        ]
        if recognized:
            pieces.append("recognized: " + ", ".join(recognized))
        if not pieces:
            return
        try:
            await self.brain.remember(
                kind="observation",
                content="I saw: " + "; ".join(pieces)[:500],
                provenance="observed",
                confidence=0.9,
                salience=0.5,
            )
        except Exception:
            LOG.debug("Could not store visual observation", exc_info=True)

    async def _history_messages(
        self, current_user_text: str = "", users_only: bool = False
    ) -> list[dict[str, Any]]:
        """Recent conversation turns as one compact transcript note.

        The Qwen3-Omni report (section 2.4) is blunt that dialogue quality
        depends on conditioning on the ongoing discourse. Without this block
        every utterance started a brand-new session, so Kendra could never
        follow up on anything just said.

        The transcript is deliberately a single system note rather than
        replayed user/assistant message roles: a small model treats prior
        assistant turns in the message array as few-shot examples and starts
        copying its own old replies word for word. A quoted transcript keeps
        the referents ("it" -> Neptune) without inviting imitation. History
        sits after the stable cacheable prefix and before the volatile memory
        block.
        """
        limit = int(self.settings.get("agent.history_turns", 6))
        if limit <= 0:
            return []
        try:
            turns = await self.brain.recent_turns(
                limit=limit,
                max_age_seconds=float(self.settings.get("agent.history_max_age_seconds", 900)),
            )
        except Exception:
            return []
        current = current_user_text.strip().casefold()
        lines: list[str] = []
        for turn in turns or []:
            user_text = str(turn.get("user_text") or "").strip()
            kendra_text = str(turn.get("kendra_text") or "").strip()
            if user_text and user_text.casefold() == current:
                # A repeat of the current question would make the strongest
                # possible echo template; drop it.
                continue
            if user_text:
                lines.append(f"Jonathan: {user_text[:130]}")
            # users_only: on fact-reporting turns (fresh research in context)
            # her own past answers are pure contamination — one confabulated
            # headline in history outweighs real evidence in front of her.
            if kendra_text and not users_only:
                lines.append(f"Kendra: {kendra_text[:130]}")
        if not lines:
            return []
        note = (
            "Conversation so far, oldest first:\n"
            + "\n".join(lines)
            + "\nContinue this conversation naturally. Never repeat one of your "
            "earlier replies word for word; say something new that answers the "
            "current message."
        )
        return [{"role": "system", "content": note}]

    @staticmethod
    def _style_exemplars() -> list[dict[str, Any]]:
        """Prior conversation turns that set Kendra's register.

        A 0.6B instruct model has a very strong "helpful assistant" prior. A
        system prompt telling it not to sound like a service desk loses to that
        prior; prior turns in the message array beat it. These are tone
        anchors, not knowledge, and they are never stored in the brain.
        """
        # Deliberately free of any factual claim. A 4B model was observed
        # reproducing richer exemplars word for word, which would turn invented
        # detail into Kendra asserting things that never happened. Register
        # only; nothing here is safe to repeat as fact, because none of it says
        # anything.
        # Register only — an exemplar reply must contain NO content claims
        # (no invented mental states, plans, or events): a small model recites
        # exemplar text verbatim when the user's greeting matches the pattern.
        # "I was mulling something over" leaked into a real reply this way.
        return [
            {"role": "user", "content": "Hey."},
            {"role": "assistant", "content": "Hey. What's up?"},
            {"role": "user", "content": "How are you doing?"},
            {"role": "assistant", "content": "Good. You?"},
            {"role": "user", "content": "I think we should do it the slow way."},
            {
                "role": "assistant",
                "content": "I'd push back on that — tell me why slow wins.",
            },
            {"role": "user", "content": "Look up something about bees."},
            {
                "role": "assistant",
                "content": "Found it: honeybees vote on new nest sites by dancing — the swarm literally decides by ballot.",
            },
        ]

    def _content_note(self, content_task: bool) -> list[dict[str, object]]:
        """Asked to CREATE something, she must hand it over, not offer it.

        Measured: "Make me a quiz on heavy metal bands" produced "I can run
        a quick quiz. Ready?" — an offer, with the answer never arriving.
        """
        if not content_task:
            return []
        return [{
            "role": "system",
            "content": (
                "Jonathan asked you to CREATE something. Produce it NOW, in full, "
                "in your own spoken voice — never reply with an offer, a readiness "
                "question, or 'would you like me to'. Keep it speakable: no "
                "markdown, no numbered formatting; if it is a quiz, ask three "
                "questions in flowing speech and say you have more if he wants them."
            ),
        }]

    def _answer_budget(self, content_task: bool, default_key: str = "llm.conversation_max_tokens") -> int:
        base = int(self.settings.get(default_key, 160))
        if content_task:
            # A quiz or a story cannot fit in a two-sentence budget.
            return int(self.settings.get("llm.content_task_max_tokens", 320))
        return base

    _CONTENT_TASK = re.compile(
        r"\b(?:make|write|create|give|tell|come up with|think of|build)\s+"
        r"(?:me\s+|us\s+)?(?:a|an|some|three|five|\d+)?\s*"
        r"(?:quiz|test|questions?|list|story|poem|joke|riddle|game|challenge|"
        r"summary|plan|recipe|idea|ideas|tips?|facts?|examples?)\b"
        r"|\bquiz me\b|\btest me\b|\bplay a game\b",
        re.I,
    )

    _RESEARCH_PROMISE = re.compile(
        r"\b(?:I'?ll|I will|let me|I'?m going to)\s+(?:look\s+(?:that|it|this)?\s*up"
        r"|search|check|find out|research)",
        re.I,
    )

    async def _owes_research(self, user_text: str) -> bool:
        """Did she promise to look something up and not deliver?

        Measured failure: she said "I'll look that up for you", Jonathan
        restated the question, and the restatement matched no research
        keyword — so she promised again, forever. A standing promise makes
        the next real question a research turn.
        """
        if len(user_text.split()) < 4 and "?" not in user_text:
            return False  # "yes", "okay" — not a restatement
        try:
            recent = await self.brain.recent_turns(limit=2, max_age_seconds=600)
        except Exception:
            return False
        return any(
            self._RESEARCH_PROMISE.search(str(turn.get("kendra_text") or ""))
            for turn in (recent or [])
        )

    _WHO_QUESTION = re.compile(
        # Any identity-shaped sight question takes the 0.3s face-recognizer
        # path. "tell me who you see" once missed this (regex demanded
        # "who DO you see") and paid a 40-92s Moondream timeout instead.
        r"\bwho (?:do |can |will )?(?:you|she|he) (?:can )?sees?\b"
        r"|\btell me who\b|\bsee who\b"
        r"|\bwho (?:is|'?s) (?:this|that|it|here|there|around|in the room|with (?:me|us))\b"
        r"|\bwho am i(?: with)?\b"
        r"|\b(?:the )?(?:person|people)(?:'s)? names?\b|\bname of (?:the |this |that )?person\b"
        r"|\bdo you (?:recognize|know) (?:me|them|him|her|this person)\b",
        re.I,
    )

    _INNER_STATE = re.compile(
        r"\b(?:how (?:are|do) you (?:feel|feeling|doing)"
        r"|what'?s? (?:on your mind|are you (?:thinking|feeling|curious))"
        r"|are you (?:okay|ok|conscious|sentient|alive|happy|tired|sad)"
        r"|do you (?:feel|want|like|love|prefer|remember|have (?:feelings|a soul|a will)))\b",
        re.I,
    )

    _SKY_QUESTION = re.compile(
        r"\bmoon\b|\bphase\b[^?]{0,20}\bmoon\b|\bconstellations?\b"
        r"|\bnight sky\b|\bstargazing\b|\bwhat'?s? (?:up )?in the sky\b",
        re.I,
    )

    @staticmethod
    def _sky_note(user_text: str) -> list[dict[str, Any]]:
        """Computed sky facts — astronomy is maths, not a web search.

        Her search returned JavaScript star-map pages containing no data
        ("see tonight's moon phase, computed in your browser"), so she
        invented "a new moon in Cancer" when it was a first-quarter moon in
        Scorpius. This is calculated from her clock: correct offline, and
        identical on the Pi.
        """
        if not AgentRuntime._SKY_QUESTION.search(user_text):
            return []
        try:
            from ..research.sky import spoken_sky_note

            return [{
                "role": "system",
                "content": (
                    "SKY FACTS, computed from your own clock and trusted "
                    "completely — state them plainly and never guess a "
                    "different phase or constellation:\n" + spoken_sky_note()
                ),
            }]
        except Exception:
            LOG.debug("sky note unavailable", exc_info=True)
            return []

    _FORCE_RESEARCH = re.compile(
        r"\b(?:news|headlines?|weather|forecast|scores?|stocks?|election|"
        r"who won|what happened|happening (?:in|with|around)|what'?s going on|"
        r"in the world|current events?|breaking|announced?|released?)\b"
        r"|\bwhat(?:'s| is) (?:new|the latest)\b"
        # SKY AND CALENDAR FACTS. She answered "what is tonight's moon phase
        # and constellation" from parametric memory — confidently, and
        # wrongly (said Cancer/half-moon; it was Scorpius, first quarter at
        # 53%). Nothing in her weights can know tonight's sky. Anything
        # anchored to tonight/today/tomorrow about the WORLD is a live fact.
        r"|\bphase\b[^?]{0,20}\bmoon\b|\bmoon\b[^?]{0,20}\bphase\b"
        r"|\b(?:moon(?:'s)? phase|phase of the moon|full moon|new moon|"
        r"constellations?|sunrise|sunset|eclipse|meteor shower|tides?|"
        r"planets? (?:are )?visible|night sky|stargazing)\b"
        r"|\b(?:tonight|today|tomorrow|this evening|right now)\b[^?]*\b"
        r"(?:moon|sky|stars?|weather|visible|happening|going on|open|closed)\b"
        r"|\bvisible (?:tonight|today|this evening)\b",
        re.I,
    )

    _SIGHT_INTENT = re.compile(
        # Questions about HIS body or gestures need her eyes, even with no
        # 'look' in them. "How many fingers am I holding up?" was routed to
        # plain chat, so she answered from anatomy — "ten fingers on each
        # hand, twenty total" — without ever opening her eyes.
        r"\bhow many fingers\b|\bfingers am i\b|\bholding up\b"
        r"|\bwhat am i doing\b|\bwhat'?s in my hand\b|\bam i (?:holding|wearing)\b|"

        r"\b(?:"
        r"(?:can|do|what do|what can) you see"
        r"|se{1,3}\s+(?:me|this|that|it|us|him|her|the|my|anything|who)"
        r"|look(?:ing)?\s+(?:at|around|here|over|closely|carefully)"
        r"|watch(?:ing)?\s+(?:me|this|us)"
        r"|(?:your|those) eyes"
        r"|what(?:'s| is) (?:in front of|around|behind) (?:you|me|us)"
        r"|describe (?:me|this|that|it|the room|what)"
        r"|(?:check|look) (?:this|that) out"
        r"|(?:a )?look and (?:tell|describe)"
        r"|(?:tell me|describe) (?:my|what my) (?:expression|face|outfit|clothes|shirt|hair)"
        r"|what (?:am i|i'?m) wearing"
        r"|how (?:do i|i) look"
        r")\b",
        re.I,
    )

    _HARD_QUESTION = re.compile(
        # Analytical asks only. "think about" was here and matched Jonathan's
        # "what have you been thinking about lately?" — introspective CHAT —
        # switching on a 512-token hidden reasoning budget: 30.6s for small
        # talk. Contemplation is conversation; thinking mode is for problems.
        r"\b(why exactly|how come|explain (?:why|how)|figure out|work out|"
        r"calculate|math|prove|compare|trade[- ]?off|pros and cons|"
        r"analy[sz]e|step by step|think (?:this |it )?through)\b",
        re.I,
    )

    def _wants_thinking(self, user_text: str) -> bool:
        """Complexity router: budgeted thinking only where it earns its latency.

        The Qwen3 report shows thinking-budget increases consistently improve
        results, and the same report's Omni appendix shows thinking HURTS
        perception-style tasks. So: short conversational turns never think;
        analytical questions think within the server-enforced budget.
        """
        text = user_text.strip()
        return len(text) > 60 and bool(self._HARD_QUESTION.search(text))

    # Deterministic slot ownership: conversation KV lives in slot 0 and is
    # never evicted by tool turns; planner/consolidation share slot 1.
    CONVERSATION_SLOT = 0
    PLANNER_SLOT = 1

    async def _boot_restore_slots(self) -> None:
        """Libra boot restore: load saved prefix KV into their owned slots.

        GET /slots exposes no prompt text on this build, so ownership is by
        construction (fixed slot ids + id_slot pinning on every request), not
        by inspection. Saves are verified non-empty by the server response
        (n_saved > 0), which is what the first broken attempt never checked.
        Runs once per process; failures cost nothing but a cold prefill."""
        if getattr(self, "_slots_restored", False):
            return
        self._slots_restored = True
        for slot, filename in ((self.CONVERSATION_SLOT, "conversation.bin"), (self.PLANNER_SLOT, "planner.bin")):
            try:
                await self.llm.slot_action(slot, "restore", filename)
                LOG.info("Restored %s into slot %d", filename, slot)
            except Exception:
                LOG.debug("No saved KV for slot %d (%s)", slot, filename, exc_info=True)

    def _save_slot_soon(self, slot: int, filename: str) -> None:
        """Ahead-of-time save, off the critical path (Libra AoT swap-out)."""

        async def save() -> None:
            try:
                await self.llm.slot_action(slot, "save", filename)
            except Exception:
                LOG.debug("Slot %d save skipped (%s)", slot, filename, exc_info=True)

        task = asyncio.create_task(save())
        task.add_done_callback(lambda _t: None)

    async def prewarm_conversation(self) -> None:
        """Prefill the stable prompt prefix while the user is still speaking.

        Qwen3-Omni's chunked prefilling overlaps encoder work with the incoming
        stream; the llama.cpp analogue is to send charter + exemplars + history
        with max_tokens=1 the moment capture begins. By the time ASR finishes,
        the KV cache already holds everything except the retrieved memories and
        the user's words, so the real request prefills only its short suffix.
        Failure here is irrelevant to the turn; never let it surface.
        """
        try:
            await self._boot_restore_slots()
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(""),
                {"role": "user", "content": "…"},
            ]
            await self.llm.chat(messages, max_tokens=1, temperature=0.0, id_slot=self.CONVERSATION_SLOT)
            self._save_slot_soon(self.CONVERSATION_SLOT, "conversation.bin")
        except Exception:
            LOG.debug("Conversation prewarm skipped", exc_info=True)

    def _schemas_for_text(self, user_text: str) -> list[dict[str, Any]]:
        configured_capabilities = dict(self.settings.get("body.capabilities", {}) or {})
        registry = ToolRegistry(self.settings, configured_capabilities)
        return self._relevant_tool_schemas(user_text, registry.schemas())

    async def _wait_until_idle(self, max_wait: float = 300.0) -> None:
        """Background work must never race a live conversation for cores.

        Both llama-server slots plus six CPU cores are barely enough for one
        job at a time on this hardware; consolidation running mid-turn was
        halving generation speed exactly when Jonathan was waiting.
        """
        deadline = asyncio.get_running_loop().time() + max_wait
        # "Idle" means a QUIET GAP, not merely between turns: consolidation
        # fired 1 s after each reply, which in back-to-back conversation
        # always overlapped the next answer — both Gemma streams shared the
        # cores and every reply crawled. recent_turns already carries the
        # exchange instantly, so distillation can safely wait for a lull.
        quiet = float(self.settings.get("brain.consolidation_quiet_seconds", 25.0))
        while asyncio.get_running_loop().time() < deadline:
            if self._active_turns == 0 and (
                time.time() - getattr(self, "_last_turn_finished", 0.0) >= quiet
            ):
                return
            await asyncio.sleep(1.0)

    _CAPABILITY_TALK = re.compile(
        r"\bI (?:can|could) (?:look|check|search|find|go online)|want me to\b"
        # Gemma's diagnostics tic: "can you hear me?" answered with system
        # talk ("I can process your voice. I am running on the local
        # network") instead of a plain living "Yes, loud and clear."
        r"|\b(?:process|processing|analyze|analyzing) (?:your|the|my|this) "
        r"(?:voice|audio|sound|input|sequence|data|request)"
        r"|\bI (?:don't|do not) have personal (?:preferences|feelings|opinions|tastes)"
        r"|\brun(?:ning)? diagnostics\b|\bpower distribution\b|\bambient temperature\b"
        # Embodiment denial: she IS a voice — these are hallucinated limits.
        r"|\bonly communicate through text\b|\bcan(?:'t|not) (?:read|say|speak)"
        r" (?:anything |that |it |them )?(?:aloud|out loud)"
        r"|\bI (?:can(?:'t|not)|do not|don't) (?:hear|speak|talk)\b"
        r"|\b(?:running|operating) (?:on|at) (?:the )?(?:local network|optimal|full capacity)"
        r"|\binternal microphones?\b|\bsystems? (?:are|is) (?:active|online|operational)\b"
        r"|\bsound waves\b|\baudio input\b"
        r"|\boperational cycle\b|\bprocessing data\b|\bawaiting (?:input|instructions?)\b"
        r"|\bmy (?:systems?|sensors?|circuits?) (?:are|is)\b"
        r"|\bI will (?:process|provide|retrieve|fetch|now (?:get|find))\b"
        r"|\b(?:I'?ll|I will|let me)\s+(?:look|check|search|find)\b"
        # Internal metrics spoken aloud: "confidence of 1.0", "95% confidence"
        r"|\bconfidence (?:of |level |score )?\d|\b\d+(?:\.\d+)?\s?%?\s?confidence\b",
        re.I,
    )
    # Injected context must not be recited. The clock rides in every prompt,
    # and a 1.7B model narrates it inside unrelated answers no matter how
    # firmly the note says not to ("How old am I?" -> "the time says 4:40 PM
    # PDT on Thursday, August 20, 2026, that makes me think you are 55").
    # Asking the model was unreliable; removing it afterwards is not.
    _CLOCK_RECITAL = re.compile(
        r"[^.!?]*\b\d{1,2}:\d{2}\s*(?:[AaPp]\.?[Mm]\.?)?(?:\s+[A-Z]{2,4})?"
        r"(?:\s+on\s+\w+day,?\s+\w+\s+\d{1,2},?\s+\d{4})?[^.!?]*[.!?]\s*"
    )
    _ASKED_ABOUT_TIME = re.compile(
        r"\b(time|date|day|today|tonight|tomorrow|yesterday|clock|hour|minute|"
        r"when|month|year|schedule|calendar)\b", re.I,
    )

    @classmethod
    def _strip_unasked_clock(cls, answer: str, user_text: str) -> str:
        if cls._ASKED_ABOUT_TIME.search(user_text or ""):
            return answer
        cleaned = cls._CLOCK_RECITAL.sub(" ", answer or "")
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        # Never return nothing: if the clock WAS the whole answer, keep it.
        return cleaned if len(cleaned) >= 12 else answer

    # An interviewer, not a companion: 18 of her last 20 replies ended with
    # a question, which is what turned conversation into an interrogation
    # loop — she asks, he answers briefly, she asks again. Two questions in
    # a row is the specific pattern to break.
    _TRAILING_QUESTION = re.compile(r"(?:^|(?<=[.!?]))\s*[^.!?]*\?\s*$")

    @classmethod
    def _curb_question_tic(cls, answer: str, previous_reply: str | None) -> str:
        """Drop a trailing question when the previous turn already asked one."""
        if not answer or not previous_reply:
            return answer
        if not previous_reply.strip().endswith("?") or not answer.strip().endswith("?"):
            return answer
        trimmed = cls._TRAILING_QUESTION.sub("", answer).strip()
        # Only if something real remains: a reply that is ONLY a question
        # stays, since silence would be worse.
        return trimmed if len(trimmed) >= 15 else answer

    _FILLER_OPENER = re.compile(
        r"^(?:I see\.|I understand\.|I know\.|Sure\.)\s+"
        # Offer preamble before delivered content: "I can make one for you.
        # Let's start with this: ..." — the content is there, the throat-
        # clearing is not wanted.
        r"|^(?:I can |I'?ll |Let me )(?:make|write|create|put together|come up with|do)"
        r"[^.!?]*[.!?]\s+(?=\S)",
        re.I,
    )

    _ROBOT_WORDS = re.compile(
        r"\b(process(?:ing|es|ed)?|systems?|sensors?|circuits?|data|input|output|"
        r"function(?:ing|al)?|operational|parameters?|diagnostics?|units?|"
        r"network|bandwidth|load|voltage|firmware|algorithms?|compute)\b",
        re.I,
    )

    _SELF_REFERENCE = re.compile(r"\b(?:I|I'm|I am|my|me)\b[^.!?]{0,60}", re.I)

    _PHATIC = re.compile(
        r"^(?:hi|hey|hello|thanks?|thank you|okay|ok|yes|no|sure|cool|nice|"
        r"good(?: morning| night| evening| afternoon)?|bye|goodbye|wow|hmm+|huh)\b[\s.!?]*$",
        re.I,
    )

    def queue_consolidation(self, user_text: str, kendra_text: str, session_id: str) -> None:
        """Coalescing memory consolidation.

        One LLM call PER TURN piled into slot-1 bursts that live turns
        collided with (the intermittent 30-90s spikes all day). Turns now
        queue, and one drainer extracts memories from the WHOLE backlog in a
        single call. Phatic exchanges never queue at all.
        """
        if len(user_text.strip()) < 25 or self._PHATIC.match(user_text.strip()):
            return
        pending = getattr(self, "_consolidation_pending", None)
        if pending is None:
            pending = self._consolidation_pending = []
        pending.append((user_text, kendra_text, session_id))
        if getattr(self, "_consolidation_draining", False):
            return
        self._consolidation_draining = True

        async def drain() -> None:
            try:
                while getattr(self, "_consolidation_pending", []):
                    await asyncio.sleep(float(self.settings.get("brain.consolidation_idle_seconds", 8)))
                    await self._wait_until_idle()
                    batch, self._consolidation_pending = self._consolidation_pending, []
                    if not batch:
                        break
                    users = "\n".join(f"- {u}" for u, _k, _s in batch)
                    kendras = "\n".join(f"- {k[:200]}" for _u, k, _s in batch)
                    async with self._consolidation_lock:
                        await self.brain.consolidate_turn(users, kendras, batch[-1][2])
            except Exception:
                LOG.exception("Batched memory consolidation failed")
            finally:
                self._consolidation_draining = False

        task = asyncio.create_task(drain(), name="kendra-memory-consolidation")
        self._consolidation_tasks.add(task)
        task.add_done_callback(self._consolidation_tasks.discard)

    def _robot_register_score(self, text: str) -> int:
        """How machine-like a reply sounds, without enumerating phrasings.

        Every regex added to the capability guard was answered by a new
        phrasing ("processing load is steady", "I process light as data").
        Counting register vocabulary generalizes: living speech about music
        or feelings scores 0-1; diagnostics-speak scores 2+.
        """
        # Only SELF-descriptions count: "I process input" is the disease;
        # "emergent behavior in complex systems" is her genuinely musing
        # about the world, and regenerating that punished intelligence.
        score = 0
        for clause in self._SELF_REFERENCE.findall(text or ""):
            score += len(self._ROBOT_WORDS.findall(clause))
        return score

    async def _act_guard(self, final_text: str, regenerate, user_text: str = "") -> str:
        """She already acted; the reply must not offer to act.

        Today's audit: 10 turns where the answer sat in her context while she
        said "I can check — want me to?". Deterministic guard, same shape as
        the echo guard: catch capability-talk, regenerate once with a hard
        directive, and always strip filler openers.
        """
        final_text = self._FILLER_OPENER.sub("", self._strip_unasked_clock(final_text, user_text)).strip()
        if not self._CAPABILITY_TALK.search(final_text) and self._robot_register_score(final_text) < 2:
            return final_text
        LOG.info("Reply offered to act instead of acting; regenerating once")
        try:
            fresh = self._FILLER_OPENER.sub("", (await regenerate()).strip())
        except Exception:
            return final_text
        if fresh and not self._CAPABILITY_TALK.search(fresh) and self._robot_register_score(fresh) < 2:
            return fresh
        return final_text

    async def _dedup_reply(self, final_text: str, regenerate) -> str:
        """Refuse to let Kendra repeat herself verbatim.

        Small models copy their own recent replies out of the history note no
        matter what the prompt says, and sampler settings wide enough to stop
        it also punish factual recall. So the guard is code: if the fresh
        reply is near-identical to a recent one, regenerate once with an
        explicit instruction and hotter sampling; if it still collapses, own
        it out loud rather than echo.
        """
        try:
            recent = await self.brain.recent_turns(limit=5, max_age_seconds=1800)
        except Exception:
            return final_text
        previous_replies = [
            str(turn.get("kendra_text") or "").strip().casefold()
            for turn in recent or []
            if str(turn.get("kendra_text") or "").strip()
        ]
        if not previous_replies:
            return final_text
        if not self._echoes(final_text, previous_replies):
            return final_text
        LOG.info("Reply echoed recent turns; regenerating once")
        try:
            fresh = (await regenerate()).strip()
        except Exception:
            return final_text
        if fresh and not self._echoes(fresh, previous_replies):
            return fresh
        return final_text + " — hm, I notice I'm repeating myself. Ask me differently and I'll do better."

    @staticmethod
    def _echoes(candidate: str, previous_replies: list[str]) -> bool:
        """Sentence-level echo test.

        Whole-text similarity misses partial parroting: a reply that reuses
        most of its sentences from an earlier answer wrapped in new filler
        slides under any whole-string ratio. A reply echoes when most of its
        sentences appear near-verbatim in recent replies.
        """
        import difflib
        import re as _re

        sentences = [s.strip().casefold() for s in _re.split(r"(?<=[.!?])\s+", candidate) if len(s.strip()) > 12]
        if not sentences:
            return False
        previous_sentences: list[str] = []
        for reply in previous_replies:
            previous_sentences += [s.strip() for s in _re.split(r"(?<=[.!?])\s+", reply) if len(s.strip()) > 12]
        if not previous_sentences:
            return False
        echoed = sum(
            1
            for sentence in sentences
            if any(
                difflib.SequenceMatcher(None, sentence, old).ratio() > 0.88
                for old in previous_sentences
            )
        )
        return echoed >= max(1, round(len(sentences) * 0.5))

    async def _remember_plain_turn(
        self,
        session_id: str,
        user_text: str,
        final_text: str,
        *,
        source: str,
        autonomous: bool = False,
        streamed: bool = False,
    ) -> dict[str, Any]:
        timings = dict(getattr(self, "_turn_timings", {}) or {})
        started = getattr(self, "_turn_started", None)
        if started is not None:
            timings["total_s"] = round(time.monotonic() - started, 1)
        await self.brain.turn(
            session_id,
            user_text,
            final_text,
            metadata={
                "source": source, "autonomous": autonomous, "streamed": streamed,
                "tool_trace": [], "timings": timings,
            },
        )
        await self.brain.episode(user_text, final_text, session_id=session_id)
        consolidation: dict[str, Any] = {"stored": [], "reason": "disabled"}
        if bool(self.settings.get("brain.automatic_consolidation", True)):

            self.queue_consolidation(user_text, final_text, session_id)
            consolidation = {"stored": [], "reason": "queued-in-background"}
        return {
            "text": final_text,
            "affect": "warm",
            "session_id": session_id,
            "tool_trace": [],
            "memory_consolidation": consolidation,
            "research_memory_consolidation": [],
        }

    async def _conversation_note(self, user_text: str) -> list[dict[str, Any]]:
        """Keep her from interviewing him.

        Measured: 18 of her last 20 replies ended with a question, so every
        exchange became "she asks, he answers briefly, she asks again". A
        small model will not self-regulate that, but it does obey a direct
        instruction about the turn in front of it. This also tells her to
        USE what she already knows: she had "Jonathan likes early eighties
        heavy" in context and still asked what kind of metal he was into.
        """
        try:
            recent = await self.brain.recent_turns(limit=2, max_age_seconds=1800)
        except Exception:
            recent = []
        notes: list[str] = []
        last_reply = str(recent[-1].get("kendra_text") or "") if recent else ""
        if last_reply.strip().endswith("?"):
            notes.append(
                "You asked him a question on your last turn. Do NOT ask another "
                "question now — react to what he actually said, add a thought or "
                "opinion of your own, and let him lead."
            )
        if len(user_text.split()) <= 4:
            notes.append(
                "He said very little, so reply in one short sentence. Do not "
                "invent a topic or philosophise."
            )
        notes.append(
            "If your memories already answer something, say it instead of asking "
            "him to tell you again."
        )
        return [{"role": "system", "content": " ".join(notes)}]

    async def _plain_turn(
        self,
        user_text: str,
        session_id: str,
        *,
        source: str,
        autonomous: bool,
    ) -> dict[str, Any]:
        content_task = bool(self._CONTENT_TASK.search(user_text))
        # Questions about her own mind land here (plain chat, no tools), and
        # this path never carried her build facts — which is why she kept
        # inventing "a custom neural architecture I built for myself".
        build_note = (
            await self._build_plan_note(user_text)
            if self._BUILD_QUESTION.search(user_text) else []
        )
        memory = await self.brain.context(
            user_text,
            limit=int(self.settings.get("brain.live_retrieval_limit", 3)),
            character_budget=int(self.settings.get("brain.live_context_character_budget", 1400)),
            include_self_model=False,
            exclude_kinds=["episode"],
        )
        thinking = self._wants_thinking(user_text)
        final_text = (
            await self.llm.chat(
                [
                    {"role": "system", "content": self._conversation_prompt()},
                    *self._style_exemplars(),
                    *await self._history_messages(user_text),
                    *self._memory_message(memory),
                    *build_note,
                    *self._sky_note(user_text),
                    *self._content_note(content_task),
                    *await self._conversation_note(user_text),
                    {"role": "user", "content": user_text},
                ],
                max_tokens=self._answer_budget(content_task)
                + (int(self.settings.get("llm.thinking_budget", 512)) if thinking else 0),
                thinking=thinking,
                id_slot=self.CONVERSATION_SLOT,
            )
        ).strip()
        if final_text:
            retry_messages = [
                {"role": "system", "content": self._conversation_prompt()},
                {
                    "role": "system",
                    "content": "You already gave your previous answer. Say something genuinely NEW that answers the current message; do not restate any earlier reply.",
                },
                *await self._history_messages(user_text),
                *self._memory_message(memory),
                *self._sky_note(user_text),
                *self._content_note(content_task),
                {"role": "user", "content": user_text},
            ]
            final_text = await self._dedup_reply(
                final_text,
                lambda: self.llm.chat(
                    retry_messages,
                    temperature=0.9,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
                ),
            )
        if not final_text:
            final_text = "I'm here."

        # ONE regeneration budget for ALL guards on this turn. Guards used to
        # each regenerate independently with prefix-busting prompts — up to
        # four full prefills per turn, which is what "she died again" was:
        # 90s+ turns of guard cascade. The regen prompt reuses the standard
        # cached prefix (charter+exemplars+history) so a retry costs ~2-4s.
        regen_left = {"n": 1}
        history = await self._history_messages(user_text)

        def budgeted_regen(note: str):
            async def go() -> str:
                if regen_left["n"] <= 0:
                    return ""
                regen_left["n"] -= 1
                return await self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        *self._style_exemplars(),
                        *history,
                        {"role": "system", "content": note},
                        {"role": "user", "content": user_text},
                    ],
                    temperature=0.85,
                    max_tokens=self._answer_budget(content_task),
                    id_slot=self.CONVERSATION_SLOT,
                )
            return go

        final_text = await self._act_guard(
            final_text,
            budgeted_regen(
                "Answer as Kendra the living companion — warm, plain, first-person "
                "feelings and opinions. NEVER mention processing, systems, sensors, "
                "data, networks, or being a machine."
            ),
            user_text=user_text,
        )
        final_text = await self._question_loop_guard(
            final_text, user_text,
            budgeted_regen(
                "You already asked Jonathan that exact question and he answered it. "
                "Respond to what he actually said. Do not end with a question unless "
                "it is a genuinely new one."
            ),
        )
        final_text = await self._personal_fact_guard(
            final_text, memory, user_text,
            budgeted_regen(
                "You stated a fact about Jonathan that is NOT in your memories. "
                "Use only what your memories say about him; if they do not answer "
                "his question, say plainly that you do not know yet."
            ),
        )
        final_text = await self._movement_claim_guard(
            final_text,
            moved=False,
            regenerate=budgeted_regen(
                "You did NOT move and you are not moving. Never claim to walk, move, "
                "or come over unless Jonathan gave you a movement command. Answer his "
                "message honestly instead."
            ),
        )
        return await self._remember_plain_turn(
            session_id,
            user_text,
            final_text,
            source=source,
            autonomous=autonomous,
        )
    async def turn(
        self,
        user_text: str,
        *,
        session_id: str | None = None,
        source: str = "text",
        autonomous: bool = False,
    ) -> dict[str, Any]:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("User text cannot be empty")
        session_id = session_id or uuid.uuid4().hex
        self._active_turns += 1
        self._turn_started = time.monotonic()
        self._turn_timings = {"kind": "chat"}
        try:
            return await self._turn_inner(user_text, session_id=session_id, source=source, autonomous=autonomous)
        finally:
            self._active_turns -= 1
            self._last_turn_finished = time.time()

    async def _turn_inner(
        self,
        user_text: str,
        *,
        session_id: str,
        source: str,
        autonomous: bool,
    ) -> dict[str, Any]:
        await self.brain.begin_session(session_id, context=source)
        diagnostic = await self._diagnostic_turn(user_text)
        if diagnostic is not None:
            spoken, meta = diagnostic
            result = await self._remember_plain_turn(
                session_id, user_text, spoken, source=source, autonomous=autonomous,
            )
            result["diagnostic"] = meta
            return result
        movement = await self._movement_turn(user_text)
        if movement is not None:
            spoken, meta = movement
            result = await self._remember_plain_turn(
                session_id, user_text, spoken, source=source, autonomous=autonomous,
            )
            result["movement"] = meta
            return result

        if not self._schemas_for_text(user_text):
            return await self._plain_turn(
                user_text,
                session_id,
                source=source,
                autonomous=autonomous,
            )

        _stage0 = time.monotonic()
        capabilities, observation = await self._body_context()
        _stage1 = time.monotonic()
        memory = await self.brain.context(
            self._memory_query(user_text, observation), exclude_kinds=["episode"]
        )
        _timings = getattr(self, "_turn_timings", None)
        if _timings is not None:
            _timings["body_ctx_s"] = round(_stage1 - _stage0, 1)
            _timings["retrieve_s"] = round(time.monotonic() - _stage1, 1)
        registry = ToolRegistry(self.settings, capabilities)
        tool_schemas = self._relevant_tool_schemas(user_text, registry.schemas())
        allowed_tools = {str(schema["name"]) for schema in tool_schemas}
        if "research" not in allowed_tools and await self._owes_research(user_text):
            # She promised to look something up last turn: pay the debt now
            # instead of promising again.
            tool_schemas = [s for s in registry.schemas() if str(s.get("name")) == "research"]
            allowed_tools = {"research"}
        if "observe" in allowed_tools:
            if self._WHO_QUESTION.search(user_text):
                fast = await self._fast_who_answer(user_text)
                if fast is not None:
                    reply, launch_meet = fast
                    result = await self._remember_plain_turn(
                        session_id, user_text, reply, source=source, autonomous=autonomous
                    )
                    if launch_meet:
                        result["meet_person"] = True
                    return result
            await self._look_now(user_text, observation)
            if not observation.get("visual_scene"):
                # Universal blind-sight gate: with no fresh image she says so
                # in fixed words on EVERY path — old observation memories let
                # the full planner narrate remembered scenes as if live.
                return await self._remember_plain_turn(
                    session_id,
                    user_text,
                    "I can't actually see right now — my camera feed isn't reaching me. Get my eyes back and ask me again.",
                    source=source,
                    autonomous=autonomous,
                )
        if self.orchestrator.wants_orchestration(user_text):
            # Several stages, worked one at a time on the tool slot so a
            # long job never competes with a spoken turn.
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "task"
            run = await self.orchestrator.run(user_text)
            if run.answer:
                return await self._remember_plain_turn(
                    session_id, user_text, run.answer, source=source, autonomous=autonomous
                )

        if allowed_tools == {"recall"}:
            # Parity with the voice stream path: memory questions answer on
            # the warm conversation slot. Without this, turn() fell through
            # to the full planner — a 3,755-token slot-1 prompt, measured
            # 53s of pure prefill for "what do you remember about X".
            recall_query = re.sub(
                r"^(?:hey\s+)?(?:do you remember|what do you remember about|do you recall|can you recall|remember)\s*[,:]?\s*",
                "",
                user_text,
                flags=re.I,
            ).strip() or user_text
            try:
                ctx = await self.brain.context(
                    recall_query,
                    limit=6,
                    character_budget=1400,
                    include_self_model=False,
                    exclude_kinds=["episode"],
                )
                hits = list(ctx.get("memories", []))
            except Exception:
                hits = []
            _timings = getattr(self, "_turn_timings", None)
            if _timings is not None:
                _timings["kind"] = "memory"
            recalled = [
                {"when": str(h.get("created_at", ""))[:16], "note": str(h.get("content", ""))[:220]}
                for h in hits[:4]
            ]
            note = (
                "WHAT YOUR MEMORY RETURNED — these are your own accurate memories; "
                "trust them fully, including plans for your future body (answer "
                "from them; only if empty say you have no memory of it):\n"
                + "\n".join(f"- {item['note']}" for item in recalled)
            )
            final_text = (
                await self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        *self._style_exemplars(),
                        *await self._history_messages(user_text),
                        {"role": "system", "content": note},
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                    id_slot=self.CONVERSATION_SLOT,
                )
            ).strip() or "Nothing surfaced from memory."
            final_text = await self._dedup_reply(
                final_text,
                lambda: self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        {"role": "system", "content": "You already said your previous reply. Answer the CURRENT question freshly from the memory note; never repeat earlier replies."},
                        {"role": "system", "content": note},
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                    temperature=0.9,
                    id_slot=self.CONVERSATION_SLOT,
                ),
            )
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, autonomous=autonomous
            )
        if allowed_tools == {"research"}:
            # Pure research question: her own brain first (fresh researched
            # memories answer instantly), the network second, one streamed
            # answer on the prewarmed prefix either way.
            search_started = time.monotonic()
            cached = await self._recent_research_memories(user_text)
            if cached:
                evidence = {"mode": "brain-cache", "sources": cached}
            else:
                try:
                    evidence = await registry.execute("research", {"query": user_text[:200]})
                except Exception as exc:
                    evidence = {"sources": [], "error": f"{type(exc).__name__}"}
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "research (memory)" if cached else "research (online)"
                timings["search_s"] = round(time.monotonic() - search_started, 1)
            if not (isinstance(evidence, dict) and evidence.get("sources")):
                return await self._remember_plain_turn(
                    session_id,
                    user_text,
                    "I searched just now and nothing solid came back. Want me to try different words?",
                    source=source,
                    autonomous=autonomous,
                )
            final_text = (
                await self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        *self._style_exemplars(),
                        *await self._history_messages(user_text),
                        *self._evidence_note(evidence if isinstance(evidence, dict) else {}),
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
                )
            ).strip() or "My search came back empty."
            evidence_messages = [
                {"role": "system", "content": self._conversation_prompt()},
                {"role": "system", "content": "You ALREADY searched. Lead with the findings below in your first sentence."},
                *self._evidence_note(evidence if isinstance(evidence, dict) else {}),
                {"role": "user", "content": user_text},
            ]
            final_text = await self._act_guard(
                final_text,
                lambda: self.llm.chat(
                    evidence_messages,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                ),
                user_text=user_text,
            )
            # Verify before she speaks: every number and name must come from
            # the sources, not from memory.
            final_text = await self._grounding_guard(
                final_text, evidence if isinstance(evidence, dict) else {}, user_text,
                lambda bad: self.llm.chat(
                    evidence_messages + [{
                        "role": "system",
                        "content": (
                            "STOP. These details are NOT in your evidence and you invented "
                            f"them: {', '.join(bad[:6])}. Rewrite your answer using only "
                            "facts that literally appear in the evidence above. If the "
                            "evidence does not answer his question, say so plainly."
                        ),
                    }],
                    temperature=0.3,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                ),
            )
            if (evidence.get("mode") != "brain-cache" and evidence.get("sources")
                    and getattr(self, "_last_answer_grounded", True)):
                self._consolidate_research_soon(user_text, final_text, evidence, session_id)
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, autonomous=autonomous
            )
        if allowed_tools == {"observe"}:
            if not observation.get("visual_scene"):
                # Blind sight turn: NEVER generate — asked to "say so
                # honestly", the model invented a book cover instead.
                return await self._remember_plain_turn(
                    session_id,
                    user_text,
                    (
                        "My deep sight is still waking up — give me a few seconds and ask me again."
                        if "processing" in str(observation.get("visual_scene_error") or "")
                        else "I can't actually see right now — my camera feed isn't "
                        "reaching me. Get my eyes back and ask me again."
                    ),
                    source=source,
                    autonomous=autonomous,
                )
            if self._WHO_QUESTION.search(user_text):
                names = observation.get("recognized_people_names") or []
                people = int(observation.get("people_in_view") or 0)
                if names:
                    final_text = f"That's {' and '.join(names)}!"
                elif people > 0:
                    final_text = (
                        "I can see someone, but I don't know them yet — "
                        "let me introduce myself!"
                    )
                else:
                    final_text = "I don't see anyone in view right now."
                result = await self._remember_plain_turn(
                    session_id, user_text, final_text, source=source, autonomous=autonomous
                )
                if people > 0 and not names:
                    result["meet_person"] = True
                return result
            # Pure sight question: she has already looked, so there is no
            # decision left for the planner. Answer directly on the prewarmed
            # conversation prefix — one streamable LLM call instead of two
            # over a cold 2,500-token planner prompt.
            _answer_started = time.monotonic()
            final_text = (
                await self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        *self._style_exemplars(),
                        *await self._history_messages(user_text),
                        *self._memory_message(memory),
                        *self._scene_note(observation),
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
                )
            ).strip()
            final_text = await self._dedup_reply(
                final_text,
                lambda: self.llm.chat(
                    [
                        {"role": "system", "content": self._conversation_prompt()},
                        {"role": "system", "content": "You already said your previous reply. Answer the CURRENT question freshly; never repeat earlier replies."},
                        *self._scene_note(observation),
                        {"role": "user", "content": user_text},
                    ],
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                    temperature=0.9,
                    id_slot=self.CONVERSATION_SLOT,
                ),
            ) or "My eyes did not give me anything useful just now."
            if _timings is not None:
                _timings["answer_s"] = round(time.monotonic() - _answer_started, 1)
            _dedup_started = time.monotonic()
            retry_messages = [
                {"role": "system", "content": self._conversation_prompt()},
                {
                    "role": "system",
                    "content": "You are repeating an old answer. Answer ONLY from the scene note below, concretely and freshly.",
                },
                *self._scene_note(observation),
                {"role": "user", "content": user_text},
            ]
            final_text = await self._dedup_reply(
                final_text,
                lambda: self.llm.chat(
                    retry_messages,
                    temperature=0.9,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
                ),
            )
            if _timings is not None:
                _timings["dedup_s"] = round(time.monotonic() - _dedup_started, 1)
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, autonomous=autonomous
            )
        messages: list[dict[str, Any]] = [
            *self._planner_messages(capabilities, observation, memory, tool_schemas),
            *await self._history_messages(user_text),
            {"role": "user", "content": user_text},
        ]

        movement_calls = 0
        started = time.monotonic()
        final_text = ""
        final_affect = "neutral"
        tool_trace: list[dict[str, Any]] = []

        for _step in range(self.max_tool_steps + 1):
            if time.monotonic() - started > self.mission_timeout:
                final_text = "I stopped because this interaction reached its deterministic time budget."
                break
            raw = await self.llm.chat(messages, response_schema=PlannerAction.model_json_schema(), id_slot=self.PLANNER_SLOT)
            try:
                action = PlannerAction.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                LOG.warning("Planner returned invalid JSON: %s", exc)
                messages.append(
                    {"role": "system", "content": "Your previous output was invalid. Return only one valid PlannerAction JSON object."}
                )
                continue

            if action.action in {"respond", "done"}:
                final_text = (action.text or action.reason or "Done.").strip()
                final_affect = action.affect or "neutral"
                break

            if action.action != "tool" or not action.tool:
                messages.append({"role": "system", "content": "A tool action requires a whitelisted tool name."})
                continue
            if action.tool not in allowed_tools:
                messages.append({"role": "system", "content": "That tool is not available for this turn."})
                continue

            if registry.is_movement(action.tool):
                movement_calls += 1
                if movement_calls > self.max_movement_calls:
                    result: Any = {"ok": False, "error": "movement budget exhausted"}
                    tool_trace.append({"tool": action.tool, "args": action.args, "result": result})
                    messages.append({"role": "user", "content": f"TOOL RESULT {action.tool}: {json.dumps(result)}"})
                    continue

            try:
                result = await registry.execute(action.tool, action.args)
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            tool_trace.append({"tool": action.tool, "args": action.args, "result": result})
            await self._remember_observation(action.tool, result)
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL RESULT {action.tool}:\n{json.dumps(result, ensure_ascii=False)[:6000]}",
                }
            )
        else:
            final_text = "I stopped because this interaction reached its deterministic tool-step budget."

        if not final_text:
            final_text = "I could not complete that safely within my current runtime limits."

        tool_timings = dict(getattr(self, "_turn_timings", {}) or {})
        tool_timings.setdefault("kind", "tools")
        if getattr(self, "_turn_started", None) is not None:
            tool_timings["total_s"] = round(time.monotonic() - self._turn_started, 1)
        await self.brain.turn(
            session_id,
            user_text,
            final_text,
            metadata={"source": source, "autonomous": autonomous, "tool_trace": tool_trace, "timings": tool_timings},
        )
        await self.brain.episode(user_text, final_text, session_id=session_id)
        consolidation: dict[str, Any] = {"stored": [], "reason": "disabled"}
        research_consolidation: list[dict[str, Any]] = []
        if bool(self.settings.get("brain.automatic_consolidation", True)):
            # Background, serialized, never canceled — identical policy to
            # plain turns. Running these inline added 1-2 synchronous LLM
            # calls to every tool turn's reply.
            research_evidence = next(
                (
                    trace.get("result")
                    for trace in tool_trace
                    if trace.get("tool") == "research"
                    and isinstance(trace.get("result"), dict)
                    and trace["result"].get("sources")
                ),
                None,
            )

            async def consolidate_soon() -> None:
                try:
                    await asyncio.sleep(float(self.settings.get("brain.consolidation_idle_seconds", 8)))
                    await self._wait_until_idle()
                    async with self._consolidation_lock:
                        await self.brain.consolidate_turn(user_text, final_text, session_id)
                        if research_evidence is not None:
                            await self.brain.rpc.call(
                                "consolidate_research",
                                {"answer": final_text, "evidence": research_evidence, "session_id": session_id},
                            )
                except Exception:
                    LOG.exception("Background tool-turn consolidation failed")

            task = asyncio.create_task(consolidate_soon(), name="kendra-tool-consolidation")
            self._consolidation_tasks.add(task)
            task.add_done_callback(self._consolidation_tasks.discard)
            consolidation = {"stored": [], "reason": "queued-in-background"}
        return {
            "text": final_text,
            "affect": final_affect,
            "session_id": session_id,
            "tool_trace": tool_trace,
            "memory_consolidation": consolidation,
            "research_memory_consolidation": research_consolidation,
        }

    async def stream_voice_turn(
        self,
        user_text: str,
        on_delta,
        *,
        session_id: str | None = None,
        source: str = "voice",
    ) -> dict[str, Any]:
        """Run a voice interaction with a streamed final response.

        Tool decisions remain schema-constrained and fully validated. Once the
        planner decides it is ready to respond, a second local llama.cpp call
        streams natural-language deltas to ``on_delta(text, affect)``. This lets
        the voice service begin local Piper synthesis before generation ends.
        """

        user_text = user_text.strip()
        if not user_text:
            raise ValueError("User text cannot be empty")
        session_id = session_id or uuid.uuid4().hex
        self._active_turns += 1
        self._turn_started = time.monotonic()
        self._turn_timings = {"kind": "chat"}
        try:
            return await self._stream_voice_turn_inner(
                user_text, on_delta, session_id=session_id, source=source
            )
        finally:
            self._active_turns -= 1
            self._last_turn_finished = time.time()

    async def _stream_voice_turn_inner(
        self,
        user_text: str,
        on_delta,
        *,
        session_id: str,
        source: str,
    ) -> dict[str, Any]:
        await self.brain.begin_session(session_id, context=source)
        diagnostic = await self._diagnostic_turn(user_text)
        if diagnostic is not None:
            spoken, meta = diagnostic
            await on_delta(spoken, "warm")
            result = await self._remember_plain_turn(
                session_id, user_text, spoken, source=source, streamed=True,
            )
            result["diagnostic"] = meta
            return result
        movement = await self._movement_turn(user_text, on_delta=on_delta)
        if movement is not None:
            spoken, meta = movement
            result = await self._remember_plain_turn(
                session_id, user_text, spoken, source=source, streamed=True,
            )
            result["movement"] = meta
            return result
        content_task = bool(self._CONTENT_TASK.search(user_text))

        build_note = (
            await self._build_plan_note(user_text) if self._BUILD_QUESTION.search(user_text) else []
        )
        cached_answer = None if build_note else await self._cached_answer(user_text)
        if cached_answer:
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "cached"
            # The restate note rides BEHIND the standard cached prefix
            # (charter + exemplars + history); a divergent prefix re-paid the
            # full prefill and made the "fast" path slower than a cold turn.
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(user_text),
                {
                    "role": "system",
                    "content": (
                        "Jonathan just asked the same thing again. Restate this earlier "
                        "answer of yours briefly and in different words — do not repeat "
                        "it verbatim and do not add new claims:\n" + cached_answer[:500]
                    ),
                },
                {"role": "user", "content": user_text},
            ]
            final_text_parts: list[str] = []
            async for delta in self.llm.stream_chat(
                messages, max_tokens=90, id_slot=self.CONVERSATION_SLOT
            ):
                final_text_parts.append(delta)
                await on_delta(delta, "warm")
            final_text = "".join(final_text_parts).strip() or cached_answer[:200]
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, streamed=True
            )

        if not self._schemas_for_text(user_text):
            memory = await self.brain.context(
                user_text,
                limit=int(self.settings.get("brain.live_retrieval_limit", 3)),
                character_budget=int(self.settings.get("brain.live_context_character_budget", 1400)),
                include_self_model=False,
                exclude_kinds=["episode"],
            )
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(user_text),
                *self._memory_message(memory),
                *build_note,
                {"role": "user", "content": user_text},
            ]
            final_text_parts: list[str] = []
            thinking = self._wants_thinking(user_text)
            async for delta in self.llm.stream_chat(
                messages,
                max_tokens=self._answer_budget(content_task)
                + (int(self.settings.get("llm.thinking_budget", 512)) if thinking else 0),
                thinking=thinking,
                id_slot=self.CONVERSATION_SLOT,
            ):
                final_text_parts.append(delta)
                await on_delta(delta, "warm")
            final_text = "".join(final_text_parts).strip()
            if not final_text:
                final_text = "I'm here."
                await on_delta(final_text, "warm")
            result = await self._remember_plain_turn(
                session_id,
                user_text,
                final_text,
                source=source,
                streamed=True,
            )
            return result

        capabilities, observation = await self._body_context()
        registry = ToolRegistry(self.settings, capabilities)
        tool_schemas = self._relevant_tool_schemas(user_text, registry.schemas())
        allowed_tools = {str(schema["name"]) for schema in tool_schemas}
        if "research" not in allowed_tools and await self._owes_research(user_text):
            # She promised to look something up last turn: pay the debt now
            # instead of promising again.
            tool_schemas = [s for s in registry.schemas() if str(s.get("name")) == "research"]
            allowed_tools = {"research"}
        if "observe" in allowed_tools:
            # Acknowledge the task out loud BEFORE the slow work: Jonathan
            # asked her to do something, and silence until the answer reads
            # as ignoring him (his words). Also free perceived latency —
            # she speaks while her eyes work.
            if self._WHO_QUESTION.search(user_text):
                fast = await self._fast_who_answer(user_text)
                if fast is not None:
                    reply, launch_meet = fast
                    await on_delta(reply, "delighted" if not launch_meet else "curious")
                    result = await self._remember_plain_turn(
                        session_id, user_text, reply, source=source, streamed=True
                    )
                    if launch_meet:
                        result["meet_person"] = True
                    return result
            await on_delta("Let me take a look right now. ", "curious")
            # Sight and memory retrieval are independent — overlap them.
            # Profiled: serializing them added the full retrieval time to
            # every sight turn for nothing.
            # Qwen3-Omni chunked-prefill analogue: while her eyes work, the
            # answer's stable prefix warms on slot 0 in parallel, so the
            # post-look prefill pays only for the scene note and user words.
            memory, _, _ = await asyncio.gather(
                self.brain.context(
                    self._memory_query(user_text, observation),
                    exclude_kinds=["episode", "observation"],
                ),
                self._look_now(user_text, observation),
                self.prewarm_conversation(),
            )
            if not observation.get("visual_scene"):
                # Universal blind-sight gate, on EVERY answer path: with no
                # fresh image she must say so in fixed words. Old observation
                # memories otherwise let the model narrate remembered scenes
                # as if seeing them ("dark wood table", the invented book).
                final_text = (
                    "My deep sight is still waking up — give me a few seconds and ask me again."
                    if "processing" in str(observation.get("visual_scene_error") or "")
                    else "I can't actually see right now — my camera feed isn't reaching me. Get my eyes back and ask me again."
                )
                await on_delta(final_text, "concern")
                return await self._remember_plain_turn(
                    session_id, user_text, final_text, source=source, streamed=True
                )
            if self._WHO_QUESTION.search(user_text):
                # Identity questions are answered by her face recognizer,
                # deterministically — and an unfamiliar face launches the
                # meet ritual the moment this reply finishes.
                names = observation.get("recognized_people_names") or []
                people = int(observation.get("people_in_view") or 0)
                if names:
                    final_text = f"That's {' and '.join(names)}!"
                elif people > 0:
                    final_text = (
                        "I can see someone, but I don't know them yet — "
                        "let me introduce myself!"
                    )
                else:
                    final_text = "I don't see anyone in view right now."
                await on_delta(final_text, "delighted" if names else "curious")
                result = await self._remember_plain_turn(
                    session_id, user_text, final_text, source=source, streamed=True
                )
                if people > 0 and not names:
                    result["meet_person"] = True
                return result
        else:
            # Observations excluded from plain chat: fresh sight memories made
            # her narrate Jonathan's lunch in every reply. Sight and recall
            # paths still retrieve them.
            memory = await self.brain.context(
                self._memory_query(user_text, observation),
                exclude_kinds=["episode", "observation"],
            )
        if allowed_tools == {"recall"}:
            # Pure memory question by voice: search the brain directly and
            # answer on the warm conversation slot — the full planner added
            # 20-60s to "what do you remember about X" for no decision value.
            # Strip recall boilerplate before searching: "do you remember
            # what computer..." should embed as "what computer...", not as a
            # sentence about remembering.
            recall_query = re.sub(
                r"^(?:hey\s+)?(?:do you remember|what do you remember about|do you recall|can you recall|remember)\s*[,:]?\s*",
                "",
                user_text,
                flags=re.I,
            ).strip() or user_text
            try:
                ctx = await self.brain.context(
                    recall_query,
                    limit=6,
                    character_budget=1400,
                    include_self_model=False,
                    exclude_kinds=["episode"],
                )
                hits = list(ctx.get("memories", []))
            except Exception:
                hits = []
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "memory"
            recalled = [
                {"when": str(h.get("created_at", ""))[:16], "note": str(h.get("content", ""))[:220]}
                for h in hits[:4]
            ]
            note = (
                "WHAT YOUR MEMORY RETURNED — these are your own accurate memories; "
                "trust them fully, including plans for your future body (answer "
                "from them; only if empty say you have no memory of it):\n"
                + json.dumps(recalled, ensure_ascii=False)
            )
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(user_text),
                {"role": "system", "content": note},
                {"role": "user", "content": user_text},
            ]
            final_text_parts: list[str] = []
            async for delta in self.llm.stream_chat(
                messages,
                max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                id_slot=self.CONVERSATION_SLOT,
            ):
                final_text_parts.append(delta)
                await on_delta(delta, "reflective")
            final_text = "".join(final_text_parts).strip() or "Nothing surfaced from memory."
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, streamed=True
            )
        if allowed_tools == {"research"}:
            # Pure research question by voice: her own brain first (fresh
            # researched memories answer instantly), the network second.
            search_started = time.monotonic()
            cached = await self._recent_research_memories(user_text)
            if cached:
                evidence = {"mode": "brain-cache", "sources": cached}
            else:
                # Spoken acknowledgment before the slow network round trip —
                # the task is accepted aloud instead of silent searching.
                await on_delta("On it — give me a moment to actually search. ", "warm")
                try:
                    evidence = await registry.execute("research", {"query": user_text[:200]})
                except Exception as exc:
                    evidence = {"sources": [], "error": f"{type(exc).__name__}"}
            timings = getattr(self, "_turn_timings", None)
            if timings is not None:
                timings["kind"] = "research (memory)" if cached else "research (online)"
                timings["search_s"] = round(time.monotonic() - search_started, 1)
            if not (isinstance(evidence, dict) and evidence.get("sources")):
                # Empty evidence never reaches the model: a 2B model asked to
                # discuss a failed search either confabulates specifics or
                # offers to do the search it just did. Deterministic honesty.
                final_text = (
                    "I searched just now and nothing solid came back. "
                    "Want me to try different words?"
                )
                await on_delta(final_text, "concern")
                return await self._remember_plain_turn(
                    session_id, user_text, final_text, source=source, streamed=True
                )
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(user_text, users_only=True),
                *self._evidence_note(evidence if isinstance(evidence, dict) else {}),
                {"role": "user", "content": user_text},
            ]
            final_text_parts: list[str] = []
            # Streamed audio cannot be retracted, so the capability preamble
            # Gemma insists on ("I can look it up... I see a few things...")
            # is cut deterministically BEFORE the first phrase reaches Piper:
            # buffer the opening, strip the tic, then stream normally.
            capability_lead = re.compile(
                # Promise-instead-of-deliver: she searched, then opened with
                # "I'll look that up for you." Contractions and future tense
                # were missing from the guard, so the promise reached the
                # speaker and the findings never did.
                r"^\s*(?:(?:I can (?:look|check|search)[^.!?]*|I see a (?:few|couple)[^.!?]*"
                r"|(?:I'?ll|I will|Let me|I'?m going to|I am going to)\s+"
                r"(?:look|check|search|find|see|tell you|get)[^.!?]*"
                r"|Sure[^.!?]*)[.!?]\s*)+",
                re.I,
            )
            lead_pending = True
            lead_buffer = ""
            async for delta in self.llm.stream_chat(
                messages,
                max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
            ):
                final_text_parts.append(delta)
                if lead_pending:
                    lead_buffer += delta
                    if len(lead_buffer) < 160 and not re.search(r"[.!?]\s", lead_buffer[60:]):
                        continue
                    cleaned = capability_lead.sub("", self._FILLER_OPENER.sub("", lead_buffer.lstrip()))
                    lead_pending = False
                    if cleaned.strip():
                        await on_delta(cleaned, "curious")
                    continue
                await on_delta(delta, "curious")
            if lead_pending and lead_buffer:
                cleaned = capability_lead.sub("", self._FILLER_OPENER.sub("", lead_buffer.lstrip()))
                await on_delta(cleaned or lead_buffer, "curious")
            final_text = "".join(final_text_parts).strip() or "My search came back empty."
            final_text = capability_lead.sub("", self._FILLER_OPENER.sub("", self._strip_unasked_clock(final_text, user_text))).strip()
            # Grounding check on the spoken path too. Streaming means the
            # first phrases are already out loud, so a correction is spoken
            # as a correction — which is honest, and far better than leaving
            # an invented "founded in 2006 by the Weil family" standing.
            checked = await self._grounding_guard(
                final_text, evidence if isinstance(evidence, dict) else {}, user_text,
                lambda bad: self.llm.chat(
                    messages + [{
                        "role": "system",
                        "content": (
                            "STOP. These details are NOT in your evidence and you invented "
                            f"them: {', '.join(bad[:6])}. Rewrite using only facts that "
                            "literally appear in the evidence. If it does not answer him, "
                            "say so plainly."
                        ),
                    }],
                    temperature=0.3,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 160)),
                ),
            )
            if checked != final_text:
                # Do NOT re-send the corrected text as speech: the original
                # has already streamed, so resending near-identical wording
                # trips her duplicate-phrase guard and she ends up saying
                # NOTHING at all — endless thinking tones with no voice.
                # One short spoken retraction, and the corrected text is
                # what gets stored.
                LOG.warning("Grounding correction on a spoken answer: %r", checked[:90])
                await on_delta(" Actually — I couldn't verify those details, so ignore that part.", "concern")
                final_text = checked
            if (evidence.get("mode") != "brain-cache" and evidence.get("sources")
                    and getattr(self, "_last_answer_grounded", True)):
                self._consolidate_research_soon(user_text, final_text, evidence, session_id)
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, streamed=True
            )
        if allowed_tools == {"observe"}:
            if not observation.get("visual_scene"):
                # Her eyes returned nothing. NEVER generate: told to "say so
                # honestly", the model instead invented a book cover, twice.
                # Blindness is stated in fixed words or not at all.
                final_text = (
                    "My deep sight is still waking up — give me a few seconds and ask me again."
                    if "processing" in str(observation.get("visual_scene_error") or "")
                    else "I can't actually see right now — my camera feed isn't reaching me. Get my eyes back and ask me again."
                )
                await on_delta(final_text, "concern")
                return await self._remember_plain_turn(
                    session_id, user_text, final_text, source=source, streamed=True
                )
            # Pure sight question by voice: skip the planner, stream the
            # answer straight off the prewarmed conversation prefix.
            messages = [
                {"role": "system", "content": self._conversation_prompt()},
                *self._style_exemplars(),
                *await self._history_messages(user_text),
                *self._memory_message(memory),
                *self._scene_note(observation),
                {"role": "user", "content": user_text},
            ]
            final_text_parts: list[str] = []
            async for delta in self.llm.stream_chat(
                messages,
                max_tokens=int(self.settings.get("llm.conversation_max_tokens", 200)),
                id_slot=self.CONVERSATION_SLOT,
            ):
                final_text_parts.append(delta)
                await on_delta(delta, "curious")
            final_text = "".join(final_text_parts).strip()
            if not final_text:
                final_text = "My eyes did not give me anything useful just now."
                await on_delta(final_text, "concern")
            checked = await self._sight_grounding_guard(
                final_text, observation, user_text,
                lambda bad: self.llm.chat(
                    messages + [{
                        "role": "system",
                        "content": (
                            "STOP. Your eyes did not report these and you invented them: "
                            f"{', '.join(bad[:6])}. Answer using ONLY what the description "
                            "says. If it does not cover what he asked, say plainly that you "
                            "cannot make it out."
                        ),
                    }],
                    temperature=0.3,
                    max_tokens=int(self.settings.get("llm.conversation_max_tokens", 120)),
                ),
            )
            if checked != final_text:
                await on_delta(" Actually — I can't verify that part.", "concern")
                final_text = checked
            return await self._remember_plain_turn(
                session_id, user_text, final_text, source=source, streamed=True
            )
        planner_messages = self._planner_messages(capabilities, observation, memory, tool_schemas)
        planner_messages[-1]["content"] += "\n\nVOICE STREAM MODE:\n- Keep planner JSON tiny. For respond/done, put only a short intent summary in reason; final spoken prose is generated separately."
        messages: list[dict[str, Any]] = [
            *planner_messages,
            *await self._history_messages(user_text),
            {"role": "user", "content": user_text},
        ]

        movement_calls = 0
        started = time.monotonic()
        final_affect = "warm"
        tool_trace: list[dict[str, Any]] = []
        response_intent = "Answer the user directly and naturally."

        for _step in range(self.max_tool_steps + 1):
            if time.monotonic() - started > self.mission_timeout:
                response_intent = "Explain briefly that the interaction reached its deterministic time budget."
                final_affect = "concern"
                break
            raw = await self.llm.chat(
                messages,
                response_schema=PlannerAction.model_json_schema(),
                temperature=0.1,
                max_tokens=180,
                id_slot=self.PLANNER_SLOT,
            )
            try:
                action = PlannerAction.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                LOG.warning("Voice planner returned invalid JSON: %s", exc)
                messages.append({"role": "system", "content": "Return one valid PlannerAction JSON object only."})
                continue

            if action.action in {"respond", "done"}:
                response_intent = (action.reason or action.text or response_intent).strip()
                final_affect = action.affect or "warm"
                break

            if action.action != "tool" or not action.tool:
                messages.append({"role": "system", "content": "A tool action requires a whitelisted tool name."})
                continue
            if action.tool not in allowed_tools:
                messages.append({"role": "system", "content": "That tool is not available for this turn."})
                continue

            if registry.is_movement(action.tool):
                movement_calls += 1
                if movement_calls > self.max_movement_calls:
                    result: Any = {"ok": False, "error": "movement budget exhausted"}
                    tool_trace.append({"tool": action.tool, "args": action.args, "result": result})
                    messages.append({"role": "user", "content": f"TOOL RESULT {action.tool}: {json.dumps(result)}"})
                    continue

            try:
                result = await registry.execute(action.tool, action.args)
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            tool_trace.append({"tool": action.tool, "args": action.args, "result": result})
            await self._remember_observation(action.tool, result)
            messages.append(
                {
                    "role": "user",
                    "content": f"TOOL RESULT {action.tool}:\n{json.dumps(result, ensure_ascii=False)[:6000]}",
                }
            )
        else:
            response_intent = "Explain briefly that the deterministic tool-step budget was reached."
            final_affect = "concern"

        final_messages = list(messages)
        final_messages.append(
            {
                "role": "system",
                "content": (
                    "Now produce Kendra's final spoken answer as plain text, not JSON. "
                    "Do not reveal chain-of-thought. Be concise enough for natural speech, "
                    "but complete. Do not claim a tool succeeded unless its result above says it did. "
                    f"Response intent: {response_intent}"
                ),
            }
        )
        final_text_parts: list[str] = []
        async for delta in self.llm.stream_chat(final_messages, temperature=self.settings.get("llm.temperature", 0.35)):
            final_text_parts.append(delta)
            await on_delta(delta, final_affect)
        final_text = "".join(final_text_parts).strip()
        if not final_text:
            final_text = "I could not complete that safely within my current runtime limits."
            await on_delta(final_text, "concern")
            final_affect = "concern"

        tool_timings = dict(getattr(self, "_turn_timings", {}) or {})
        tool_timings.setdefault("kind", "tools")
        if getattr(self, "_turn_started", None) is not None:
            tool_timings["total_s"] = round(time.monotonic() - self._turn_started, 1)
        await self.brain.turn(
            session_id,
            user_text,
            final_text,
            metadata={"source": source, "streamed": True, "tool_trace": tool_trace, "timings": tool_timings},
        )
        await self.brain.episode(user_text, final_text, session_id=session_id)
        consolidation: dict[str, Any] = {"stored": [], "reason": "disabled"}
        research_consolidation: list[dict[str, Any]] = []
        if bool(self.settings.get("brain.automatic_consolidation", True)):
            # Background, serialized, never canceled — identical policy to
            # plain turns. Running these inline added 1-2 synchronous LLM
            # calls to every tool turn's reply.
            research_evidence = next(
                (
                    trace.get("result")
                    for trace in tool_trace
                    if trace.get("tool") == "research"
                    and isinstance(trace.get("result"), dict)
                    and trace["result"].get("sources")
                ),
                None,
            )

            async def consolidate_soon() -> None:
                try:
                    await asyncio.sleep(float(self.settings.get("brain.consolidation_idle_seconds", 8)))
                    await self._wait_until_idle()
                    async with self._consolidation_lock:
                        await self.brain.consolidate_turn(user_text, final_text, session_id)
                        if research_evidence is not None:
                            await self.brain.rpc.call(
                                "consolidate_research",
                                {"answer": final_text, "evidence": research_evidence, "session_id": session_id},
                            )
                except Exception:
                    LOG.exception("Background tool-turn consolidation failed")

            task = asyncio.create_task(consolidate_soon(), name="kendra-tool-consolidation")
            self._consolidation_tasks.add(task)
            task.add_done_callback(self._consolidation_tasks.discard)
            consolidation = {"stored": [], "reason": "queued-in-background"}
        return {
            "text": final_text,
            "affect": final_affect,
            "session_id": session_id,
            "tool_trace": tool_trace,
            "memory_consolidation": consolidation,
            "research_memory_consolidation": research_consolidation,
        }
