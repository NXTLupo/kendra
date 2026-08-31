"""A service must not be able to go on running code that no longer exists.

This cost an entire day. Kendra's stack was healthy by every check she had --
ten services alive, every socket answering, every port responding -- while all
ten ran source from the previous evening. Nothing errored. Every fix simply had
no effect, which is indistinguishable from a fix that did not work, so the same
bugs were re-diagnosed and re-fixed against code that was never loaded.

Liveness answers "is it running". This answers "is it running *this*".

HOW IT KNOWS WHICH FILES MATTER. Not by scanning the package: the vision
service does not care that the voice service changed, and flagging all ten
whenever any file moves is a smoke alarm that goes off when you make toast --
it gets ignored, which is worse than silence. Instead each service asks
``sys.modules`` what it actually imported, after it has imported it. That set
is exact, it is free, and it is the real answer to "what am I running".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

#: A service that exits with this code was not broken -- its source moved.
#: The supervisor restarts it rather than reporting a crash.
STALE_EXIT_CODE = 75


def loaded_sources() -> dict[str, float]:
    """Every file of Kendra's own code this process has actually imported.

    Third-party modules are excluded deliberately: their files do not change
    while she runs, and hashing site-packages would make this slow enough to
    skip.
    """
    root = Path(__file__).resolve().parent
    found: dict[str, float] = {}
    for module in list(sys.modules.values()):
        origin = getattr(getattr(module, "__spec__", None), "origin", None)
        if not origin or not origin.endswith(".py"):
            continue
        path = Path(origin)
        try:
            path = path.resolve()
        except OSError:
            continue
        if root not in path.parents and path != root:
            continue
        try:
            found[str(path)] = path.stat().st_mtime
        except OSError:
            continue
    return found


def fingerprint(sources: dict[str, float] | None = None) -> str:
    """One short string for a set of files and their modification times."""
    items = sorted((sources if sources is not None else loaded_sources()).items())
    digest = hashlib.blake2b(digest_size=12)
    for path, mtime in items:
        digest.update(path.encode("utf-8"))
        # Whole milliseconds: a filesystem with coarse timestamps must not
        # make this flap, and nothing edits a file twice in one millisecond.
        digest.update(f"{mtime:.3f}".encode("ascii"))
    return digest.hexdigest()


def stamp_path(runtime_dir: Path | str, name: str) -> Path:
    return Path(runtime_dir) / "codestamp" / f"{name}.json"


def write_stamp(runtime_dir: Path | str, name: str, config: Path | str | None = None) -> dict[str, Any]:
    """Record what this service is running, at the moment it starts.

    The active config file counts as source. Her Slot 0 text, her model
    expectations, her thresholds and her device selection all live in YAML,
    and a service reads them once at construction — so editing a config had
    exactly the same silent-staleness failure as editing code, and the
    watchdog could not see it.
    """
    sources = loaded_sources()
    if config:
        try:
            path = Path(config).resolve()
            sources[str(path)] = path.stat().st_mtime
        except OSError:
            pass
    stamp = {
        "service": name,
        "pid": os.getpid(),
        "started_at": time.time(),
        "fingerprint": fingerprint(sources),
        "files": len(sources),
        "sources": sources,
    }
    path = stamp_path(runtime_dir, name)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stamp), encoding="utf-8")
    except OSError:
        LOG.debug("Could not record the code stamp for %s", name, exc_info=True)
    return stamp


def read_stamp(runtime_dir: Path | str, name: str) -> dict[str, Any] | None:
    try:
        return json.loads(stamp_path(runtime_dir, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def changed_files(stamp: dict[str, Any]) -> list[str]:
    """Which of the recorded files have moved since the stamp was taken."""
    moved: list[str] = []
    for path, recorded in (stamp.get("sources") or {}).items():
        try:
            current = Path(path).stat().st_mtime
        except OSError:
            moved.append(path)      # deleted or renamed counts as changed
            continue
        if abs(current - float(recorded)) > 0.001:
            moved.append(path)
    return moved


def service_report(runtime_dir: Path | str, name: str, pid: int | None = None) -> dict[str, Any]:
    """What this running service is running, and whether it still matches.

    ``state`` is one of:

      current   -- every file it loaded is unchanged
      stale     -- some have changed; it should have exited already
      unknown   -- it never recorded a stamp, or the stamp belongs to a
                   different process

    "unknown" is NOT "fine". A service that cannot say what it loaded is
    exactly the situation this module exists to end: the whole stack ran the
    previous evening's code for a day while every check reported health.
    """
    stamp = read_stamp(runtime_dir, name)
    if not stamp:
        return {
            "state": "unknown",
            "why": "it recorded no code stamp — started before this check existed, "
                   "or by something that bypasses `kendra service`",
            "changed": [],
        }
    if pid is not None and int(stamp.get("pid", -1)) != int(pid):
        return {
            "state": "unknown",
            "why": f"the stamp belongs to pid {stamp.get('pid')}, but pid {pid} is running",
            "changed": [],
        }
    moved = changed_files(stamp)
    return {
        "state": "stale" if moved else "current",
        "why": "" if not moved else f"{len(moved)} of its source files changed since it started",
        "changed": moved,
        "fingerprint": stamp.get("fingerprint"),
    }


def service_is_stale(runtime_dir: Path | str, name: str) -> list[str]:
    """Files this service loaded that have since changed. Empty means current."""
    stamp = read_stamp(runtime_dir, name)
    if not stamp:
        return []
    return changed_files(stamp)


def watch(
    runtime_dir: Path | str,
    name: str,
    *,
    interval: float = 3.0,
    settle: float = 6.0,
    exit_on_change: bool = True,
    config: Path | str | None = None,
) -> threading.Thread:
    """Exit when this service's own source changes, so it gets restarted.

    ``settle`` is what makes this safe to leave on while you work: a file must
    have stopped changing for that long before she acts on it. Without it she
    would restart on every keystroke of a half-written edit and never come up.

    Exits rather than reloading in place. Python cannot reliably swap a live
    module graph -- half-reloaded state is a far nastier failure than a
    restart, and her services take about a second to come back.
    """
    stamp = write_stamp(runtime_dir, name, config)
    LOG.info(
        "%s is running %d source files, fingerprint %s",
        name, stamp["files"], stamp["fingerprint"],
    )

    def loop() -> None:
        pending_since = 0.0
        while True:
            time.sleep(interval)
            try:
                moved = changed_files(stamp)
            except Exception:  # pragma: no cover - defensive
                continue
            if not moved:
                pending_since = 0.0
                continue
            now = time.monotonic()
            if pending_since == 0.0:
                pending_since = now
                LOG.info(
                    "%s: %d of its own source files changed; waiting %.0fs for the "
                    "edit to settle", name, len(moved), settle,
                )
                continue
            if now - pending_since < settle:
                continue
            names = ", ".join(sorted(Path(p).name for p in moved)[:6])
            LOG.warning(
                "%s IS RUNNING STALE CODE (%s). Exiting so the supervisor "
                "restarts it with the current source.", name, names,
            )
            if not exit_on_change:
                pending_since = 0.0
                continue
            # Flush before dying so the reason survives in the log.
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass
            os._exit(STALE_EXIT_CODE)

    thread = threading.Thread(target=loop, name=f"kendra-codestamp-{name}", daemon=True)
    thread.start()
    return thread
