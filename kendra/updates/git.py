from __future__ import annotations

import re
import subprocess
from typing import Any

from ..config import Settings

NAME_RE = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


class GitUpdateInspector:
    """Read/check Kendra's fixed Git channel without mutating the working tree."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.root
        self.remote = str(settings.get("updates.git_remote", "origin"))
        self.branch = str(settings.get("updates.git_branch", "main"))
        if not NAME_RE.fullmatch(self.remote) or not NAME_RE.fullmatch(self.branch):
            raise ValueError("Update remote or branch contains unsupported characters")

    def _git(self, *args: str, timeout: int = 20) -> str:
        process = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if process.returncode != 0:
            detail = process.stderr.strip()[-1_000:]
            raise RuntimeError(f"Git update check failed: {detail or 'git exited unsuccessfully'}")
        return process.stdout.strip()

    def status(self, *, fetch: bool = False) -> dict[str, Any]:
        expected_url = str(self.settings.get("updates.git_remote_url") or "").rstrip("/")
        actual_url = self._git("remote", "get-url", self.remote).rstrip("/")
        if expected_url and actual_url != expected_url:
            raise RuntimeError("Configured update remote does not match the pinned repository URL")
        if fetch:
            try:
                self._git("fetch", "--quiet", "--no-tags", self.remote, self.branch, timeout=60)
            except Exception as exc:
                # A missing remote branch or an offline network is a state to
                # report, not an error to throw at the dashboard.
                return {
                    "current_commit": self._git("rev-parse", "HEAD"),
                    "remote_commit": None,
                    "upgrade_available": False,
                    "note": f"remote unavailable: {str(exc)[:120]}",
                }
            remote_ref = "FETCH_HEAD"
        else:
            remote_ref = f"refs/remotes/{self.remote}/{self.branch}"
        current = self._git("rev-parse", "HEAD")
        try:
            remote_commit = self._git("rev-parse", remote_ref)
            behind, ahead = [
                int(value)
                for value in self._git("rev-list", "--left-right", "--count", f"HEAD...{remote_commit}").split()
            ]
        except RuntimeError:
            remote_commit = None
            behind = 0
            ahead = 0
        return {
            "remote": self.remote,
            "branch": self.branch,
            "remote_url": actual_url,
            "current_commit": current,
            "remote_commit": remote_commit,
            "commits_behind": behind,
            "local_commits_ahead": ahead,
            "upgrade_available": behind > 0,
            "working_tree_clean": not bool(self._git("status", "--porcelain")),
            "install_policy": "signed-release-only",
            "voice_install_enabled": bool(self.settings.get("updates.allow_voice_install", False)),
        }

    def voice_request(self) -> dict[str, Any]:
        result = self.status(fetch=True)
        if not result["upgrade_available"]:
            return {**result, "accepted": False, "reason": "already_current"}
        if not result["working_tree_clean"]:
            return {**result, "accepted": False, "reason": "working_tree_not_clean"}
        if not result["voice_install_enabled"]:
            return {
                **result,
                "accepted": False,
                "reason": "signed voice installation is locked until a release key/channel is configured",
            }
        return {
            **result,
            "accepted": False,
            "reason": "installer requires a verified signed release bundle; raw Git code is never activated",
        }
