from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from ..connectivity import assert_loopback_http_url


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str):
        if tag == "a" and self._href is not None:
            text = " ".join(part.strip() for part in self._text if part.strip())
            self.links.append((self._href, text))
            self._href = None
            self._text = []


class KiwixClient:
    def __init__(self, base_url: str, book_name: str, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        assert_loopback_http_url(self.base_url)
        self.book_name = book_name
        self.timeout = timeout

    async def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{self.base_url}/search",
                params={"books.name": self.book_name, "pattern": query},
            )
            response.raise_for_status()
        parser = _LinkParser()
        parser.feed(response.text)
        found: list[dict[str, str]] = []
        seen: set[str] = set()
        for href, title in parser.links:
            absolute = urljoin(str(response.url), href)
            if absolute in seen or "/content/" not in absolute:
                continue
            seen.add(absolute)
            found.append({"title": title or absolute, "url": absolute})
            if len(found) >= limit:
                break
        return found

    async def read(self, url: str, max_chars: int = 12000) -> str:
        if not url.startswith(self.base_url):
            raise ValueError("Kiwix article must come from the configured local Kiwix server")
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
        # Lightweight HTML text fallback without adding another dependency.
        class TextParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []
            def handle_data(self, data: str):
                value = data.strip()
                if value:
                    self.parts.append(value)
        parser = TextParser()
        parser.feed(response.text)
        return "\n".join(parser.parts)[:max_chars]
