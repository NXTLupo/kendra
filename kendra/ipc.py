from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .protocol import RpcRequest, RpcResponse

LOG = logging.getLogger(__name__)

RpcHandler = Callable[[str, dict[str, Any]], Awaitable[Any]]


class UnixJsonClient:
    def __init__(self, socket_path: Path, timeout: float = 10.0):
        self.socket_path = socket_path
        self.timeout = timeout

    async def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        request = RpcRequest(id=uuid.uuid4().hex, method=method, params=params or {})
        reader, writer = await asyncio.wait_for(
            # 16 MiB line limit: camera frames and brain transfers travel as
            # single base64 JSON lines, far beyond asyncio's 64 KiB default.
            asyncio.open_unix_connection(str(self.socket_path), limit=16 * 1024 * 1024),
            timeout=self.timeout,
        )
        try:
            writer.write((request.model_dump_json() + "\n").encode("utf-8"))
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
            if not line:
                raise ConnectionError(f"No response from {self.socket_path}")
            response = RpcResponse.model_validate_json(line)
            if response.id != request.id:
                raise RuntimeError("Mismatched RPC response id")
            if not response.ok:
                raise RuntimeError(response.error or "Unknown RPC error")
            return response.result
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


class UnixJsonServer:
    def __init__(self, socket_path: Path, handler: RpcHandler):
        self.socket_path = socket_path
        self.handler = handler
        self.server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            # Single-instance enforcement: if a live server already answers
            # on this socket, REFUSE to start instead of silently stealing
            # the path. Stolen sockets left orphaned duplicates running —
            # twice they fought over the microphone and Kendra went deaf.
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_unix_connection(str(self.socket_path)), timeout=1.5
                )
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                raise RuntimeError(
                    f"A live service already owns {self.socket_path}; refusing to start "
                    "a duplicate. Stop the running instance first."
                )
            except (ConnectionRefusedError, FileNotFoundError, TimeoutError):
                pass  # stale socket from a dead process — safe to reclaim
            self.socket_path.unlink()
        self.server = await asyncio.start_unix_server(
            self._client, path=str(self.socket_path), limit=16 * 1024 * 1024
        )
        os.chmod(self.socket_path, 0o660)

    async def serve_forever(self) -> None:
        if self.server is None:
            await self.start()
        assert self.server is not None
        async with self.server:
            await self.server.serve_forever()

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        if self.socket_path.exists():
            self.socket_path.unlink()

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if not line:
                return
            request = RpcRequest.model_validate_json(line)
            try:
                result = await self.handler(request.method, request.params)
                response = RpcResponse(id=request.id, ok=True, result=result)
            except Exception as exc:
                response = RpcResponse(id=request.id, ok=False, error=f"{type(exc).__name__}: {exc}")
            writer.write((response.model_dump_json() + "\n").encode("utf-8"))
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, ConnectionError):
                # The caller gave up before we answered — normal whenever a UI
                # health poll times out against a service that is busy doing
                # local inference. It is not a service fault and must not be
                # logged as an unhandled callback exception.
                LOG.debug("Client disconnected before %s response was delivered", request.method)
        except Exception:
            LOG.exception("Unhandled error while serving %s", self.socket_path.name)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
