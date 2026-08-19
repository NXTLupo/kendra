from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from html import unescape
from urllib.parse import urljoin, urlparse

import httpx


@dataclass(slots=True)
class FetchedDocument:
    url: str
    title: str
    text: str
    status_code: int


def _resolved_ips(hostname: str) -> list[ipaddress._BaseAddress]:
    values: list[ipaddress._BaseAddress] = []
    for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
        values.append(ipaddress.ip_address(result[4][0]))
    return values


def assert_public_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Research fetch supports only http/https")
    if not parsed.hostname:
        raise ValueError("URL has no hostname")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Localhost is blocked for model-directed research fetches")
    addresses = _resolved_ips(hostname)
    if not addresses:
        raise ValueError("Hostname did not resolve")
    for address in addresses:
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError(f"Blocked non-public research destination: {address}")


def _fallback_extract(html: str) -> tuple[str, str]:
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
    cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    text = unescape(re.sub(r"\s+", " ", cleaned)).strip()
    return title, text


def extract_document(html: str) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        return _fallback_extract(html)
    text = trafilatura.extract(
        html,
        include_links=False,
        include_images=False,
        include_tables=False,
        favor_precision=True,
    ) or ""
    title = ""
    metadata = trafilatura.extract_metadata(html)
    if metadata and metadata.title:
        title = metadata.title
    return title, text.strip()


class SafeFetcher:
    def __init__(self, *, timeout: float, max_bytes: int, user_agent: str):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml,text/plain"}

    async def fetch(self, url: str, max_redirects: int = 4) -> FetchedDocument:
        current = url
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=False, headers=self.headers) as client:
            for _ in range(max_redirects + 1):
                assert_public_http_url(current)
                async with client.stream("GET", current) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise RuntimeError("Redirect without Location header")
                        current = urljoin(current, location)
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not any(kind in content_type for kind in ("text/", "html", "xml", "json")):
                        raise ValueError(f"Unsupported research content type: {content_type}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise ValueError("Research document exceeded configured size limit")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    encoding = response.encoding or "utf-8"
                    html = raw.decode(encoding, errors="replace")
                    title, text = extract_document(html)
                    return FetchedDocument(
                        url=str(response.url),
                        title=title or str(response.url),
                        text=text,
                        status_code=response.status_code,
                    )
            raise RuntimeError("Too many redirects")
