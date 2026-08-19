"""Native web search: one tiny HTTPS request, no Docker, no daemon.

DuckDuckGo's lite endpoint returns ~15KB of plain HTML for a query — a
single GET parsed with the stdlib, typically well under a second on a warm
connection. This is Kendra's PRIMARY search on every body: the identical
pure-Python path on the Intel iMac and the Raspberry Pi, alive the moment
the network is, with nothing to install and nothing to babysit.

The old SearXNG container becomes an optional aggregator: measured, its
multi-engine fan-out took 2-6s behind a Docker VM that burned more than a
core just existing — and the robot will not carry Docker at all.

A pooled AsyncClient is kept for the service lifetime so repeat searches
skip DNS + TLS handshakes ("make it super fast" — Jonathan).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx

LITE_URL = "https://lite.duckduckgo.com/lite/"

# lite.duckduckgo.com result rows: an anchor with the target URL wrapped in
# a redirect, followed by a snippet cell. Parsed with regex on purpose —
# the page is table-markup so simple that a parser dependency isn't earned.
_ANCHOR = re.compile(
    r"<a[^>]+href=\"(?P<href>[^\"]+)\"[^>]*class=['\"]result-link['\"][^>]*>(?P<title>.*?)</a>",
    re.S,
)
_SNIPPET = re.compile(r"<td class=['\"]result-snippet['\"]>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str | None = "duckduckgo"


def _clean(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment)).strip()


class DuckDuckGoLiteClient:
    def __init__(self, timeout: float = 6.0, user_agent: str = "Mozilla/5.0 (KendraResearch)"):
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent},
            follow_redirects=True,
        )

    async def search(self, query: str, limit: int = 8, category: str | None = None) -> list[SearchResult]:
        # DDG lite has no news vertical; freshness-shaped queries get a
        # plain-language nudge instead, which its ranker honors well.
        q = f"{query} latest news" if category == "news" and "news" not in query.lower() else query
        response = await self._client.post(LITE_URL, data={"q": q})
        response.raise_for_status()
        page = response.text
        titles = list(_ANCHOR.finditer(page))
        snippets = [_clean(m.group(1)) for m in _SNIPPET.finditer(page)]
        results: list[SearchResult] = []
        import urllib.parse

        for index, match in enumerate(titles[:limit]):
            url = match.group("href")
            if "uddg=" in url:
                url = urllib.parse.unquote(url.split("uddg=", 1)[1].split("&", 1)[0])
            if not url.startswith("http"):
                continue
            results.append(
                SearchResult(
                    title=_clean(match.group("title")) or url,
                    url=url,
                    snippet=snippets[index] if index < len(snippets) else "",
                )
            )
        return results

    async def aclose(self) -> None:
        await self._client.aclose()
