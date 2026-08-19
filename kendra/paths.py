from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    env = os.getenv("KENDRA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def resolve_path(value: str | Path, root: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (root or project_root()) / path
