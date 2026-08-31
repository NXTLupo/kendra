"""The whole Python half of the chain, through the real bridge process.

The unit tests cover the bus in isolation. This one spawns
``kendra dashboard-bridge`` exactly as Electron does and asserts that an event
published by a service comes back out of its stdout in the shape Electron
forwards to the renderer. It is the difference between "the parts work" and
"her face will actually move when she speaks".
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

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


@pytest.mark.skipif(not PYTHON.exists(), reason="needs the project virtualenv")
def test_a_published_event_reaches_the_bridge_stdout() -> None:
    import asyncio

    from kendra.facebus import FaceBusPublisher

    with tempfile.TemporaryDirectory(prefix="kfb-", dir="/tmp") as directory:
        runtime = Path(directory)
        profile = runtime / "probe.yaml"
        # A throwaway runtime dir: this must never touch a running stack's
        # sockets, and starting a second bridge against the real one would.
        profile.write_text(
            "project:\n  mode: simulation\n"
            f"paths:\n  runtime_dir: {runtime}\n"
            "voice:\n  asr:\n    provider: whisper_cpp\n"
        )
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUNBUFFERED="1")
        process = subprocess.Popen(
            [str(PYTHON), "-m", "kendra", "--config", str(profile), "dashboard-bridge"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            socket = runtime / "face.sock"
            deadline = time.time() + 60
            while time.time() < deadline and not socket.exists():
                if process.poll() is not None:
                    pytest.fail(f"bridge exited early: {process.stderr.read()[:800]}")
                time.sleep(0.1)
            assert socket.exists(), "the bridge never opened the face socket"

            class _Settings:
                runtime_dir = runtime

            async def publish() -> None:
                bus = FaceBusPublisher(_Settings())
                bus.publish("thinking", mode="search")
                bus.publish("speech_start", text="Here is what I found.", seconds=1.75)
                bus.publish("speech_end")
                await asyncio.sleep(0.6)

            asyncio.run(publish())
            time.sleep(0.4)
        finally:
            process.terminate()
            try:
                stdout, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
                stdout, _ = process.communicate()

    events = [
        json.loads(line)
        for line in stdout.splitlines()
        if line.strip().startswith("{") and '"event"' in line
    ]
    assert [event["event"] for event in events] == [
        "thinking",
        "speech_start",
        "speech_end",
    ]
    # The duration survives the whole trip untouched. Everything downstream
    # depends on this number being the audio's, not an estimate.
    assert events[1]["data"]["seconds"] == pytest.approx(1.75)
    assert events[1]["data"]["text"] == "Here is what I found."
    # Events must never be confused with command responses: Electron routes on
    # the presence of `event` and would otherwise drop them.
    assert all("id" not in event for event in events)


def test_electron_forwards_events_instead_of_discarding_them() -> None:
    """The precise line that caused the bug.

    ``const item = pending.get(message.id); if (!item) return;`` silently threw
    away every unsolicited line. The event check has to come first.
    """
    main = (ROOT / "dashboard/electron/main.mjs").read_text(encoding="utf-8")
    event_branch = main.index("if (message.event)")
    id_lookup = main.index("pending.get(message.id)")
    assert event_branch < id_lookup, "events must be handled before the id lookup drops them"
    assert 'webContents.send("kendra:event"' in main


def test_the_renderer_no_longer_times_speech_from_a_poll() -> None:
    body = (ROOT / "dashboard/src/KendraBody.tsx").read_text(encoding="utf-8")
    assert "speechDuration" not in body
    assert "speech_start" in body
    page = (ROOT / "dashboard/app/page.tsx").read_text(encoding="utf-8")
    # Polling still exists, and should: pose and sentiment are snapshot-shaped.
    # It simply may never be what starts her mouth again.
    assert "setInterval(refresh, 3000)" in page


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
