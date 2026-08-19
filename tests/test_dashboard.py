from pathlib import Path

import pytest

from kendra.brain.sync import BrainSyncClient
from kendra.dashboard.controller import DashboardController


def test_dashboard_photo_access_stays_in_photo_directory(settings, tmp_path: Path):
    photo = tmp_path / "photos" / "kendra-test.jpg"
    photo.parent.mkdir(parents=True, exist_ok=True)
    photo.write_bytes(b"jpeg")
    controller = DashboardController(settings)
    assert controller.photo_path(photo.name) == photo
    with pytest.raises(PermissionError):
        controller.photo_path("../config/default.yaml")


def test_wifi_brain_sync_rejects_command_injection(settings):
    sync = BrainSyncClient(settings)
    with pytest.raises(ValueError, match="hostname"):
        sync._target("kendra.local;touch-pwned", "kendra")
    with pytest.raises(ValueError, match="hostname"):
        sync._target("kendra.local", "root -oProxyCommand=bad")
