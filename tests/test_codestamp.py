"""A service must not be able to go on running code that no longer exists.

This is the test file for the day that was lost to it. Kendra's stack was
healthy by every check she had -- ten services alive, every socket answering,
every port responding -- while all ten ran source from the previous evening.
Nothing errored. Every fix simply had no effect, which is indistinguishable
from a fix that did not work, so the same bugs were re-diagnosed and re-fixed
against code that was never loaded.

The guarantee has four parts, and all four are covered here:

  1. Each service records the exact files IT imported, not the whole package.
  2. It exits when one of those files changes.
  3. A service that cannot say what it loaded is reported, not assumed fine.
  4. Something restarts it -- the desktop supervisor, or systemd on the robot.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from kendra import codestamp

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


@pytest.fixture()
def runtime() -> Path:
    with tempfile.TemporaryDirectory(prefix="kcs-", dir="/tmp") as directory:
        yield Path(directory)


def test_it_records_only_kendra_code(runtime: Path) -> None:
    """Not site-packages, and not files this process never imported."""
    stamp = codestamp.write_stamp(runtime, "probe")
    assert stamp["files"] > 3
    for path in stamp["sources"]:
        assert "/kendra/" in path, path
        assert "site-packages" not in path


def test_a_changed_file_is_detected(runtime: Path) -> None:
    stamp = codestamp.write_stamp(runtime, "probe")
    assert codestamp.changed_files(stamp) == []

    target = next(iter(stamp["sources"]))
    os.utime(target, (time.time() + 5, time.time() + 5))
    try:
        assert Path(target).name in [Path(p).name for p in codestamp.changed_files(stamp)]
    finally:
        os.utime(target, (stamp["sources"][target], stamp["sources"][target]))


def test_a_deleted_file_counts_as_changed(runtime: Path) -> None:
    stamp = {"sources": {"/no/such/kendra/file.py": 1.0}}
    assert codestamp.changed_files(stamp) == ["/no/such/kendra/file.py"]


def test_an_unstamped_service_is_unknown_not_fine(runtime: Path) -> None:
    """The exact hole that let ten stale services report health.

    "I cannot tell you what I am running" must never be reported as "current".
    """
    report = codestamp.service_report(runtime, "never-started")
    assert report["state"] == "unknown"
    assert "no code stamp" in report["why"]


def test_a_stamp_from_a_different_process_is_not_trusted(runtime: Path) -> None:
    """A leftover stamp from a dead service must not vouch for a live one."""
    codestamp.write_stamp(runtime, "probe")
    report = codestamp.service_report(runtime, "probe", pid=os.getpid() + 12345)
    assert report["state"] == "unknown"


def test_a_matching_process_reads_as_current(runtime: Path) -> None:
    codestamp.write_stamp(runtime, "probe")
    assert codestamp.service_report(runtime, "probe", pid=os.getpid())["state"] == "current"


def test_the_fingerprint_moves_only_when_something_moves() -> None:
    sources = {"/a/kendra/x.py": 1.0, "/a/kendra/y.py": 2.0}
    assert codestamp.fingerprint(sources) == codestamp.fingerprint(dict(reversed(sources.items())))
    assert codestamp.fingerprint(sources) != codestamp.fingerprint({**sources, "/a/kendra/y.py": 2.5})


@pytest.mark.skipif(not PYTHON.exists(), reason="needs the project virtualenv")
def test_a_real_service_exits_when_its_own_source_changes() -> None:
    """End to end, with a real service process.

    Not a unit test of the predicate: the whole point is that the running
    process actually goes away, because a check nobody acts on is what this
    replaces.
    """
    with tempfile.TemporaryDirectory(prefix="kcs-", dir="/tmp") as directory:
        runtime = Path(directory)
        profile = runtime / "p.yaml"
        profile.write_text(
            "project: {mode: simulation}\n"
            f"paths: {{runtime_dir: {runtime}, logs_dir: {runtime}/logs}}\n"
            "dev: {code_watch_seconds: 0.5, code_settle_seconds: 1.0}\n"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        process = subprocess.Popen(
            [str(PYTHON), "-m", "kendra", "--config", str(profile), "service", "leds"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            stamp_file = codestamp.stamp_path(runtime, "leds")
            deadline = time.time() + 45
            while time.time() < deadline and not stamp_file.exists():
                if process.poll() is not None:
                    pytest.fail(f"service exited early: {process.stdout.read()[:600]}")
                time.sleep(0.1)
            assert stamp_file.exists(), "the service never recorded what it loaded"
            stamp = json.loads(stamp_file.read_text())
            assert stamp["pid"] == process.pid

            # Touch a file it actually loaded.
            target = next(p for p in stamp["sources"] if p.endswith("leds/service.py"))
            os.utime(target, None)

            try:
                code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pytest.fail("the service kept running its own stale code")
            assert code == codestamp.STALE_EXIT_CODE, f"expected 75 (stale), got {code}"
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


def test_the_desktop_supervises_and_does_not_loop() -> None:
    """Exiting on stale code is only safe if something brings it back."""
    main = (ROOT / "dashboard/electron/main.mjs").read_text(encoding="utf-8")
    assert "superviseServices" in main
    assert "setInterval(() => void superviseServices()" in main
    # A crash that is not staleness must not become an infinite restart storm.
    assert "RESTART_LIMIT" in main and "backing off" in main


def test_the_robot_supervises_too() -> None:
    """On the Pi, systemd is the supervisor. Every unit must restart."""
    units = sorted((ROOT / "systemd").glob("kendra-*.service"))
    assert units, "no systemd units found"
    for unit in units:
        text = unit.read_text(encoding="utf-8")
        assert "Restart=always" in text, f"{unit.name} would not come back"


def test_the_robot_profile_opts_out_deliberately() -> None:
    """A body with legs must not restart itself mid-motion.

    Her code does not change under her out there anyway -- updates arrive
    through the signed A/B slot mechanism, which restarts deliberately.
    """
    from kendra.config import Settings

    assert Settings.load("config/pc.yaml").get("dev.exit_on_stale_code") is True
    assert Settings.load("config/production.example.yaml").get("dev.exit_on_stale_code") is False


def test_dev_status_answers_the_second_question() -> None:
    """"Alive" was the only question it ever asked, and that was the bug."""
    import inspect

    from kendra.devstack import DevStack

    source = inspect.getsource(DevStack.status)
    assert '"code"' in source and "service_report" in source
    assert "stale_services" in source


def test_the_launcher_refuses_to_leave_stale_services_running() -> None:
    launcher = (ROOT / "scripts/kendra_desktop_launcher.sh").read_text(encoding="utf-8")
    assert "NEEDS_FRESH" in launcher
    assert 'not in (None, "current")' in launcher
    # It must not depend on the model server being up to make this decision.
    freshness = launcher[launcher.index("NEEDS_FRESH") :]
    assert "kendra truth" not in freshness


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))


def test_the_active_config_counts_as_source(runtime: Path) -> None:
    """A config edit is a code edit as far as a running service is concerned.

    Her Slot 0 text, her model expectations, her thresholds and her microphone
    selection all live in YAML and are read once at construction. Watching
    only .py files left exactly the same silent-staleness hole the rest of
    this module exists to close: Slot 0 was repointed from the 1462-token
    charter to the 490-token kernel, and no service would have noticed.
    """
    config = runtime / "profile.yaml"
    config.write_text("project: {mode: simulation}\n", encoding="utf-8")
    stamp = codestamp.write_stamp(runtime, "probe", config)
    assert str(config.resolve()) in stamp["sources"]
    assert codestamp.changed_files(stamp) == []

    os.utime(config, (time.time() + 5, time.time() + 5))
    assert str(config.resolve()) in codestamp.changed_files(stamp)


def test_the_service_entry_point_passes_its_config() -> None:
    import inspect

    from kendra import cli

    source = inspect.getsource(cli.cmd_service)
    assert "config=getattr(args" in source, "the watcher must know which profile is live"
