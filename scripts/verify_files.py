#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_specs(item: dict) -> list[tuple[str, str | None, str | None]]:
    return [
        ("primary", item.get("source_archive") or item.get("path") or item.get("filename"), item.get("sha256")),
        ("config", item.get("config_path"), item.get("config_sha256")),
        ("mmproj", item.get("mmproj_path"), item.get("mmproj_sha256")),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Validate recorded files when present but skip unprovisioned large runtime assets",
    )
    args = parser.parse_args()
    failed = 0
    for manifest_name in ("models.yaml", "kiwix.yaml"):
        path = ROOT / "manifests" / manifest_name
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        items = (
            data.get("models", {})
            if manifest_name == "models.yaml"
            else {str(i): value for i, value in enumerate(data.get("archives", []))}
        )
        for name, item in items.items():
            specs = [spec for spec in artifact_specs(item) if spec[1] and spec[2]]
            if not specs:
                print(f"SKIP {manifest_name}:{name}: path/hash not fully recorded")
                continue
            for label, rel, expected in specs:
                artifact_name = f"{manifest_name}:{name}:{label}"
                assert rel is not None and expected is not None
                target = ROOT / rel
                if not target.exists():
                    if args.allow_missing:
                        print(f"SKIP {artifact_name}: not provisioned")
                    else:
                        print(f"FAIL {artifact_name}: missing {target}")
                        failed += 1
                    continue
                actual = sha256(target)
                if actual.lower() != str(expected).lower():
                    print(f"FAIL {artifact_name}: SHA-256 mismatch")
                    failed += 1
                else:
                    print(f"PASS {artifact_name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
