from __future__ import annotations

import hashlib
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import yaml


class UpdateVerifier:
    def __init__(self, public_key_file: Path):
        self.public_key_file = public_key_file

    def verify_manifest(self, manifest: Path, signature: Path) -> None:
        if not shutil.which("minisign"):
            raise RuntimeError("minisign is required for update verification")
        process = subprocess.run(
            ["minisign", "-Vm", str(manifest), "-x", str(signature), "-P", self._public_key()],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(f"Update manifest signature failed: {process.stderr[-1000:]}")

    def _public_key(self) -> str:
        lines = [
            line.strip()
            for line in self.public_key_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("untrusted comment")
        ]
        if len(lines) != 1 or "AAAA" in lines[0] or "REPLACE" in lines[0]:
            raise RuntimeError("Replace config/minisign.pub with Kendra's real public key")
        return lines[0]

    def verify_artifacts(self, manifest: Path, base_dir: Path) -> dict[str, Any]:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        artifacts = data.get("artifacts", [])
        verified = []
        for artifact in artifacts:
            relative = Path(str(artifact["path"]))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Update manifest contains an unsafe artifact path")
            path = (base_dir / relative).resolve()
            if base_dir.resolve() not in path.parents and path != base_dir.resolve():
                raise ValueError("Update artifact escaped manifest directory")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            expected = str(artifact["sha256"]).lower()
            if digest != expected:
                raise RuntimeError(f"SHA-256 mismatch for {relative}")
            verified.append(str(relative))
        return {"verified": verified}


def safe_extract_tar(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with tarfile.open(archive, "r:*") as tar:
        members = tar.getmembers()
        for member in members:
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise ValueError(f"Unsafe archive member type: {member.name}")
            target = (destination / member.name).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe archive member: {member.name}")
        tar.extractall(destination, members=members)
