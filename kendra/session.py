"""Where one conversation with Kendra ends and the next begins.

Every turn she has ever spoken lives in her brain forever, and the durable
things she learned from them live in her memories and her wiki. What should
NOT carry across a restart is the raw transcript: yesterday's half-finished
exchange replayed into today's prompt is not continuity, it is confusion. She
would answer a fresh "hello" against a two-hour-old thread, and the desktop
would show a conversation the person in front of her never had.

So the stack stamps a session when it starts, and both her rolling context and
her on-screen transcript begin there. Her memory does not reset — only the
window she is currently talking inside.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

LOG = logging.getLogger(__name__)

FILE = "session.json"


def path(runtime_dir: Path | str) -> Path:
    return Path(runtime_dir) / FILE


def begin(runtime_dir: Path | str) -> str:
    """Mark the start of a session. Returns the ISO timestamp used."""
    started = datetime.now(UTC).isoformat()
    target = path(runtime_dir)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"started_at": started, "epoch": time.time()}), encoding="utf-8"
        )
    except OSError:
        LOG.debug("Could not record the session start", exc_info=True)
    return started


def started_at(runtime_dir: Path | str) -> str | None:
    """When the current session began, or None if nothing has stamped one.

    None means "no boundary known", and callers fall back to their age window
    rather than showing nothing — a missing stamp must never make her look
    like she has lost her memory.
    """
    try:
        value = json.loads(path(runtime_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    started = value.get("started_at")
    return str(started) if started else None
