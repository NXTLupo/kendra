from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from .fetch import SafeFetcher
from .kiwix import KiwixClient
from .searxng import SearXNGClient
from .websearch import DuckDuckGoLiteClient


class ResearchService:
    def __init__(self, settings: Settings):
        self.settings = settings
        timeout = float(settings.get("research.request_timeout_seconds", 12))
        # Native search is PRIMARY: no Docker on any body (the robot carries
        # none), one pooled HTTPS request, measured ~0.85s vs SearXNG's 2-6s.
        # SearXNG remains an optional aggregator when its URL is configured
        # and the container happens to be up.
        self.web = DuckDuckGoLiteClient(timeout=min(timeout, 6.0))
        searxng_url = settings.get("research.searxng_url")
        self.search = SearXNGClient(str(searxng_url), timeout) if searxng_url else None
        self.kiwix = KiwixClient(
            str(settings.require("research.kiwix_url")),
            str(settings.require("research.kiwix_book")),
            timeout,
        )
        self.fetcher = SafeFetcher(
            timeout=timeout,
            max_bytes=int(settings.get("research.max_download_bytes", 2500000)),
            user_agent=str(settings.get("research.user_agent", "KendraResearch/0.1")),
        )
        self.server = UnixJsonServer(settings.socket_path("research"), self.handle)

    _COMMAND_PREFIX = re.compile(
        r"^(?:please\s+)?(?:can you\s+|could you\s+)?(?:go\s+)?(?:on(?:line)?\s+)?(?:and\s+)?"
        r"(?:research|look up|look it up|find out|find|search(?: for| the)?|check|give me|tell me|get me)\s+"
        r"(?:me\s+)?(?:the\s+)?",
        re.I,
    )

    def _clean_query(self, query: str) -> str:
        """Search engines want topics, not commands.

        "Research the top five news headlines for today." as a literal query
        returns pages ABOUT researching news — outlet homepages with
        headline-free snippets, which a small model then confabulates around.
        """
        cleaned = self._COMMAND_PREFIX.sub("", query.strip()).strip(" .!?")
        return cleaned if len(cleaned) >= 3 else query.strip(" .!?")

    async def deep_evidence(self, query: str) -> dict[str, Any]:
        """Always fetch full pages, never settle for snippets.

        Used when a snippet-based answer failed its grounding check: search
        snippets vary between calls, and a truncated intro often omits the
        very date or name that was asked for. Rather than refuse a
        well-known fact, go and read the page.
        """
        return await self.online_evidence(query, force_pages=True)

    async def online_evidence(self, query: str, force_pages: bool = False) -> dict[str, Any]:
        max_results = int(self.settings.get("research.max_results", 5))
        max_pages = int(self.settings.get("research.max_pages", 2))
        per_source_chars = int(self.settings.get("research.per_source_chars", 2500))
        query = self._clean_query(query)
        category = "news" if re.search(r"\b(news|headline|headlines)\b", query, re.I) else None
        try:
            results = await self.web.search(query, max_results, category=category)
        except Exception:
            results = []
        if not results and self.search is not None:
            # Optional aggregator fallback — only when the container exists.
            try:
                results = await self.search.search(query, max_results, category=category)
            except Exception:
                results = []

        # Snippets-first (edge search-context discipline): search snippets are
        # already clean, high-density text. A small model answers most factual
        # queries from the top snippets alone, so return them immediately and
        # only fetch full pages when snippets look thin. Feeding a 2B model
        # raw page dumps chokes its prefill without making it smarter.
        snippets = [
            {
                "id": f"N{index + 1}",
                "title": item.title,
                "url": item.url,
                "snippet": str(item.snippet or "")[:400],
            }
            for index, item in enumerate(results[:3])
            if item.snippet
        ]
        snippet_density = sum(len(s["snippet"]) for s in snippets)
        # Volume is not the same as an answer. 891 chars of Wikipedia intro
        # about the Eiffel Tower contained no year and no architect, so she
        # answered from memory and her grounding check (rightly) refused it.
        # If the question asks for a specific KIND of fact, the snippets must
        # actually contain that kind of token or the pages get fetched.
        blob = " ".join(s["snippet"] for s in snippets)
        lowered = query.casefold()
        needs: list[bool] = []
        if re.search(r"\b(when|what year|how old|founded|built|created|born|died|started)\b", lowered):
            needs.append(bool(re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", blob)))
        if re.search(r"\b(who|whom|founder|designed|invented|wrote|directed|ceo)\b", lowered):
            needs.append(bool(re.search(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b", blob)))
        if re.search(r"\b(how many|how much|how tall|how far|how long|price|cost)\b", lowered):
            needs.append(bool(re.search(r"\d", blob)))
        specifics_present = all(needs) if needs else True
        if not force_pages \
                and snippet_density >= int(self.settings.get("research.snippets_sufficient_chars", 400)) \
                and specifics_present:
            return {"mode": "online-snippets", "query": query, "sources": snippets}

        # Fetch candidate pages concurrently instead of serially: page fetches
        # dominated research latency (up to 12s each, one after another).
        async def fetch_one(item):
            try:
                document = await self.fetcher.fetch(item.url)
                return item, document
            except Exception:
                return item, None

        candidates = results[: max_pages * 2]
        fetched = await asyncio.gather(*(fetch_one(item) for item in candidates))
        sources: list[dict[str, Any]] = []
        for item, document in fetched:
            if len(sources) >= max_pages:
                break
            if document is None or not document.text:
                continue
            sources.append(
                {
                    "id": f"S{len(sources) + 1}",
                    "title": document.title or item.title,
                    "url": document.url,
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "text": document.text[:per_source_chars],
                    "search_snippet": item.snippet,
                }
            )
        return {"mode": "online", "query": query, "sources": sources or snippets}

    async def offline_evidence(self, query: str) -> dict[str, Any]:
        results = await self.kiwix.search(query, limit=4)
        sources: list[dict[str, Any]] = []
        for item in results[:3]:
            try:
                text = await self.kiwix.read(item["url"])
            except Exception:
                continue
            sources.append(
                {
                    "id": f"K{len(sources) + 1}",
                    "title": item["title"],
                    "url": item["url"],
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "archive": self.kiwix.book_name,
                    "text": text,
                }
            )
        return {"mode": "offline", "query": query, "sources": sources}

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True}
        if method == "deep":
            return await self.deep_evidence(str(params["query"]))
        if method == "online":
            return await self.online_evidence(str(params["query"]))
        if method == "offline":
            return await self.offline_evidence(str(params["query"]))
        if method == "auto":
            query = str(params["query"])
            try:
                evidence = await self.online_evidence(query)
                if evidence["sources"]:
                    return evidence
            except Exception:
                pass
            return await self.offline_evidence(query)
        raise KeyError(f"Unknown research method: {method}")

    async def run(self) -> None:
        await self.server.serve_forever()


class ResearchClient:
    def __init__(self, settings: Settings):
        self.rpc = UnixJsonClient(settings.socket_path("research"), timeout=45)

    async def evidence(self, query: str, mode: str = "auto") -> dict[str, Any]:
        if mode not in {"auto", "online", "offline"}:
            raise ValueError("mode must be auto, online, or offline")
        return await self.rpc.call(mode, {"query": query})


def run(settings: Settings) -> None:
    asyncio.run(ResearchService(settings).run())
