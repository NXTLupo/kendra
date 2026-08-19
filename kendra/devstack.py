from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings


@dataclass(frozen=True, slots=True)
class ManagedService:
    name: str
    required: bool = True


CORE_SERVICES = [
    ManagedService("brain"),
    ManagedService("identity"),
    ManagedService("reflex"),
    ManagedService("body"),
    ManagedService("research"),
    ManagedService("vision"),
    ManagedService("leds"),
    ManagedService("delivery"),
    ManagedService("agent"),
]

OPTIONAL_SERVICES = [ManagedService("voice", required=False), ManagedService("autonomy", required=False)]


class DevStack:
    """Cross-platform local process manager for Virtual Kendra.

    This deliberately uses ordinary subprocesses instead of systemd so the same
    development workflow works on macOS, Windows, and Linux. Production Pi
    deployment uses the systemd units in ``systemd/``.
    """

    def __init__(self, settings: Settings, config_path: Path):
        self.settings = settings
        self.config_path = config_path.resolve()
        self.state_path = settings.runtime_dir / "devstack.json"
        self.log_dir = settings.path("paths.logs_dir") / "devstack"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.startup_timeout = float(settings.get("dev.startup_timeout_seconds", 8.0))

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"services": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"services": {}}
        except Exception:
            return {"services": {}}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_suffix(".tmp")
        temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temp, self.state_path)

    @staticmethod
    def _alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        services = {}
        for name, metadata in dict(state.get("services") or {}).items():
            pid = int(metadata.get("pid", 0))
            services[name] = {**metadata, "alive": self._alive(pid)}
        return {"config": str(self.config_path), "state_file": str(self.state_path), "services": services}

    def _service_socket(self, name: str) -> Path:
        if name in {"voice", "delivery"}:
            return self.settings.runtime_dir / f"{name}.sock"
        return self.settings.socket_path(name)

    def _wait_for_start(self, service: ManagedService, process: subprocess.Popen[bytes]) -> None:
        socket_path = self._service_socket(service.name)
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return
            if socket_path.exists():
                time.sleep(0.1)
                return
            time.sleep(0.05)

    def start(self, *, include_voice: bool = False, include_autonomy: bool = False) -> dict[str, Any]:
        current = self.status()
        running = [name for name, item in current["services"].items() if item.get("alive")]
        if running:
            raise RuntimeError("Virtual Kendra stack already has running services: " + ", ".join(sorted(running)))

        services = list(CORE_SERVICES)
        if include_voice:
            services.append(ManagedService("voice", required=False))
        if include_autonomy:
            services.append(ManagedService("autonomy", required=False))

        state: dict[str, Any] = {
            "started_at_epoch": time.time(),
            "config": str(self.config_path),
            "services": {},
        }
        started: list[subprocess.Popen[bytes]] = []
        try:
            for service in services:
                log_path = self.log_dir / f"{service.name}.log"
                log_handle = log_path.open("ab", buffering=0)
                command = [
                    sys.executable,
                    "-m",
                    "kendra",
                    "--config",
                    str(self.config_path),
                    "service",
                    service.name,
                ]
                process = subprocess.Popen(
                    command,
                    cwd=self.settings.root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=(os.name != "nt"),
                )
                log_handle.close()
                started.append(process)
                state["services"][service.name] = {
                    "pid": process.pid,
                    "log": str(log_path),
                    "required": service.required,
                    "command": command,
                }
                self._write_state(state)
                self._wait_for_start(service, process)
                if process.poll() is not None:
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(f"Service {service.name} exited during startup. Log tail:\n{tail}")
                if not self._service_socket(service.name).exists():
                    tail = log_path.read_text(encoding="utf-8", errors="replace")[-3000:]
                    raise RuntimeError(
                        f"Service {service.name} did not create its IPC socket within "
                        f"{self.startup_timeout:.1f}s. Log tail:\n{tail}"
                    )
            return self.status()
        except Exception:
            for process in reversed(started):
                if process.poll() is None:
                    self._terminate(process.pid)
            self._write_state({"services": {}})
            raise

    def _terminate(self, pid: int) -> None:
        if not self._alive(pid):
            return
        try:
            if os.name == "nt":
                os.kill(pid, signal.SIGTERM)
            else:
                os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self._alive(pid):
                return
            time.sleep(0.1)
        try:
            if os.name == "nt":
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def stop(self) -> dict[str, Any]:
        state = self._read_state()
        items = list(dict(state.get("services") or {}).items())
        for _name, metadata in reversed(items):
            self._terminate(int(metadata.get("pid", 0)))
        self._write_state({"services": {}})
        return self.status()
