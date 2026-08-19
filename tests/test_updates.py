from __future__ import annotations

import io
import tarfile

import pytest

from kendra.updates.installer import SignedReleaseStager
from kendra.updates.verify import safe_extract_tar


def test_safe_extract_rejects_parent_escape(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar:
        data = b"bad"
        info = tarfile.TarInfo("../escape.txt")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    with pytest.raises(ValueError, match="Unsafe"):
        safe_extract_tar(archive, tmp_path / "out")


def test_safe_extract_rejects_links(tmp_path):
    archive = tmp_path / "link.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    with pytest.raises(ValueError, match="member type"):
        safe_extract_tar(archive, tmp_path / "out")


def test_signed_release_channel_rejects_other_hosts(settings):
    settings.data["updates"]["release_manifest_url"] = "https://example.com/manifest.yaml"
    with pytest.raises(ValueError, match="pinned GitHub"):
        SignedReleaseStager(settings)
