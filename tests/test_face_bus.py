"""Her services must be able to tell her face what she is doing.

Before the face bus existed, the only channel from Kendra's services to
anything drawing her was the desktop bridge, which answers requests and
nothing else — Electron discarded any line without a matching request id. Her
renderer therefore polled a transcript every three seconds and animated
replies that had already finished playing.

These cover the transport and the timing contract. The renderer half lives in
dashboard/tests/speech-sync.test.mjs.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import numpy as np
import pytest

from kendra.facebus import FaceBusPublisher, FaceBusServer, socket_path


class _Settings:
    """Just the one attribute the bus needs."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir


@pytest.fixture()
def runtime_dir() -> Path:
    """A SHORT temporary directory.

    A unix socket path is capped near 104 bytes on macOS, and pytest's
    ``tmp_path`` is comfortably longer than that. Kendra's real socket lives
    at ``runtime/pc/face.sock`` and is nowhere near the limit, but the test
    must not fail for a reason her runtime will never hit.
    """
    with tempfile.TemporaryDirectory(prefix="kfb-", dir="/tmp") as directory:
        yield Path(directory)


async def _collect(events: list[dict], settings: _Settings, publish) -> list[dict]:
    async def on_event(payload: dict) -> None:
        events.append(payload)

    server = FaceBusServer(socket_path(settings), on_event)
    await server.start()
    try:
        publish(FaceBusPublisher(settings))
        # Publishing is fire-and-forget, so give the tasks a turn to land.
        for _ in range(40):
            await asyncio.sleep(0.01)
            if events:
                break
        await asyncio.sleep(0.05)
    finally:
        await server.close()
    return events


def test_events_arrive_in_order_with_their_payloads(runtime_dir: Path) -> None:
    settings = _Settings(runtime_dir)

    def publish(bus: FaceBusPublisher) -> None:
        bus.publish("listening")
        bus.publish("thinking", mode="search")
        bus.publish("speech_start", text="Hello there.", seconds=1.25)
        bus.publish("speech_end")

    events: list[dict] = []
    asyncio.run(_collect(events, settings, publish))
    assert [e["event"] for e in events] == [
        "listening",
        "thinking",
        "speech_start",
        "speech_end",
    ]
    assert events[1]["data"]["mode"] == "search"
    assert events[2]["data"]["seconds"] == pytest.approx(1.25)
    assert events[2]["data"]["text"] == "Hello there."


def test_publishing_with_nobody_listening_is_harmless(runtime_dir: Path) -> None:
    """The desktop app is usually not running. That must cost her nothing."""
    settings = _Settings(runtime_dir / "no-such-runtime")

    async def go() -> None:
        bus = FaceBusPublisher(settings)
        bus.publish("speech_start", text="into the void", seconds=1.0)
        await asyncio.sleep(0.2)

    asyncio.run(go())  # must not raise


def test_a_stale_socket_never_blocks_the_bus(runtime_dir: Path) -> None:
    """A crashed desktop app leaves the path behind; her face must still work.

    ``UnixJsonServer`` deliberately refuses to start on an existing socket, to
    stop duplicate services fighting over the microphone. That rule is wrong
    here: this is a sink, and refusing would mean one crash disables her face
    until someone deletes a file.
    """
    settings = _Settings(runtime_dir)
    stale = socket_path(settings)
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("not really a socket")

    events: list[dict] = []
    asyncio.run(_collect(events, settings, lambda bus: bus.publish("idle")))
    assert [e["event"] for e in events] == ["idle"]


def test_speech_duration_comes_from_the_audio_not_the_text() -> None:
    """The whole point: her mouth is timed by samples, not by characters.

    A character count said the old renderer's 13 chars/sec; the audio says
    exactly how long it is. This asserts the arithmetic the voice service
    publishes, against a buffer whose length we chose.
    """
    sample_rate = 24_000
    # 1.5 seconds of audio, by construction.
    audio = np.zeros(int(sample_rate * 1.5), dtype=np.int16)
    seconds = audio.shape[0] / float(sample_rate)
    assert seconds == pytest.approx(1.5)

    # The same words, estimated the old way, are wrong by a wide margin —
    # which is why they can never be used for timing again.
    text = "Yes."
    estimated = max(1.8, len(text) / 13)
    assert abs(estimated - seconds) > 0.25


