from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from kendra.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config" / "default.yaml").read_text(encoding="utf-8"))
    data = copy.deepcopy(data)
    data["paths"]["runtime_dir"] = str(tmp_path / "runtime")
    data["paths"]["brain_db"] = str(tmp_path / "brain.db")
    data["paths"]["photos_dir"] = str(tmp_path / "photos")
    data["paths"]["outbox_dir"] = str(tmp_path / "outbox")
    data["paths"]["logs_dir"] = str(tmp_path / "logs")
    data["paths"]["exports_dir"] = str(tmp_path / "exports")
    data["brain"]["backup_dir"] = str(tmp_path / "exports" / "backups")
    data["brain"]["jsonl_export_dir"] = str(tmp_path / "exports" / "jsonl")
    data["brain"]["import_dir"] = str(tmp_path / "exports" / "imports")
    data["vision"]["face"]["embeddings_dir"] = str(tmp_path / "faces")
    # CI runners carry no model weights (600MB embedding model, GGUFs). When
    # the semantic model is absent, tests fall back to the deterministic
    # hashing provider — the store's behavior contract is identical, only
    # recall quality differs, and quality is asserted by on-machine tests.
    embed_dir = root / str(data["brain"]["embedding"]["model_path"]).lstrip("./")
    import os
    if os.environ.get("KENDRA_TEST_NO_MODELS") == "1" or not embed_dir.exists():
        data["brain"]["embedding"] = {"provider": "hashing", "dimensions": 256}
    return Settings(data=data, root=root)
