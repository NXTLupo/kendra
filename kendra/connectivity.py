from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from .config import Settings


def assert_loopback_host(host: str) -> None:
    """Reject any configured local-service host that can resolve off-machine."""

    value = host.strip().rstrip(".")
    if not value:
        raise ValueError("Local service host cannot be empty")
    try:
        addresses = [ipaddress.ip_address(value)]
    except ValueError:
        try:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
            ]
        except OSError as exc:
            raise ValueError(f"Local service host did not resolve: {value}") from exc
    if not addresses or any(not address.is_loopback for address in addresses):
        raise ValueError(f"Local service must use a loopback host, not {host!r}")


def assert_loopback_http_url(url: str) -> None:
    """Enforce Kendra's no-hosted-inference/local-service transport boundary."""

    parsed = urlparse(url)
    if parsed.scheme != "http":
        raise ValueError("Local service URL must use http on a loopback interface")
    if not parsed.hostname:
        raise ValueError("Local service URL has no hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Local service URL must not contain credentials")
    assert_loopback_host(parsed.hostname)


async def network_state(settings: Settings) -> str:
    if not bool(settings.get("connectivity.enabled", True)):
        return "unknown"
    host = str(settings.get("connectivity.probe_host", "1.1.1.1"))
    port = int(settings.get("connectivity.probe_port", 443))
    timeout = float(settings.get("connectivity.timeout_seconds", 1.0))

    def probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    return "online" if await asyncio.to_thread(probe) else "offline"
