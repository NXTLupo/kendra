from __future__ import annotations

import json
import logging
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

LOG = logging.getLogger(__name__)


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
        """Alive, and running the code it was started from.

        ``alive`` alone is what let the whole stack serve the previous
        evening's source for a day while every check reported health. ``code``
        is the other half of the question: current, stale, or unknown.
        """
        from .codestamp import service_report

        state = self._read_state()
        services = {}
        stale: list[str] = []
        for name, metadata in dict(state.get("services") or {}).items():
            pid = int(metadata.get("pid", 0))
            alive = self._alive(pid)
            report = (
                service_report(self.settings.runtime_dir, name, pid)
                if alive
                else {"state": "stopped", "changed": []}
            )
            if report["state"] == "stale":
                stale.append(name)
            services[name] = {
                **metadata,
                "alive": alive,
                "code": report["state"],
                "code_changed": [Path(f).name for f in report.get("changed", [])][:8],
            }
        return {
            "config": str(self.config_path),
            "state_file": str(self.state_path),
            "services": services,
            "stale_services": stale,
        }

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

        # Anything left over from a previous generation still owns its
        # socket, and the single-instance guard would refuse to start over it.
        self.reap_orphans()

        # A new stack is a new conversation. Her memories persist; the raw
        # transcript does not follow her across a restart.
        from .session import begin

        begin(self.settings.runtime_dir)

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
                # MICROPHONE PERMISSION IS INHERITED, AND setsid() BREAKS IT.
                #
                # `start_new_session=True` calls setsid(), which is what keeps
                # her services alive when the launching shell goes away. But on
                # macOS it also severs the TCC "responsible process" chain, so
                # the child inherits no microphone authorization -- and a
                # denied microphone does not raise. It opens successfully and
                # returns digital silence forever.
                #
                # That is why every input device read RMS 0 while the hardware
                # was perfectly healthy: input volume 88, built-in mic as the
                # system default, nothing muted. She was not deaf; she was
                # unauthorized, and it looked identical.
                #
                # Voice therefore stays a direct child of whatever started it
                # -- the desktop app, which holds the permission. It is the one
                # service that SHOULD die with the app rather than outlive it.
                detached = os.name != "nt" and service.name != "voice"
                process = subprocess.Popen(
                    command,
                    cwd=self.settings.root,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=detached,
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

    @staticmethod
    def _signal(pid: int, sig: int) -> None:
        """Signal a service whether or not it leads its own process group.

        This used to be an unconditional ``os.killpg``, which works only
        because every service got its own group from ``setsid``. Voice no
        longer does -- it stays attached so it inherits the desktop app's
        microphone permission -- so ``killpg`` targeted a group voice does not
        lead, silently failed, and left it running. The orphan kept
        ``voice.sock``, so every subsequent start died with "a live service
        already owns it" and Kendra came up unable to hear.
        """
        if os.name == "nt":
            os.kill(pid, sig)
            return
        try:
            leads_group = os.getpgid(pid) == pid
        except (ProcessLookupError, PermissionError):
            leads_group = False
        if leads_group:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)

    def _terminate(self, pid: int) -> None:
        if not self._alive(pid):
            return
        try:
            self._signal(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self._alive(pid):
                return
            time.sleep(0.1)
        try:
            self._signal(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def reap_orphans(self, names: list[str] | None = None) -> list[int]:
        """Kill stray service processes this stack has lost track of.

        ``stop()`` can only terminate pids recorded in its state file. A
        service that outlived a crashed state file, or was started by an
        earlier generation, is invisible to it -- and it still owns its
        socket. ``UnixJsonServer`` then refuses to start a duplicate (rightly:
        two voice services would fight over the microphone), so the service
        can never come back and Kendra comes up unable to hear.

        That is not hypothetical. A voice service orphaned this way held
        voice.sock through every restart, and each attempt failed with "a live
        service already owns it" while the supervisor tried again every
        fifteen seconds.

        Matching is on the exact command line, so nothing else on the machine
        can be caught by it.
        """
        import subprocess

        known = {
            int(item.get("pid", 0))
            for item in dict(self._read_state().get("services") or {}).values()
        }
        wanted = set(names or [s.name for s in CORE_SERVICES + OPTIONAL_SERVICES])
        reaped: list[int] = []
        try:
            listing = subprocess.run(
                ["ps", "-Ao", "pid=,args="], capture_output=True, text=True, timeout=5, check=False
            ).stdout
        except Exception:  # pragma: no cover - platform dependent
            return reaped
        marker = f"--config {self.config_path}"
        for line in listing.splitlines():
            if "-m kendra" not in line or " service " not in line or marker not in line:
                continue
            pid_text, _, rest = line.strip().partition(" ")
            if not pid_text.isdigit():
                continue
            pid = int(pid_text)
            if pid in known or pid == os.getpid():
                continue
            name = rest.rsplit(" service ", 1)[-1].split()[0]
            if name not in wanted:
                continue
            LOG.warning("Reaping an orphaned %s service (pid %d) that still holds its socket", name, pid)
            self._terminate(pid)
            reaped.append(pid)
        return reaped

    def stop(self) -> dict[str, Any]:
        state = self._read_state()
        items = list(dict(state.get("services") or {}).items())
        for _name, metadata in reversed(items):
            self._terminate(int(metadata.get("pid", 0)))
        self.reap_orphans()
        self._write_state({"services": {}})
        return self.status()
