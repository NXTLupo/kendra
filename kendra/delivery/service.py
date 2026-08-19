from __future__ import annotations

import asyncio
import hashlib
import json
import smtplib
import subprocess
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..config import Settings
from ..ipc import UnixJsonServer


class DeliveryService:
    """Whitelisted photo exfiltration boundary.

    Literal addresses and credentials are resolved only from local config. The
    planner can provide only an alias, photo id, and note.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.photos_dir = settings.path("paths.photos_dir").resolve()
        self.outbox = settings.path("paths.outbox_dir")
        self.outbox.mkdir(parents=True, exist_ok=True)
        self.aliases = self._load_aliases()
        self.server = UnixJsonServer(settings.runtime_dir / "delivery.sock", self.handle)

    def _load_aliases(self) -> dict[str, dict[str, str]]:
        path = self.settings.root / "config" / "recipients.local.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("recipients.local.json must contain an object keyed by alias")
        return data

    def _resolve_photo(self, photo_id: str) -> Path:
        matches = list(self.photos_dir.glob(f"{photo_id}.jpg"))
        if not matches:
            raise FileNotFoundError(f"Unknown photo id: {photo_id}")
        path = matches[0].resolve()
        if path.parent != self.photos_dir:
            raise PermissionError("Photo escaped Kendra photos directory")
        return path

    def _queue(self, alias: str, photo_path: Path, note: str, error: str) -> dict[str, Any]:
        payload = {
            "alias": alias,
            "photo_path": str(photo_path),
            "note": note,
            "queued_at": datetime.now(UTC).isoformat(),
            "last_error": error,
        }
        name = f"{int(datetime.now(UTC).timestamp())}-{hashlib.sha256(str(photo_path).encode()).hexdigest()[:10]}.json"
        path = self.outbox / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return {"ok": False, "queued": True, "outbox_item": path.name, "error": error}

    def deliver(self, alias: str, photo_path: Path, note: str = "", *, queue_on_failure: bool = True) -> dict[str, Any]:
        entry = self.aliases.get(alias)
        if not entry:
            raise PermissionError(f"Unknown delivery alias: {alias}")
        if not photo_path.exists() or photo_path.resolve().parent != self.photos_dir:
            raise PermissionError("Photo delivery only accepts files from Kendra's photos directory")
        try:
            channel = entry.get("channel")
            if channel == "signal":
                result = self._signal(entry, photo_path, note)
            elif channel == "smtp":
                result = self._smtp(entry, photo_path, note)
            else:
                raise ValueError(f"Unsupported delivery channel: {channel}")
            return {"ok": True, "alias": alias, **result}
        except Exception as exc:
            if not queue_on_failure:
                raise
            return self._queue(alias, photo_path, note, f"{type(exc).__name__}: {exc}")

    def _signal(self, entry: dict[str, str], photo_path: Path, note: str) -> dict[str, Any]:
        command = [
            "signal-cli", "-a", entry["account"], "send",
            "-m", note or "Photo from Kendra",
            "-a", str(photo_path),
            entry["target"],
        ]
        process = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
        if process.returncode != 0:
            raise RuntimeError(f"signal-cli failed: {process.stderr[-1000:]}")
        return {"channel": "signal", "sha256": hashlib.sha256(photo_path.read_bytes()).hexdigest()}

    def _smtp(self, entry: dict[str, str], photo_path: Path, note: str) -> dict[str, Any]:
        secret_path = Path(entry["password_file"]).expanduser()
        password = secret_path.read_text(encoding="utf-8").strip()
        message = EmailMessage()
        message["From"] = entry["from"]
        message["To"] = entry["target"]
        message["Subject"] = entry.get("subject", "Photo from Kendra")
        message.set_content(note or "Kendra sent a photo.")
        message.add_attachment(photo_path.read_bytes(), maintype="image", subtype="jpeg", filename=photo_path.name)
        with smtplib.SMTP(entry["host"], int(entry.get("port", 587)), timeout=30) as smtp:
            smtp.starttls()
            smtp.login(entry["username"], password)
            smtp.send_message(message)
        return {"channel": "smtp", "sha256": hashlib.sha256(photo_path.read_bytes()).hexdigest()}

    def flush(self) -> dict[str, Any]:
        sent = 0
        failed = 0
        for item in sorted(self.outbox.glob("*.json")):
            try:
                payload = json.loads(item.read_text(encoding="utf-8"))
                path = Path(payload["photo_path"]).resolve()
                result = self.deliver(payload["alias"], path, payload.get("note", ""), queue_on_failure=False)
                if result.get("ok"):
                    item.unlink()
                    sent += 1
            except Exception:
                failed += 1
        return {"sent": sent, "failed": failed, "remaining": len(list(self.outbox.glob("*.json")))}

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {"ok": True, "aliases": sorted(self.aliases), "queued": len(list(self.outbox.glob("*.json")))}
        if method == "deliver_photo":
            path = self._resolve_photo(str(params["photo_id"]))
            return await asyncio.to_thread(self.deliver, str(params["recipient_alias"]), path, str(params.get("note", "")))
        if method == "flush":
            return await asyncio.to_thread(self.flush)
        raise KeyError(f"Unknown delivery method: {method}")

    async def run(self) -> None:
        await self.server.serve_forever()


def run(settings: Settings) -> None:
    asyncio.run(DeliveryService(settings).run())
