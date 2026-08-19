from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from .backup import backup_sqlite, export_jsonl
from .consolidator import BrainConsolidator
from .second_brain import SecondBrain
from .store import BrainStore

LOG = logging.getLogger(__name__)


class BrainService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = BrainStore.from_settings(settings)
        self.consolidator = BrainConsolidator(settings, self.store)
        self.second_brain = SecondBrain(settings.path("brain.second_brain.dir"))
        self.server = UnixJsonServer(settings.socket_path("brain"), self.handle)

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, **self.store.stats()}
        if method == "stats":
            return self.store.stats()
        if method == "event":
            self.store.event(str(params["event_type"]), dict(params.get("payload") or {}))
            return {"ok": True}
        if method == "dashboard_snapshot":
            return self.store.dashboard_snapshot(limit=int(params.get("limit", 20)))
        if method == "remember":
            # Consolidated memories also land in the raw experience log, so
            # observations and learned facts reach the wiki compile step.
            # System-provenance seeds skip it: docs/ already hold them.
            if str(params.get("provenance") or "") != "system":
                self.second_brain.ingest(
                    str(params.get("kind") or "memory"), str(params.get("content") or "")
                )
            return {"memory_id": self.store.remember(**params)}
        if method == "get_memory":
            return self.store.get_memory(int(params["memory_id"]))
        if method == "correct":
            values = dict(params)
            memory_id = int(values.pop("memory_id"))
            return {"memory_id": self.store.correct(memory_id, **values)}
        if method == "search":
            hits = self.store.search(
                str(params["query"]),
                limit=int(params.get("limit", 10)),
                kinds=params.get("kinds"),
                include_system=bool(params.get("include_system", False)),
            )
            return [hit.as_dict() for hit in hits]
        if method == "context":
            context = self.store.context_for(
                str(params["query"]),
                limit=int(params.get("limit", self.settings.get("brain.retrieval_limit", 12))),
                character_budget=int(
                    params.get(
                        "character_budget",
                        self.settings.get("brain.context_character_budget", 7000),
                    )
                ),
                include_self_model=bool(params.get("include_self_model", True)),
                exclude_kinds=params.get("exclude_kinds") or [],
            )
            # Execute step of the second-brain loop: the best compiled wiki
            # excerpt rides ahead of raw memories on every retrieval. File
            # read, sub-millisecond, identical on the Pi.
            try:
                for hit in reversed(self.second_brain.lookup(str(params["query"]), limit=1)):
                    context.setdefault("memories", []).insert(
                        0,
                        {
                            "content": f"[your wiki: {hit['title']}] {hit['excerpt'][:280]}",
                            "kind": "wiki",
                            "provenance": "wiki",
                            "created_at": "",
                        },
                    )
            except Exception:
                LOG.debug("Wiki lookup skipped", exc_info=True)
            return context
        if method == "episode":
            return {"memory_id": self.store.add_episode(**params)}
        if method == "dream":
            return await self.consolidator.dream()
        if method == "recent_turns":
            return self.store.recent_turns(
                limit=int(params.get("limit", 6)),
                max_age_seconds=float(params.get("max_age_seconds", 900)),
            )
        if method == "begin_session":
            self.store.begin_session(str(params["session_id"]), params.get("context"))
            return {"ok": True}
        if method == "turn":
            self.store.add_turn(**params)
            metadata = dict(params.get("metadata") or {})
            if not str(metadata.get("source") or "").startswith("probe"):
                self.second_brain.ingest(
                    "turn",
                    f"Jonathan: {params.get('user_text', '')}\n"
                    f"Kendra: {params.get('kendra_text', '')}",
                    {"session": params.get("session_id")},
                )
            return {"ok": True}
        if method == "end_session":
            self.store.end_session(str(params["session_id"]))
            return {"ok": True}
        if method == "interest":
            self.store.reinforce_interest(**params)
            return {"ok": True}
        if method == "question":
            return {"question_id": self.store.add_question(**params)}
        if method == "resolve_question":
            self.store.resolve_question(int(params["question_id"]), params.get("memory_id"))
            return {"ok": True}
        if method == "goal":
            return {"goal_id": self.store.add_goal(**params)}
        if method == "complete_goal":
            self.store.complete_goal(int(params["goal_id"]))
            return {"ok": True}
        if method == "set_self":
            self.store.set_self(**params)
            return {"ok": True}
        if method == "person":
            return {"person_id": self.store.upsert_person(**params)}
        if method == "place":
            return {"place_id": self.store.upsert_place(**params)}
        if method == "consolidate_turn":
            return await self.consolidator.consolidate_turn(
                str(params["user_text"]),
                str(params["kendra_text"]),
                params.get("session_id"),
            )
        if method == "set_fact":
            self.store.set_fact(str(params["subject"]), str(params["key"]), str(params["value"]))
            return {"ok": True}
        if method == "amend_last_turn":
            return {"ok": self.store.amend_last_turn(str(params["kendra_text"]))}
        if method == "meet_person":
            # The meet ritual's storage step: the person lands in her second
            # brain (raw + a dedicated wiki page) and her associative memory,
            # alongside the face embeddings the vision service just enrolled.
            name = str(params["name"]).strip()
            person_uid = params.get("person_uid")
            self.second_brain.ingest(
                "person",
                f"Kendra met {name} in person, introduced herself, and enrolled "
                f"their face so she will always recognize them.",
                {"person_uid": person_uid},
            )
            self.second_brain.upsert_page(
                f"person-{self.second_brain.slugify(name)}",
                name,
                [
                    f"{name} is a person Kendra met in person and can recognize by face.",
                ],
                links=["people"],
            )
            self.store.set_fact(name, "relationship", "met in person; recognized by face")
            self.store.remember(
                kind="relationship",
                content=f"Kendra met {name} in person and will recognize them by sight.",
                provenance="observed",
                confidence=1.0,
                salience=0.9,
            )
            return {"ok": True}
        if method == "consolidate_research":
            evidence = dict(params["evidence"])
            query = str(evidence.get("query") or "")
            self.second_brain.ingest(
                "research",
                (f"Question: {query}\n" if query else "") + f"Answer: {params['answer']}",
            )
            # Immediate wiki page too — not just raw awaiting the idle
            # compile: a repeat question must be one fast file read away.
            answer = str(params["answer"]).strip()
            if answer:
                import re as _re

                words = [
                    w
                    for w in _re.findall(r"[a-z0-9]+", query.casefold())
                    if len(w) > 2
                    and w not in {"the", "and", "for", "what", "who", "how", "why",
                                  "when", "where", "about", "tell", "latest", "current",
                                  "today", "news", "please", "kendra"}
                ][:5]
                slug = "research-" + self.second_brain.slugify(" ".join(words) or answer[:40])
                self.second_brain.upsert_page(
                    slug,
                    (query or answer[:60]).strip()[:70],
                    [answer[:400]],
                    links=["research"],
                )
            return await self.consolidator.consolidate_research(
                str(params["answer"]),
                evidence,
                params.get("session_id"),
            )
        if method == "wiki_stats":
            return self.second_brain.stats()
        if method == "wiki_lookup":
            return self.second_brain.lookup(
                str(params["query"]), limit=int(params.get("limit", 3))
            )
        if method == "wiki_page":
            return {"content": self.second_brain.read_page(str(params["slug"]))}
        if method == "wiki_compile":
            return await self.consolidator.compile_wiki(self.second_brain)
        if method == "backup":
            target = self.settings.path("brain.backup_dir")
            return {"path": str(backup_sqlite(self.store.conn, target))}
        if method == "export_jsonl":
            target = self.settings.path("brain.jsonl_export_dir")
            return {"path": str(export_jsonl(self.store.conn, target))}
        if method == "import_jsonl":
            imports_dir = self.settings.path("brain.import_dir").resolve()
            source = Path(str(params["path"])).resolve()
            if imports_dir not in source.parents:
                raise PermissionError("Brain imports must come from the configured import directory")
            return self.store.import_memory_jsonl(source, str(params.get("source_label", source.name)))
        raise KeyError(f"Unknown brain method: {method}")

    async def _dream_loop(self) -> None:
        """Her sleep cycle: only when long idle, at most every few hours."""
        while True:
            await asyncio.sleep(float(self.settings.get("brain.dreaming.check_interval_seconds", 900)))
            try:
                if not bool(self.settings.get("brain.dreaming.enabled", True)):
                    continue
                idle_minutes = float(self.settings.get("brain.dreaming.idle_minutes", 30))
                row = self.store.conn.execute("SELECT MAX(created_at) FROM turns").fetchone()
                last_turn = str(row[0] or "")
                from datetime import UTC, datetime
                idle_seconds = float("inf")
                if last_turn:
                    idle_seconds = (
                        datetime.now(UTC) - datetime.fromisoformat(last_turn)
                    ).total_seconds()
                # Wiki compile runs on a much shorter leash than dreaming: a
                # few quiet minutes and enough uncompiled raw entries. This
                # is what makes the second brain self-updating within a
                # session instead of only overnight.
                if (
                    idle_seconds
                    >= float(self.settings.get("brain.second_brain.compile_idle_minutes", 5)) * 60
                    and self.second_brain.pending_count()
                    >= int(self.settings.get("brain.second_brain.compile_min_entries", 8))
                ):
                    result = await self.consolidator.compile_wiki(self.second_brain)
                    LOG.info("Wiki compile: %s", result)
                if idle_seconds < idle_minutes * 60:
                    continue
                now = datetime.now(UTC).timestamp()
                if now - getattr(self, "_last_dream_at", 0.0) < float(
                    self.settings.get("brain.dreaming.min_interval_seconds", 21600)
                ):
                    continue
                self._last_dream_at = now
                result = await self.consolidator.dream()
                LOG.info("Dream review: %s", result)
            except Exception:
                LOG.exception("Dream loop error")

    async def run(self) -> None:
        # Warm the embedding model at boot: the 613MB Qwen3-Embedding ONNX
        # session otherwise loads lazily on the first memory search, which
        # made "she takes forever to remember" literally true after every
        # restart. One throwaway encode moves that cost to startup.
        try:
            await asyncio.to_thread(self.store.embedding.encode, "startup warmup")
            LOG.info("Embedding model warmed at startup")
        except Exception:
            LOG.debug("Embedding warmup skipped", exc_info=True)
        dream_task = asyncio.create_task(self._dream_loop())
        dream_task.add_done_callback(lambda _t: None)
        await self.server.serve_forever()


