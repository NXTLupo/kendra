"""The rig has to reach the screen, not just the disk.

The pipeline downloaded a finished rig into data/tripo/ and stopped there,
while the app loads from dashboard/public/kendra3d/kendra-body.glb. Nothing
joined the two, so a successful re-rig would have spent real credits and
changed nothing on screen — the failure would have looked like the animation
work being broken.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "tripo_pipeline", Path(__file__).resolve().parent.parent / "scripts" / "tripo_pipeline.py",
)
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(pipeline)


def test_a_finished_rig_lands_where_the_app_loads_it(tmp_path, monkeypatch):
    stage = tmp_path / "public" / "kendra3d"
    dist = tmp_path / "dist" / "kendra3d"
    stage.mkdir(parents=True)
    dist.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "STAGE", stage)
    monkeypatch.setattr(pipeline, "DIST", dist)
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)

    (stage / "kendra-body.glb").write_bytes(b"the body she is wearing")
    (dist / "kendra-body.glb").write_bytes(b"the body she is wearing")
    rigged = tmp_path / "kendra-rigged.glb"
    rigged.write_bytes(b"the new octopod rig")

    pipeline.install_for_app(rigged)

    # Both trees updated: vite copies public/ into dist/ only at build time,
    # so writing public/ alone leaves the running app on the old body.
    assert (stage / "kendra-body.glb").read_bytes() == b"the new octopod rig"
    assert (dist / "kendra-body.glb").read_bytes() == b"the new octopod rig"


def test_the_body_she_is_wearing_is_never_destroyed(tmp_path, monkeypatch):
    stage = tmp_path / "public" / "kendra3d"
    stage.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "STAGE", stage)
    monkeypatch.setattr(pipeline, "DIST", tmp_path / "absent")
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)

    (stage / "kendra-body.glb").write_bytes(b"the octopus Jonathan supplied")
    rigged = tmp_path / "new.glb"
    rigged.write_bytes(b"replacement")

    pipeline.install_for_app(rigged)

    assert (stage / "kendra-body.previous.glb").read_bytes() == b"the octopus Jonathan supplied"
    assert (stage / "kendra-body.glb").read_bytes() == b"replacement"


def test_a_missing_dist_is_not_an_error(tmp_path, monkeypatch):
    """A checkout that has never been built must still install cleanly."""
    stage = tmp_path / "public" / "kendra3d"
    stage.mkdir(parents=True)
    monkeypatch.setattr(pipeline, "STAGE", stage)
    monkeypatch.setattr(pipeline, "DIST", tmp_path / "never-built")
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)

    rigged = tmp_path / "new.glb"
    rigged.write_bytes(b"rig")
    pipeline.install_for_app(rigged)

    assert (stage / "kendra-body.glb").read_bytes() == b"rig"
    assert not (tmp_path / "never-built").exists()