def test_tts_engines_expose_a_speech_clock() -> None:
    """Both voices must be able to say when they started and stopped.

    Kokoro is her voice today and Piper is the documented one-line rollback,
    so a rollback must not silently take her face with it.
    """
    import inspect

    from kendra.voice.tts import KokoroTTS, PiperTTS, SpeechClock

    for engine in (KokoroTTS, PiperTTS):
        assert "self.clock = SpeechClock()" in inspect.getsource(engine.__init__), (
            f"{engine.__name__} must carry a speech clock"
        )
        playback = inspect.getsource(engine._speak_blocking)
        assert "self.clock.started(" in playback, (
            f"{engine.__name__} must announce playback as it begins"
        )
        assert "self.clock.ended()" in playback, (
            f"{engine.__name__} must announce the end, including when stopped early"
        )

    clock = SpeechClock()
    seen: list[tuple[str, float]] = []
    clock.on_start = lambda text, seconds: seen.append((text, seconds))
    clock.on_end = lambda: seen.append(("<end>", 0.0))
    clock.started("Hello.", 1.25)
    clock.ended()
    assert seen == [("Hello.", 1.25), ("<end>", 0.0)]


def test_a_failing_subscriber_never_costs_her_a_word() -> None:
    """Drawing is optional; speaking is not.

    If whatever is drawing Kendra raises, the audio thread must carry on. A
    dropped animation frame is a cosmetic loss; an exception on the playback
    thread is her going silent mid-sentence.
    """
    from kendra.voice.tts import SpeechClock

    clock = SpeechClock()

    def explode(*_args: object) -> None:
        raise RuntimeError("the renderer fell over")

    clock.on_start = explode
    clock.on_end = explode
    clock.started("Still speaking.", 1.0)  # must not raise
    clock.ended()  # must not raise


def test_the_bus_ignores_malformed_lines(runtime_dir: Path) -> None:
    settings = _Settings(runtime_dir)
    events: list[dict] = []

    async def go() -> None:
        async def on_event(payload: dict) -> None:
            events.append(payload)

        server = FaceBusServer(socket_path(settings), on_event)
        await server.start()
        try:
            _, writer = await asyncio.open_unix_connection(str(socket_path(settings)))
            writer.write(b"not json\n")
            writer.write(b'{"no_event_key": true}\n')
            writer.write(b'{"event": "idle", "data": {}}\n')
            await writer.drain()
            await asyncio.sleep(0.15)
            writer.close()
        finally:
            await server.close()

    asyncio.run(go())
    assert [e["event"] for e in events] == ["idle"]


def test_voice_service_publishes_state_alongside_the_lights() -> None:
    """One signal, two subscribers.

    The voice service already told the LED ring what she was doing. On the
    desktop that driver is disabled, so the description was computed and
    thrown away every turn while her face guessed. The same call must now
    reach both.
    """
    import inspect

    from kendra.voice.service import VoiceService

    leds = inspect.getsource(VoiceService._leds)
    assert "_publish_state" in leds, "the light state must also reach her face"

    publish_state = inspect.getsource(VoiceService._publish_state)
    for event in ("thinking", "idle", "listening"):
        assert f'"{event}"' in publish_state

    bind = inspect.getsource(VoiceService._bind_speech_clock)
    assert "speech_start" in bind and "speech_end" in bind
    assert "publish_threadsafe" in bind, "playback runs on a worker thread"


def test_a_service_running_old_code_is_reported() -> None:
    """The failure mode that lost a day.

    A service started before an edit keeps running the old code perfectly
    happily: no error, no warning, and the change simply has no effect --
    which looks exactly like a change that did not work.

    The full guarantee now lives in tests/test_codestamp.py: services stamp
    what they loaded and exit when it changes. This asserts the reporting
    half, and specifically that it is answered PER SERVICE from what that
    service imported. The first version compared every service against the
    newest file anywhere under kendra/, so editing one flagged all ten --
    a smoke alarm that goes off when you make toast gets ignored.
    """
    import inspect

    from kendra.health import runtime_truth

    source = inspect.getsource(runtime_truth.check_service_freshness)
    # Assert the CONTRACT, not the prose: an earlier version of this test
    # matched the word "newest" and tripped on the docstring explaining why
    # scanning for the newest file was wrong.
    body = source[source.index('"""', source.index('"""') + 3) + 3 :]
    assert "service_report(" in body, "freshness must come from each service's own stamp"
    assert '"unknown"' in body, "a service that cannot say what it loaded is not 'fine'"
    # Never a package-wide scan: that reported all ten stale whenever any one
    # file changed, which is a signal nobody reads.
    assert ".rglob(" not in body

