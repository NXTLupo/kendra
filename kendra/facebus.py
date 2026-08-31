"""Her outward state, published the moment it changes.

Kendra's face had no connection to her voice. The desktop bridge is strictly
request/response and Electron discards any line without a matching request id,
so the renderer's only source of truth was a three-second poll of
``snapshot.brain.turns`` — a transcript written *after* a reply finishes. Her
mouth therefore animated speech that was already over, on a duration guessed at
thirteen characters per second, and two replies inside one poll window arrived
as one rushed burst.

This is the missing primitive: a one-way, fire-and-forget bus from her services
to whatever is drawing her. Publishers never block and never fail loudly —
losing an animation frame must never cost her a word.

The voice service already computes exactly the right events for the LED ring
(``_leds(thinking=True, thinking_mode="think")``) and, on the desktop, throws
them away because that driver is disabled. Now the same signal drives the
screen today and the WS2812 ring on the robot later, from one implementation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

SOCKET_NAME = "face.sock"

#: Events her face understands. Kept small and explicit — a renderer should
#: never have to guess what a name means.
EVENTS = (
    "listening",      # microphone open, she is being spoken to
    "thinking",       # working; data.mode is think | search | look
    "speech_start",   # audio playback has begun; data.text, data.seconds
    "speech_end",     # the utterance finished or was cut off
    "idle",           # nothing in progress
)


def socket_path(settings: Any) -> Path:
    return Path(settings.runtime_dir) / SOCKET_NAME


class FaceBusPublisher:
    """Fire-and-forget publisher. Never blocks a caller, never raises."""

    def __init__(self, settings: Any):
        self._path = socket_path(settings)
        self._tasks: set[asyncio.Task[None]] = set()

    async def _send(self, payload: dict[str, Any]) -> None:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self._path)), timeout=0.5
            )
        except (OSError, TimeoutError):
            return  # nobody is drawing her right now; that is fine
        try:
            writer.write((json.dumps(payload, default=str) + "\n").encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=0.5)
        except Exception:
            LOG.debug("Face event dropped", exc_info=True)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def publish(self, event: str, **data: Any) -> None:
        """Schedule an event. Returns immediately.

        Safe to call from any coroutine. Outside a running loop it is a no-op
        rather than an error, so a synchronous code path can call it freely.
        """
        payload = {"event": str(event), "at": time.time(), "data": data}
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._send(payload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def publish_threadsafe(self, loop: asyncio.AbstractEventLoop, event: str, **data: Any) -> None:
        """Publish from a worker thread — audio playback runs in one."""
        payload = {"event": str(event), "at": time.time(), "data": data}
        try:
            asyncio.run_coroutine_threadsafe(self._send(payload), loop)
        except Exception:
            LOG.debug("Face event dropped from thread", exc_info=True)


class FaceBusServer:
    """Accepts fire-and-forget event lines and hands each to ``on_event``.

    Deliberately not the project's ``UnixJsonServer``: that answers one request
    per connection and refuses to start if the path exists, both of which are
    wrong here. This is a sink, and a stale socket from a crashed desktop app
    must never stop her face from working again.
    """

    def __init__(self, path: Path, on_event: Callable[[dict[str, Any]], Awaitable[None]]):
        self.path = Path(path)
        self.on_event = on_event
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self._client, path=str(self.path))
        with contextlib.suppress(OSError):
            os.chmod(self.path, 0o660)

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("event"):
                    await self.on_event(payload)
        except Exception:
            LOG.debug("Face event connection ended", exc_info=True)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
