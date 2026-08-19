from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from ..config import Settings
from .slots import SlotManager
from .verify import UpdateVerifier, safe_extract_tar


class SignedReleaseStager:
    """Download, verify, build, and optionally activate a signed Git release."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.manifest_url = str(settings.require("updates.release_manifest_url"))
        self.signature_url = str(settings.require("updates.release_signature_url"))
        self.archive_url = str(settings.require("updates.release_archive_url"))
        self.staging_dir = settings.path("updates.staging_dir")
        self.verifier = UpdateVerifier(settings.path("updates.public_key_file"))
        for url in (self.manifest_url, self.signature_url, self.archive_url):
            self._assert_release_url(url)

    @staticmethod
    def _assert_release_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
            raise ValueError("Signed intelligence releases must use the pinned GitHub raw HTTPS channel")
        if not parsed.path.startswith("/NXTLupo/kendra/"):
            raise ValueError("Signed intelligence release URL is outside the pinned Kendra repository")

    @staticmethod
    def _download(url: str, destination: Path, max_bytes: int) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "KendraSignedUpdater/1.0"})
        total = 0
        with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise RuntimeError("Signed release artifact exceeds the configured size limit")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError("Signed release artifact exceeds the configured size limit")
                output.write(chunk)
        os.replace(temporary, destination)

    def _release_metadata(self, directory: Path) -> dict[str, Any]:
        manifest = directory / "manifest.yaml"
        signature = directory / "manifest.minisig"
        self._download(self.manifest_url, manifest, 256 * 1024)
        self._download(self.signature_url, signature, 64 * 1024)
        self.verifier.verify_manifest(manifest, signature)
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        if data.get("format") != "kendra-signed-git-release" or int(data.get("version", 0)) != 1:
            raise ValueError("Unsupported signed intelligence release format")
        commit = str(data.get("git_commit") or "")
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit.lower()):
            raise ValueError("Signed intelligence release has an invalid Git commit")
        return data

    def check(self) -> dict[str, Any]:
        check_dir = self.staging_dir / "check"
        check_dir.mkdir(parents=True, exist_ok=True)
        data = self._release_metadata(check_dir)
        current_file = self.settings.root / ".release-commit"
        current = current_file.read_text(encoding="utf-8").strip() if current_file.exists() else None
        return {
            "channel": "signed-github-release",
            "latest_commit": data["git_commit"],
            "release_name": data.get("release_name"),
            "current_commit": current,
            "upgrade_available": current != data["git_commit"],
            "voice_install_enabled": bool(self.settings.get("updates.allow_voice_install", False)),
            "signature": "valid",
        }

    def stage(self) -> dict[str, Any]:
        if not bool(self.settings.get("updates.allow_voice_install", False)):
            return {**self.check(), "accepted": False, "reason": "voice installation is disabled"}
        work = self.staging_dir / "incoming"
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        data = self._release_metadata(work)
        archive = work / "kendra-update.tar.gz"
        self._download(
            self.archive_url,
            archive,
            int(self.settings.get("updates.max_archive_bytes", 100 * 1024 * 1024)),
        )
        verified = self.verifier.verify_artifacts(work / "manifest.yaml", work)
        if archive.name not in verified["verified"]:
            raise RuntimeError("Signed manifest does not cover the Kendra update archive")

        manager = SlotManager(self.settings.path("updates.slots_root"))
        manager.ensure_layout()
        inactive = manager.clear_inactive()
        safe_extract_tar(archive, inactive)
        if not (inactive / "pyproject.toml").is_file() or not (inactive / "kendra").is_dir():
            raise RuntimeError("Signed release archive is not a complete Kendra application")
        (inactive / ".release-commit").write_text(str(data["git_commit"]) + "\n", encoding="utf-8")

        python = shutil.which("python3")
        if not python:
            raise RuntimeError("python3 is required to build the inactive Kendra slot")
        self._run([python, "-m", "venv", str(inactive / ".venv")], timeout=120)
        self._run(
            [str(inactive / ".venv/bin/pip"), "install", "--no-input", "-e", f"{inactive}[brain,hardware]"],
            timeout=900,
        )
        self._run(
            [str(inactive / ".venv/bin/python"), "-m", "compileall", "-q", str(inactive / "kendra")],
            timeout=120,
        )
        if bool(self.settings.get("updates.activate_after_stage", False)):
            manager.activate(inactive)
            state = "activated_restart_required"
        else:
            state = "staged_activation_disabled"
        receipt = {
            "format": "kendra-update-receipt",
            "git_commit": data["git_commit"],
            "slot": str(inactive),
            "state": state,
        }
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8")
        return {**receipt, "accepted": True, "signature": "valid"}

    @staticmethod
    def _run(command: list[str], timeout: int) -> None:
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        if process.returncode != 0:
            detail = (process.stderr or process.stdout)[-2_000:]
            raise RuntimeError(f"Inactive-slot validation failed: {detail}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
