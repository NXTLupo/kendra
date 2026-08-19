from __future__ import annotations

import asyncio
import base64
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from ..brain.service import BrainClient
from ..config import Settings
from ..ipc import UnixJsonClient
from ..llm import LlamaCppClient
from ..updates.git import GitUpdateInspector

FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class DashboardController:
    """Read-only aggregation and validated local operations for the desktop UI.

    The controller has no HTTP server. The Electron main process reaches it
    through a private stdio bridge and exposes only named IPC commands to the
    sandboxed renderer.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.upload_limit = int(settings.get("dashboard.memory_upload_max_bytes", 25 * 1024 * 1024))
        # A 2s probe made healthy services flicker as "unavailable" whenever they
        # were mid-inference on Intel CPU. Poll patiently; the UI would rather
        # wait than report a false outage.
        self.health_timeout = float(settings.get("dashboard.health_timeout_seconds", 6.0))

    async def _rpc_health(self, name: str, socket_path: Path) -> tuple[str, dict[str, Any]]:
        try:
            result = await UnixJsonClient(socket_path, timeout=self.health_timeout).call("health")
            return name, {"ok": bool(result.get("ok", True)), "detail": result}
        except Exception as exc:
            return name, {"ok": False, "error": type(exc).__name__}

    async def record_event(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            await BrainClient(self.settings).event(event_type, payload)
        except Exception:
            # UI observability must never turn a valid local action into a failure.
            pass

    async def snapshot(self) -> dict[str, Any]:
        sockets = {
            "brain": self.settings.socket_path("brain"),
            "identity": self.settings.socket_path("identity"),
            "reflex": self.settings.socket_path("reflex"),
            "body": self.settings.socket_path("body"),
            "research": self.settings.socket_path("research"),
            "vision": self.settings.socket_path("vision"),
            "leds": self.settings.socket_path("leds"),
            "delivery": self.settings.runtime_dir / "delivery.sock",
            "agent": self.settings.socket_path("agent"),
            "voice": self.settings.runtime_dir / "voice.sock",
        }
        health_pairs = await asyncio.gather(
            *(self._rpc_health(name, path) for name, path in sockets.items())
        )
        services = dict(health_pairs)
        try:
            body = await UnixJsonClient(
                self.settings.socket_path("body"), timeout=self.health_timeout
            ).call("observation")
        except Exception as exc:
            body = {"body_state": "unavailable", "reflex_lock": True, "error": type(exc).__name__}
        try:
            brain = await BrainClient(self.settings).dashboard_snapshot(limit=24)
        except Exception as exc:
            brain = {"stats": {}, "turns": [], "events": [], "memories": [], "error": type(exc).__name__}
        llm_ok = await LlamaCppClient(self.settings).health()
        vlm_ok = False
        vlm_url = self.settings.get("vision.semantic_vlm_url")
        if vlm_url:
            try:
                base = str(vlm_url).rstrip("/").removesuffix("/v1")
                async with httpx.AsyncClient(timeout=2) as client:
                    response = await client.get(f"{base}/health")
                    vlm_ok = 200 <= response.status_code < 300
            except httpx.HTTPError:
                pass
        try:
            git_state = await asyncio.to_thread(GitUpdateInspector(self.settings).status)
        except Exception as exc:
            git_state = {"error": type(exc).__name__, "upgrade_available": False}
        return {
            "generated_at": time.time(),
            "interaction_mode": str(self.settings.get("project.interaction_mode", "voice_first")),
            "profile": {
                "mode": self.settings.get("project.mode"),
                "body_driver": self.settings.get("body.driver"),
                "body_name": self.settings.get("body.capabilities.body_name"),
                "runtime_dir": str(self.settings.runtime_dir),
                "webots": self.settings.get("body.driver") == "webots",
            },
            "services": services,
            "models": {"llm": llm_ok, "vlm": vlm_ok},
            "body": body,
            "brain": brain,
            "git": git_state,
            "latest_photo": self.latest_photo(),
            "healthy_services": sum(1 for value in services.values() if value.get("ok")),
        }

    def latest_photo(self) -> dict[str, Any] | None:
        photos_dir = self.settings.path("paths.photos_dir")
        candidates = sorted(
            (path for path in photos_dir.glob("kendra-*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"}),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return None
        path = candidates[0]
        return {"name": path.name, "modified": path.stat().st_mtime}

    def photo_path(self, name: str) -> Path:
        if Path(name).name != name:
            raise PermissionError("Invalid photo name")
        photos_dir = self.settings.path("paths.photos_dir").resolve()
        path = (photos_dir / name).resolve()
        if photos_dir not in path.parents or path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
            raise PermissionError("Photo is outside Kendra's local photo directory")
        if not path.is_file():
            raise FileNotFoundError(name)
        return path

    def photo_data_url(self, name: str) -> str:
        path = self.photo_path(name)
        if path.stat().st_size > 15 * 1024 * 1024:
            raise ValueError("Local camera frame is unexpectedly large")
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

    async def import_upload(self, body: bytes, filename: str) -> dict[str, Any]:
        if not body or len(body) > self.upload_limit:
            raise ValueError("Brain transfer is empty or exceeds the configured safety limit")
        clean = FILENAME_RE.sub("-", Path(filename).name)[:120]
        if not clean.endswith(".jsonl"):
            raise ValueError("Choose a Kendra Brain .jsonl export")
        imports_dir = self.settings.path("brain.import_dir")
        imports_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = imports_dir / f"usb-{stamp}-{clean}"
        temporary = destination.with_suffix(".part")
        temporary.write_bytes(body)
        os.replace(temporary, destination)
        result = await BrainClient(self.settings).import_jsonl(destination, f"usb:{clean}")
        return {**result, "transport": "usb-or-file", "archive": str(destination)}
