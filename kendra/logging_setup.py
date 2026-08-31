from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Kendra runs for days at a time on a machine nobody is watching. Nothing
# rotated her logs and the directory reached 616 MB, at which point finding a
# real error meant grepping hundreds of megabytes of routine chatter. Every
# stream she writes is now bounded.
MAX_LOG_BYTES = 8 * 1024 * 1024
BACKUP_COUNT = 2


def configure_logging(level: str = "INFO", *, logs_dir: Path | None = None, name: str | None = None) -> None:
    """Configure stderr logging, plus a bounded per-service file when asked.

    ``logs_dir``/``name`` are supplied by the service entry points so each
    service owns one rotating file. Without them this is stderr only, which is
    what the CLI and the tests want.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(stream=sys.stderr)]
    if logs_dir is not None and name:
        try:
            target = Path(logs_dir)
            target.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    target / f"{name}.log",
                    maxBytes=MAX_LOG_BYTES,
                    backupCount=BACKUP_COUNT,
                    encoding="utf-8",
                )
            )
        except OSError:
            # A service that cannot open its log must still start. Losing the
            # file is an inconvenience; failing to boot is an outage.
            pass
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        # stdout is a machine-readable transport for commands such as the
        # native desktop bridge. Human diagnostics must never corrupt it.
        handlers=handlers,
        force=True,
    )


def trim_oversized_logs(logs_dir: Path, max_bytes: int = MAX_LOG_BYTES * 8) -> list[str]:
    """Roll any log file that grew unbounded before rotation existed.

    Returns the names it rolled. Called once at service start so a machine
    carrying the old 400 MB files heals itself instead of needing a manual
    cleanup that nobody remembers to run.
    """
    rolled: list[str] = []
    try:
        entries = sorted(Path(logs_dir).glob("*.log"))
    except OSError:
        return rolled
    for entry in entries:
        try:
            if entry.is_file() and entry.stat().st_size > max_bytes:
                backup = entry.with_suffix(".log.1")
                os.replace(entry, backup)
                rolled.append(entry.name)
        except OSError:
            continue
    return rolled
