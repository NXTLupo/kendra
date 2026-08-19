from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from .service import BrainClient

HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


class BrainSyncClient:
    """Pull a Kendra Brain export over authenticated SSH on the local network."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.import_dir = settings.path("brain.import_dir")
        self.import_dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = int(settings.get("brain.sync.max_bytes", 25 * 1024 * 1024))

    def _target(self, host: str | None, user: str | None) -> str:
        host = (host or str(self.settings.get("brain.sync.ssh_host") or "")).strip()
        user = (user or str(self.settings.get("brain.sync.ssh_user") or "kendra")).strip()
        if not HOST_RE.fullmatch(host) or not USER_RE.fullmatch(user):
            raise ValueError("Wi-Fi sync requires a simple trusted hostname and SSH username")
        return f"{user}@{host}"

    def _ssh_command(self, host: str | None, user: str | None) -> list[str]:
        ssh = shutil.which("ssh")
        if not ssh:
            raise RuntimeError("OpenSSH is required for encrypted Wi-Fi brain sync")
        command = [
            ssh,
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
        ]
        identity = self.settings.get("brain.sync.ssh_identity_file")
        if identity:
            identity_path = Path(str(identity)).expanduser().resolve()
            if not identity_path.is_file():
                raise FileNotFoundError(f"SSH identity file does not exist: {identity_path}")
            if identity_path.stat().st_mode & 0o077:
                raise PermissionError("SSH identity file must not be accessible by group or other users")
            command += ["-i", str(identity_path)]
        command += [
            self._target(host, user),
            "/opt/kendra/current/.venv/bin/kendra",
            "--config",
            "/etc/kendra/production.yaml",
            "brain",
            "export-jsonl",
            "--stdout",
        ]
        return command

    async def pull(self, host: str | None = None, user: str | None = None) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *self._ssh_command(host, user),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError("Wi-Fi brain sync timed out") from None
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-1_000:].strip()
            raise RuntimeError(f"Encrypted Wi-Fi brain sync failed: {detail or 'SSH exited unsuccessfully'}")
        if not stdout or len(stdout) > self.max_bytes:
            raise RuntimeError("Wi-Fi brain export is empty or exceeds the configured safety limit")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = self.import_dir / f"wifi-kendra-brain-{stamp}.jsonl"
        temporary = destination.with_suffix(".part")
        temporary.write_bytes(stdout)
        os.replace(temporary, destination)
        result = await BrainClient(self.settings).import_jsonl(destination, f"wifi:{self._target(host, user)}")
        return {**result, "transport": "encrypted-ssh", "archive": str(destination)}
