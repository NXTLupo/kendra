from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from ..connectivity import assert_loopback_http_url


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str | None = None


class SearXNGClient:
    def __init__(self, base_url: str, timeout: float = 12.0):
        self.base_url = base_url.rstrip("/")
        assert_loopback_http_url(self.base_url)
        self.timeout = timeout

    async def search(self, query: str, limit: int = 8, category: str | None = None) -> list[SearchResult]:
        params: dict[str, str | int] = {"q": query, "format": "json", "safesearch": 1}
        if category:
            # SearXNG's news category returns articles whose TITLES are real
            # headlines — general search returns outlet homepages whose
            # snippets contain none, which a small model then invents around.
            params["categories"] = category
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            data: dict[str, Any] = response.json()
        results: list[SearchResult] = []
        for item in data.get("results", [])[:limit]:
            url = str(item.get("url") or "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or url),
                    url=url,
                    snippet=str(item.get("content") or ""),
                    engine=str(item.get("engine")) if item.get("engine") else None,
                )
            )
        return results