class BrainClient:
    def __init__(self, settings: Settings):
        self.rpc = UnixJsonClient(settings.socket_path("brain"), timeout=15)

    async def health(self) -> dict[str, Any]:
        return await self.rpc.call("health")

    async def stats(self) -> dict[str, Any]:
        return await self.rpc.call("stats")

    async def dashboard_snapshot(self, limit: int = 20) -> dict[str, Any]:
        return await self.rpc.call("dashboard_snapshot", {"limit": limit})

    async def event(self, event_type: str, payload: dict[str, Any]) -> None:
        await self.rpc.call("event", {"event_type": event_type, "payload": payload})

    async def import_jsonl(self, path: Path, source_label: str) -> dict[str, Any]:
        return await self.rpc.call("import_jsonl", {"path": str(path), "source_label": source_label})

    async def context(
        self,
        query: str,
        *,
        limit: int | None = None,
        character_budget: int | None = None,
        include_self_model: bool = True,
        exclude_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "include_self_model": include_self_model}
        if exclude_kinds:
            params["exclude_kinds"] = exclude_kinds
        if limit is not None:
            params["limit"] = limit
        if character_budget is not None:
            params["character_budget"] = character_budget
        return await self.rpc.call("context", params)

    async def recent_turns(self, limit: int = 6, max_age_seconds: float = 900.0) -> list[dict[str, Any]]:
        return await self.rpc.call(
            "recent_turns", {"limit": limit, "max_age_seconds": max_age_seconds}
        )

    async def remember(self, **kwargs: Any) -> int:
        result = await self.rpc.call("remember", kwargs)
        return int(result["memory_id"])

    async def correct(self, memory_id: int, **kwargs: Any) -> int:
        result = await self.rpc.call("correct", {"memory_id": memory_id, **kwargs})
        return int(result["memory_id"])

    async def episode(self, user_text: str, kendra_text: str, session_id: str | None = None) -> int:
        result = await self.rpc.call(
            "episode",
            {"user_text": user_text, "kendra_text": kendra_text, "session_id": session_id},
        )
        return int(result["memory_id"])

    async def search(
        self, query: str, limit: int = 10, include_system: bool = False
    ) -> list[dict[str, Any]]:
        return await self.rpc.call(
            "search", {"query": query, "limit": limit, "include_system": include_system}
        )

    async def begin_session(self, session_id: str, context: str | None = None) -> None:
        await self.rpc.call("begin_session", {"session_id": session_id, "context": context})

    async def turn(self, session_id: str, user_text: str, kendra_text: str, metadata: dict[str, Any] | None = None) -> None:
        await self.rpc.call(
            "turn",
            {
                "session_id": session_id,
                "user_text": user_text,
                "kendra_text": kendra_text,
                "metadata": metadata or {},
            },
        )

    async def consolidate_turn(self, user_text: str, kendra_text: str, session_id: str | None = None) -> dict[str, Any]:
        return await self.rpc.call(
            "consolidate_turn",
            {"user_text": user_text, "kendra_text": kendra_text, "session_id": session_id},
        )


async def run_service(settings: Settings) -> None:
    service = BrainService(settings)
    try:
        await service.run()
    finally:
        service.store.close()


def run(settings: Settings) -> None:
    asyncio.run(run_service(settings))
