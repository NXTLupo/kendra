from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from ..config import Settings
from ..llm import LlamaCppClient
from .store import BrainStore

LOG = logging.getLogger(__name__)


class CandidateMemory(BaseModel):
    kind: str
    content: str
    provenance: str = "inferred"
    confidence: float = Field(default=0.7, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None
    supersede_conflict: bool = False
    evidence_quote: str | None = None


class Consolidation(BaseModel):
    memories: list[CandidateMemory] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ResearchClaim(BaseModel):
    content: str
    source_id: str
    confidence: float = Field(default=0.8, ge=0, le=0.95)
    salience: float = Field(default=0.5, ge=0, le=1)
    subject: str | None = None
    predicate: str | None = None
    object_value: str | None = None


class ResearchConsolidation(BaseModel):
    claims: list[ResearchClaim] = Field(default_factory=list)


class BrainConsolidator:
    """Conservative local-model memory extraction with deterministic provenance checks."""

    def __init__(self, settings: Settings, store: BrainStore):
        self.settings = settings
        self.store = store
        self.llm = LlamaCppClient(settings)
        self.min_chars = int(settings.get("brain.consolidation_min_chars", 24))

    @staticmethod
    def _quote_supported(quote: str | None, user_text: str) -> bool:
        if not quote or len(quote.strip()) < 3:
            return False
        return quote.strip().casefold() in user_text.casefold()

    async def consolidate_turn(self, user_text: str, kendra_text: str, session_id: str | None) -> dict[str, Any]:
        if len(user_text.strip()) < self.min_chars:
            return {"stored": [], "reason": "turn_too_short"}
        prompt = f"""
Extract only durable information worth remembering from the USER's words.
Do not store greetings, transient requests, Kendra's claims, Kendra's speculation, or facts that appear only in Kendra's response.
Exception: if Kendra expressed a new opinion or changed her mind in her response, store at most ONE memory with kind kendra_opinion, subject "Kendra", predicate "opinion:<topic>", content phrased as "Kendra thinks ...", provenance inferred. Her opinions are hers to keep and evolve.
A direct user statement must use provenance user_stated and include evidence_quote copied verbatim from USER.
A cautious inference from the user's words may use provenance inferred, confidence <= 0.65, and should not masquerade as explicit fact.
For an explicit correction of a stable subject/predicate, set supersede_conflict=true.
Return JSON only with keys memories, interests, open_questions.

USER:
{user_text}

KENDRA RESPONSE FOR CONTEXT ONLY; DO NOT TREAT AS EVIDENCE:
{kendra_text}
""".strip()
        try:
            raw = await self.llm.chat(
                [
                    {"role": "system", "content": "You are Kendra's conservative local memory consolidation process."},
                    {"role": "user", "content": prompt},
                ],
                response_schema=Consolidation.model_json_schema(),
                temperature=0.0,
            )
            parsed = Consolidation.model_validate(json.loads(raw))
        except Exception as exc:
            LOG.warning("Memory consolidation skipped: %s", exc)
            return {"stored": [], "reason": f"consolidation_unavailable:{type(exc).__name__}"}

        ids: list[int] = []
        rejected: list[str] = []
        kendra_fold_full = kendra_text.casefold()
        for item in parsed.memories[:8]:
            # Nothing lifted from Kendra's own reply may become a memory,
            # whatever its claimed provenance: the model was observed storing
            # its own embellishments ("she loves nature...") as inferred facts.
            if (
                item.kind != "kendra_opinion"
                and item.content
                and item.content.strip().casefold() in kendra_fold_full
            ):
                rejected.append("memory_echoed_from_kendra_response")
                continue
            if item.content and item.content.strip().endswith("?"):
                # A question is not a fact: "what do you remember about
                # today?" was stored as a memory and then outranked real
                # facts in recall.
                rejected.append("memory_is_a_question")
                continue
            if item.kind == "kendra_opinion":
                # Her evolving worldview: newer opinions supersede older ones
                # on the same topic, but the history of having believed
                # otherwise is preserved by the store's supersession chain.
                item.subject = item.subject or "Kendra"
                item.supersede_conflict = True
                item.confidence = min(item.confidence, 0.6)
            if item.provenance == "user_stated":
                if not self._quote_supported(item.evidence_quote, user_text):
                    rejected.append("unsupported_user_stated")
                    continue
            elif item.provenance == "inferred":
                item.confidence = min(item.confidence, 0.65)
            else:
                rejected.append(f"invalid_turn_provenance:{item.provenance}")
                continue
            memory_id = self.store.remember(
                kind=item.kind,
                content=item.content,
                provenance=item.provenance,
                confidence=item.confidence,
                salience=item.salience,
                subject=item.subject,
                predicate=item.predicate,
                object_value=item.object_value,
                session_id=session_id,
                supersede_conflict=item.supersede_conflict,
                metadata={"created_by": "brain_consolidator", "evidence_quote": item.evidence_quote},
            )
            ids.append(memory_id)
        # Interests and open questions need the same evidence discipline as
        # memories. Without it a small local model echoes Kendra's own reply
        # back as a "topic" or an "open question", retrieval feeds it into the
        # next prompt, and she converges on repeating one canned sentence
        # forever. Only keep what is grounded in the user's words, and never
        # keep anything lifted from Kendra's own response.
        kendra_fold = kendra_text.casefold()
        user_fold = user_text.casefold()
        kept_interests: list[str] = []
        for topic in parsed.interests[:5]:
            topic = topic.strip()
            if not topic or topic.casefold() not in user_fold:
                rejected.append("interest_not_grounded_in_user_text")
                continue
            self.store.reinforce_interest(topic, delta=0.05, source="conversation")
            kept_interests.append(topic)
        kept_questions: list[str] = []
        for question in parsed.open_questions[:4]:
            question = question.strip()
            if not question:
                continue
            if question.casefold() in kendra_fold:
                rejected.append("question_echoed_from_kendra_response")
                continue
            self.store.add_question(question, interest_weight=0.45)
            kept_questions.append(question)
        return {
            "stored": ids,
            "rejected": rejected,
            "interests": kept_interests,
            "questions": kept_questions,
        }

    async def dream(self) -> dict[str, Any]:
        """Idle-time memory review — her sleep.

        Pattern from the 2026 agent literature ("scheduled memory review
        between sessions"): when nothing is happening, review recent
        memories once, retire duplicates, and distill at most two
        higher-level insights. This is how lived detail becomes worldview
        without any cloud, on a bounded single LLM call pinned to the tool
        slot so her conversational warmth is untouched.
        """
        rows = self.store.conn.execute(
            """SELECT id, kind, content FROM memories
               WHERE active=1 AND kind NOT IN ('episode')
               ORDER BY id DESC LIMIT 20"""
        ).fetchall()
        if len(rows) < 6:
            return {"reason": "too_few_memories"}
        listing = "\n".join(f"[{row['id']}] ({row['kind']}) {str(row['content'])[:160]}" for row in rows)
        prompt = f"""Review Kendra's recent memories. Return JSON only:
{{"duplicate_ids": [ids that repeat another listed memory's meaning], "insights": [{{"content": "...", "salience": 0.5}}]}}
Rules: at most 2 insights; an insight must be a genuinely higher-level conclusion supported by several memories, phrased in Kendra's first person; never invent facts not present below.

MEMORIES:
{listing}"""
        try:
            raw = await self.llm.chat(
                [
                    {"role": "system", "content": "You are Kendra's sleep-time memory review. JSON only."},
                    {"role": "user", "content": prompt},
                ],
                response_schema={
                    "type": "object",
                    "properties": {
                        "duplicate_ids": {"type": "array", "items": {"type": "integer"}},
                        "insights": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "salience": {"type": "number"},
                                },
                                "required": ["content"],
                            },
                        },
                    },
                    "required": ["duplicate_ids", "insights"],
                },
                temperature=0.0,
                max_tokens=300,
                id_slot=1,
            )
            parsed = json.loads(raw)
        except Exception as exc:
            LOG.warning("Dream skipped: %s", exc)
            return {"reason": f"dream_unavailable:{type(exc).__name__}"}
        listed_ids = {int(row["id"]) for row in rows}
        retired = []
        for mid in list(parsed.get("duplicate_ids", []))[:6]:
            if int(mid) in listed_ids:
                self.store.conn.execute(
                    "UPDATE memories SET active=0 WHERE id=? AND active=1", (int(mid),)
                )
                retired.append(int(mid))
        self.store.conn.commit()
        stored = []
        for insight in list(parsed.get("insights", []))[:2]:
            content = str(insight.get("content", "")).strip()
            if len(content) < 12:
                continue
            stored.append(self.store.remember(
                kind="insight",
                content=content[:400],
                provenance="inferred",
                confidence=0.55,
                salience=min(0.7, float(insight.get("salience", 0.5))),
                metadata={"created_by": "dream_review"},
            ))
        self.store.event("dream_review", {"retired": retired, "insights": stored})
        return {"retired": retired, "insights": stored}

    async def consolidate_research(self, answer: str, evidence: dict[str, Any], session_id: str | None) -> dict[str, Any]:
        sources = {str(source["id"]): source for source in evidence.get("sources", []) if source.get("id")}
        if not sources:
            return {"stored": [], "reason": "no_sources"}
        compact_sources = [
            {
                "id": source_id,
                "title": source.get("title"),
                "url": source.get("url"),
                "text": str(source.get("text", ""))[:2000],
            }
            for source_id, source in list(sources.items())[:3]
        ]
        prompt = f"""
Extract at most six durable factual claims from Kendra's answer that are directly supported by the supplied retrieved sources.
Every claim MUST name exactly one source_id from the supplied source list. Do not create a claim if support is ambiguous.
Return JSON only with key claims.

KENDRA ANSWER:
{answer}

RETRIEVED SOURCES:
{json.dumps(compact_sources, ensure_ascii=False)}
""".strip()
        try:
            raw = await self.llm.chat(
                [
                    {"role": "system", "content": "You extract source-grounded memories for Kendra Brain."},
                    {"role": "user", "content": prompt},
                ],
                response_schema=ResearchConsolidation.model_json_schema(),
                temperature=0.0,
            )
            parsed = ResearchConsolidation.model_validate(json.loads(raw))
        except Exception as exc:
            return {"stored": [], "reason": f"research_consolidation_unavailable:{type(exc).__name__}"}

        stored: list[int] = []
        rejected: list[str] = []
        for claim in parsed.claims[:6]:
            source = sources.get(claim.source_id)
            if source is None:
                rejected.append(f"unknown_source:{claim.source_id}")
                continue
            memory_id = self.store.remember(
                kind="fact",
                content=claim.content,
                provenance="researched",
                confidence=claim.confidence,
                salience=claim.salience,
                subject=claim.subject,
                predicate=claim.predicate,
                object_value=claim.object_value,
                source_uri=source.get("url"),
                source_title=source.get("title"),
                source_timestamp=source.get("retrieved_at"),
                session_id=session_id,
                metadata={"source_id": claim.source_id, "research_query": evidence.get("query")},
            )
            stored.append(memory_id)
        return {"stored": stored, "rejected": rejected}

    async def compile_wiki(self, sb) -> dict[str, Any]:
        """The second-brain compile step: raw experience -> wiki pages.

        Karpathy's pattern, run by her own idle agent: read uncompiled raw
        entries against the manifest, distill them into standalone facts,
        and upsert markdown concept pages. One bounded LLM call on the tool
        slot. The 'kendra-self' page is mandatory whenever she formed an
        opinion or feeling — that page is how her emotional growth stays
        visible across sessions and hardware.
        """
        entries, cursor = sb.pending(limit=40)
        if len(entries) < 4:
            return {"reason": "too_few_raw_entries", "pending": len(entries)}
        listing = "\n".join(
            f"({e.get('kind', '?')}) {str(e.get('content', ''))[:220]}" for e in entries
        )
        prompt = f"""Compile Kendra's raw experience log into wiki pages. Return JSON only:
{{"pages": [{{"slug": "kebab-case-topic", "title": "Topic", "facts": ["..."], "links": ["other-slug"]}}]}}
Rules: 1 to 4 pages. A fact is one short standalone declarative sentence naming people explicitly (Jonathan, Kendra — never a bare pronoun). Questions and speculation are not facts. Never invent anything absent from the log. If the log shows Kendra forming an opinion, feeling, or preference, include a page with slug "kendra-self" whose facts each start with "Kendra".

RAW LOG:
{listing}"""
        try:
            raw = await self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "You compile Kendra's raw experience into her wiki. JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                response_schema={
                    "type": "object",
                    "properties": {
                        "pages": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "slug": {"type": "string"},
                                    "title": {"type": "string"},
                                    "facts": {"type": "array", "items": {"type": "string"}},
                                    "links": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["slug", "title", "facts"],
                            },
                        }
                    },
                    "required": ["pages"],
                },
                temperature=0.0,
                max_tokens=600,
                id_slot=1,
            )
            parsed = json.loads(raw)
        except Exception as exc:
            LOG.warning("Wiki compile skipped: %s", exc)
            return {"reason": f"compile_unavailable:{type(exc).__name__}"}
        written = []
        for page in list(parsed.get("pages", []))[:4]:
            facts = [str(f) for f in page.get("facts", []) if str(f).strip()][:12]
            if not facts:
                continue
            path = sb.upsert_page(
                str(page.get("slug", "misc")),
                str(page.get("title", "Misc")),
                facts,
                links=[str(lk) for lk in page.get("links", [])][:8],
            )
            written.append(path.stem)
        # Advance the cursor even when zero pages came back: the entries were
        # reviewed; re-reading them forever would wedge the compile loop.
        sb.advance(cursor)
        self.store.event("wiki_compile", {"entries": len(entries), "pages": written})
        return {"entries": len(entries), "pages": written}