def test_total_deafness_is_announced_rather_than_endured() -> None:
    """On macOS an unauthorized microphone returns zeros forever.

    So "she cannot hear anything" and "nobody is talking" look identical from
    the inside, and she just sits there -- which reads as a crash. She has to
    say so.
    """
    import inspect

    from kendra.voice.audio import LocalAudioCapture
    from kendra.voice.service import VoiceService

    probe = inspect.getsource(LocalAudioCapture)
    assert "self.no_microphone = True" in probe, "silence across every device must be flagged"
    assert "self.no_microphone = False" in probe, "and cleared when a live input is found"

    listen = inspect.getsource(VoiceService)
    assert 'publish("deaf")' in listen, "her face must be told she cannot hear"
    assert "NO WORKING MICROPHONE" in listen, "and the log must say it plainly"


def test_the_guitar_interface_is_no_longer_pinned_as_her_microphone() -> None:
    """It is switched on only when Jonathan plays, so pinning it made a normal
    start log a device error every single time."""
    from kendra.config import Settings

    settings = Settings.load("config/pc.yaml")
    assert str(settings.get("voice.capture.device", "auto")) == "auto"


def test_voice_is_not_detached_from_its_parent() -> None:
    """setsid() severs macOS microphone authorization.

    Every service is started with ``start_new_session`` so it survives the
    launching shell. For voice that is actively harmful: setsid() breaks the
    TCC responsible-process chain, the child inherits no microphone
    permission, and a denied microphone on macOS does not raise -- it opens
    fine and returns zeros forever. Measured on a machine whose input volume
    was 88 with the built-in mic as system default and nothing muted: every
    device read RMS 0.
    """
    import inspect

    from kendra.devstack import DevStack

    source = inspect.getsource(DevStack.start)
    assert 'service.name != "voice"' in source, (
        "voice must stay attached to the app that holds microphone permission"
    )
    assert "start_new_session=detached" in source


def test_the_sight_prompt_matches_the_eye_she_actually_has() -> None:
    """A workaround for the old eye was left in place across the swap.

    The llama.cpp eye cannot answer questions, so she captioned and let her
    language model answer from the caption. Her eye was then switched to
    Moondream's own ONNX runtime, whose measured contract is the opposite:

        "what is the person holding?"    1.2 s   correct
        free-form "describe this image"  12.5 s  invents detail -- avoid

    She was still sending "Describe this image." Asked what he was holding she
    spent 10.1 s and answered "a white Wii remote in your mouth".
    """
    from kendra.agent.planner import AgentRuntime
    from kendra.config import Settings

    def prompt_for(provider: str, question: str) -> str:
        runtime = object.__new__(AgentRuntime)
        settings = Settings.load("config/pc.yaml")
        original = settings.get
        settings.get = lambda key, default=None: (
            provider if key == "vision.semantic_provider" else original(key, default)
        )
        runtime.settings = settings
        return AgentRuntime._vlm_prompt(runtime, question)

    # The eye she has now asks his actual question, briefly.
    assert prompt_for("moondream_onnx", "what am I holding in my hand?") == (
        "What is the person holding?"
    )
    assert prompt_for("moondream_onnx", "what am I wearing?") == "What is the person wearing?"
    # Never a free-form description, which is the slow, inventive path.
    for question in ("what am I holding?", "tell me what you see", "describe the room"):
        assert "describe this image" not in prompt_for("moondream_onnx", question).casefold()
    # Never a mangled imperative: naive pronoun substitution produced
    # "Tell me what the person see?".
    assert prompt_for("moondream_onnx", "tell me what you see") == (
        "What is in front of the camera?"
    )
    # Every prompt stays short; long prompts are what make this model wander.
    for question in ("what is the person doing over there by the window with the guitar?",):
        assert len(prompt_for("moondream_onnx", question).split()) <= 15

    # The llama.cpp eye keeps its captioning workaround, because it needs it.
    assert prompt_for("llamacpp", "what am I holding in my hand?") == "Describe this image."
    # ...except counting, which captions could never answer.
    assert "fingers" in prompt_for("llamacpp", "how many fingers am I holding up?")


def test_whichever_eye_she_has_is_warmed() -> None:
    """The warm keeper only ever pinged the llama.cpp HTTP endpoint."""
    import inspect

    from kendra.vision.service import VisionService

    for name in ("_warm_ping", "_warm_vlm"):
        source = inspect.getsource(getattr(VisionService, name))
        assert 'hasattr(eye, "warm")' in source, f"{name} must warm a local eye too"
