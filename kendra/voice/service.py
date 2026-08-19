from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..agent.planner import AgentRuntime
from ..config import Settings
from ..ipc import UnixJsonClient, UnixJsonServer
from .acks import AckPlayer, ThinkingSounds
from .asr import build_asr
from .audio import LocalAudioCapture
from .streaming import PhraseAccumulator
from .tts import create_tts
from .wake import DisabledWakeWord, build_wake_provider

LOG = logging.getLogger(__name__)


_CAPTION_RE = re.compile(
    r"^[\s\[\(\*]*(?:upbeat |soft |tense |dramatic )?"
    r"(?:music|applause|laughter|keyboard clicking|typing|silence|noise|coughs?|sighs?|clicking)"
    r"[\s\]\)\*\.!]*$",
    re.I,
)


def _strip_wake_prefix(text: str, phrases: list[str]) -> str:
    """Remove a leading wake phrase from the transcript.

    The wake-tail buffer (which saved beheaded commands) means the wake words
    themselves now reach ASR: "Hey Kendra, good morning" arrives whole. The
    model mirrors greetings, so leaving "Hey Kendra" in the prompt makes her
    repeat the user's words back. Strip it deterministically instead.
    """
    stripped = text.strip()
    lowered = stripped.casefold()
    for phrase in phrases:
        for lead in (f"hey {phrase}", f"hi {phrase}", f"okay {phrase}", f"ok {phrase}", phrase):
            if lowered.startswith(lead):
                remainder = stripped[len(lead):].lstrip(" ,.!?;:-")
                # "Hey Kendra." alone is a greeting; mapping it to "Hey."
                # stops her from mirroring her own name back.
                return remainder if remainder else "Hey."
    return stripped


def _is_noise_caption(text: str) -> bool:
    """Whisper captions non-speech audio like a subtitle track: "(upbeat
    music)", "[Applause]". Those are sounds in the room, not something the
    user said — never answer them."""
    stripped = text.strip()
    if _CAPTION_RE.match(stripped):
        return True
    return bool(stripped) and stripped[0] in "([" and stripped[-1] in ")]"


class VoiceService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.asr = build_asr(settings)
        self.tts = create_tts(settings)
        self.acks = AckPlayer(settings, self.tts)
        self.thinking_sounds = ThinkingSounds(settings)
        self.audio = LocalAudioCapture(settings)
        agent_timeout = float(settings.get("agent.client_timeout_seconds", 320))
        self.agent = UnixJsonClient(settings.socket_path("agent"), timeout=agent_timeout)
        self.streaming_agent = AgentRuntime(settings)
        self.body = UnixJsonClient(settings.socket_path("body"), timeout=2)
        self.server = UnixJsonServer(settings.runtime_dir / "voice.sock", self.handle)
        self.wake_provider = build_wake_provider(settings, "voice.wake")
        self.stop_provider = build_wake_provider(settings, "voice.stop_wake")
        self.wake_enabled = str(settings.get("voice.wake.provider", "disabled")) != "disabled"
        self.stop_enabled = str(settings.get("voice.stop_wake.provider", "disabled")) != "disabled"
        self.stream_responses = bool(settings.get("voice.streaming.enabled", True))
        self._capture_lock = asyncio.Lock()
        self._manual_capture_active = asyncio.Event()
        self._wake_cancel = threading.Event()

    async def _spoken_stop(self, reason: str) -> None:
        self.tts.stop()
        try:
            await self.body.call("stop", {"reason": reason})
        except Exception:
            LOG.exception("Spoken stop could not reach body service")

    async def _speak_with_barge_in(self, text: str, affect: str) -> bool:
        """Speak locally while listening for Kendra's secondary spoken stop."""

        self.thinking_sounds.stop()
        # Self-echo ledger: everything she says is remembered briefly so her
        # own voice, picked up by her own microphone, can never become a
        # "user" turn. (Her ambient comment was once transcribed as Jonathan
        # saying "Taking Sir Look, the image shows a man...".)
        ledger = getattr(self, "_spoken_ledger", None)
        if ledger is None:
            ledger = self._spoken_ledger = []
        ledger.append((time.time(), text))
        del ledger[:-8]
        self._speaking_until = time.time() + max(2.0, len(text) * 0.07) + 1.0

        # The per-phrase stop monitor opens a fresh CoreAudio input stream for
        # every sentence Kendra speaks — the main source of intermittent
        # "Error opening RawInputStream" wake-loop crashes on the desktop,
        # where there is no physical motion to emergency-stop anyway. The
        # robot profile keeps it on.
        if not self.stop_enabled or not bool(self.settings.get("voice.barge_in_monitor", True)):
            await self.tts.speak(text, affect=affect)
            # Playback is over: replace the length-based estimate with the
            # truth plus a short mic-tail guard. The estimate ran long and
            # ate her listening window, or ran short and let her own voice
            # be transcribed as Jonathan.
            self._speaking_until = time.time() + 0.6
            return False
        cancel = threading.Event()
        monitor = asyncio.create_task(
            asyncio.to_thread(self.audio.wait_for_wake, DisabledWakeWord(), self.stop_provider, cancel)
        )
        speech = asyncio.create_task(self.tts.speak(text, affect=affect))
        done, _ = await asyncio.wait({monitor, speech}, return_when=asyncio.FIRST_COMPLETED)
        interrupted = False
        if monitor in done:
            event = monitor.result()
            if event == "stop":
                interrupted = True
                await self._spoken_stop("spoken stop during local TTS playback")
        cancel.set()
        if not speech.done():
            await speech
        if not monitor.done():
            await monitor
        self._speaking_until = time.time() + 0.6
        return interrupted

    async def _stream_and_speak(self, user_text: str) -> dict[str, Any]:
        """Generate locally and start Piper on the first complete phrase."""

        queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=8)
        accumulator = PhraseAccumulator(
            min_chars=int(self.settings.get("voice.streaming.min_phrase_chars", 28)),
            max_chars=int(self.settings.get("voice.streaming.max_phrase_chars", 140)),
        )
        last_affect = "warm"
        interrupted = asyncio.Event()

        async def on_delta(delta: str, affect: str) -> None:
            nonlocal last_affect
            if interrupted.is_set():
                raise asyncio.CancelledError
            last_affect = affect
            for phrase in accumulator.feed(delta):
                await queue.put((phrase, affect))

        async def produce() -> dict[str, Any]:
            try:
                result = await self.streaming_agent.stream_voice_turn(user_text, on_delta, source="voice")
                tail = accumulator.flush()
                if tail:
                    await queue.put((tail, str(result.get("affect") or last_affect)))
                return result
            finally:
                await queue.put(None)

        producer = asyncio.create_task(produce(), name="kendra-local-voice-generator")
        import difflib

        # Phrases she must not speak: the user's own words mirrored back, and
        # near-verbatim sentences from her own recent replies (a small model
        # copies both out of the prompt). Streaming cannot retract audio, so
        # each phrase is checked BEFORE Piper gets it. Short pleasantries are
        # exempt — repeating "Good morning" is human, reciting a paragraph is
        # parroting.
        recent_sentences: list[str] = []
        try:
            for turn in await self.streaming_agent.brain.recent_turns(limit=5, max_age_seconds=1800) or []:
                reply = str(turn.get("kendra_text") or "")
                recent_sentences += [
                    s.strip().casefold() for s in re.split(r"(?<=[.!?])\s+", reply) if len(s.strip()) > 20
                ]
        except Exception:
            pass

        def _speakable_phrase(candidate: str, first: bool) -> bool:
            folded = candidate.strip().casefold()
            if first and difflib.SequenceMatcher(None, folded, user_text.strip().casefold()).ratio() > 0.75:
                return False
            if len(folded) <= 20:
                return True
            return not any(
                difflib.SequenceMatcher(None, folded, old).ratio() > 0.88 for old in recent_sentences
            )

        spoken_phrases: list[str] = []
        was_interrupted = False
        while True:
            item = await queue.get()
            if item is None:
                break
            phrase, affect = item
            if not _speakable_phrase(phrase, first=not spoken_phrases):
                LOG.info("Skipped an echoed phrase before it was spoken")
                continue
            spoken_phrases.append(phrase)
            if await self._speak_with_barge_in(phrase, affect):
                was_interrupted = True
                interrupted.set()
                producer.cancel()
                break
            if queue.empty() and not producer.done():
                # She spoke an acknowledgment (or a phrase) and the slow
                # work continues: bring the thinking blips back so the wait
                # never reads as a hang. They stop when the next phrase
                # speaks or when Jonathan starts talking.
                self.thinking_sounds.cue()

        if was_interrupted:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer
            return {
                "text": " ".join(spoken_phrases).strip(),
                "affect": "alert",
                "interrupted": True,
            }
        result = await producer
        return {**result, "interrupted": False}

    async def _capture_turn(
        self, start_timeout: float | None = None, threshold_multiplier: float = 1.0
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="kendra-voice-") as directory:
            wav = Path(directory) / "input.wav"
            # Prefill the model's stable prompt prefix concurrently with the
            # microphone capture: the KV cache is warm before ASR finishes.
            prewarm = asyncio.create_task(self.streaming_agent.prewarm_conversation())
            prewarm.add_done_callback(lambda _t: None)
            await asyncio.to_thread(
                self.audio.capture_utterance,
                wav,
                start_timeout,
                threshold_multiplier,
                self.thinking_sounds.stop,
            )
            # She heard you: acknowledge instantly while ASR and the LLM run —
            # but only when speech was actually captured. Acknowledging a
            # silent timeout makes her sound broken.
            if self.audio.last_capture_speech:
                self.acks.play_random()
                self.thinking_sounds.start()
            user_text = await self.asr.transcribe(wav)
            if not user_text or _is_noise_caption(user_text):
                return {"heard": user_text, "response": ""}
            user_text = _strip_wake_prefix(
                user_text,
                [str(self.settings.get("voice.wake.phrase", "kendra")).casefold()],
            )
            import difflib

            for spoken_at, spoken in getattr(self, "_spoken_ledger", []):
                if time.time() - spoken_at > 45:
                    continue
                if difflib.SequenceMatcher(
                    None, user_text.casefold(), spoken.casefold()
                ).ratio() > 0.45:
                    LOG.info("Discarded self-echo transcript: %r", user_text[:60])
                    return {"heard": "", "response": ""}
            lowered = user_text.strip().lower().rstrip(".!")
            if lowered in {"that's all", "thats all", "thanks kendra", "thank you kendra", "go to sleep", "goodnight", "good night", "we're done", "that will be all"}:
                await self._speak_with_barge_in("Okay.", "warm")
                return {"heard": user_text, "response": "Okay.", "end_conversation": True}
            if user_text.strip().lower() in {"stop", "kendra stop", "stop kendra"}:
                await self._spoken_stop("spoken secondary stop")
                return {"heard": user_text, "response": "Stopped.", "affect": "alert", "end_conversation": True}
            # Mic checks are phatic — the alive answer is an instant one.
            # Sent to the LLM, Gemma answers with device diagnostics ("my
            # internal microphones are active") no matter how it's steered.
            if re.search(r"\b(?:can|do) you hear me\b|\bare you (?:listening|there|awake)\b|\byou there\b", lowered):
                reply = random.choice([
                    "Loud and clear.",
                    "Yep, I hear you.",
                    "I hear you just fine. What's up?",
                    "Right here. Go ahead.",
                ])
                await self._speak_with_barge_in(reply, "warm")
                return {"heard": user_text, "response": reply}

            if self.stream_responses:
                result = await self._stream_and_speak(user_text)
                return {
                    "heard": user_text,
                    "response": str(result.get("text", "")),
                    "affect": str(result.get("affect") or "warm"),
                    "interrupted": bool(result.get("interrupted", False)),
                    "session_id": result.get("session_id"),
                    "streamed": True,
                }

            result = await self.agent.call("turn", {"text": user_text, "source": "voice"})
            response = str(result["text"])
            affect = str(result.get("affect") or "warm")
            was_interrupted = await self._speak_with_barge_in(response, affect)
            return {
                "heard": user_text,
                "response": response,
                "affect": affect,
                "interrupted": was_interrupted,
                "session_id": result.get("session_id"),
                "streamed": False,
            }

    async def _conversation(self) -> None:
        """One wake word opens a whole conversation, not a single turn.

        After Kendra finishes speaking, she keeps listening for a follow-up —
        longer when her reply asked a question — and only returns to
        wake-word listening when Jonathan stays quiet. A soft cue tone marks
        the open floor. Identical logic on the robot body.
        """
        result = await self.one_turn()
        if not bool(self.settings.get("voice.followup.enabled", True)):
            return
        await self._followup_loop(result)

    async def _followup_loop(self, result: dict[str, Any]) -> None:
        base_window = float(self.settings.get("voice.followup.window_seconds", 10.0))
        question_window = float(self.settings.get("voice.followup.question_window_seconds", 20.0))
        max_turns = int(self.settings.get("voice.followup.max_turns", 40))
        # Follow-up capture demands clearly deliberate speech: ambient noise
        # and music sit near the base threshold and once self-triggered a
        # window loop where Kendra conversed with the stereo indefinitely.
        multiplier = float(self.settings.get("voice.followup.threshold_multiplier", 1.8))
        for _ in range(max_turns):
            if result.get("end_conversation"):
                return
            # Silence OR filtered noise (music, typing captions) both mean
            # nobody is talking to her: hand the floor back to the wake word.
            if not str(result.get("heard") or "").strip() or not str(result.get("response") or "").strip():
                return
            asked_question = str(result.get("response") or "").strip().endswith("?")
            window = question_window if asked_question else base_window
            # brief grace so the speaker tail is not captured, then cue that
            # she is still listening
            await asyncio.sleep(0.3)
            self.thinking_sounds.cue()
            result = await self.one_turn(start_timeout=window, threshold_multiplier=multiplier)

    async def one_turn(
        self, start_timeout: float | None = None, threshold_multiplier: float = 1.0
    ) -> dict[str, Any]:
        # Never open the microphone while her own voice is still playing —
        # an ambient comment landing right as a capture window opened is how
        # her speech ended up transcribed as a user turn.
        while time.time() < getattr(self, "_speaking_until", 0.0):
            await asyncio.sleep(0.2)
        async with self._capture_lock:
            self._manual_capture_active.set()
            self._wake_cancel.set()
            await asyncio.sleep(0.05)
            try:
                return await self._capture_turn(start_timeout, threshold_multiplier)
            finally:
                self.thinking_sounds.stop()
                self._manual_capture_active.clear()
                self._wake_cancel.clear()

    async def desktop_capture_begin(self) -> dict[str, Any]:
        """Yield the microphone to the native desktop renderer."""
        self._manual_capture_active.set()
        self._wake_cancel.set()
        await asyncio.sleep(0.1)
        return {"ok": True, "microphone": "yielded-to-desktop"}

    async def desktop_capture_end(self) -> dict[str, Any]:
        self._manual_capture_active.clear()
        self._wake_cancel.clear()
        return {"ok": True}

    async def wake_loop(self) -> None:
        if not self.wake_enabled and not self.stop_enabled:
            while True:
                await asyncio.sleep(3600)
        while True:
            try:
                while self._manual_capture_active.is_set():
                    await asyncio.sleep(0.05)
                self._wake_cancel.clear()
                event = await asyncio.to_thread(
                    self.audio.wait_for_wake,
                    self.wake_provider,
                    self.stop_provider if self.stop_enabled else None,
                    self._wake_cancel,
                )
                self._wake_failures = 0
                if event == "cancel":
                    continue
                if time.time() < getattr(self, "_speaking_until", 0.0):
                    # That was her own voice reaching her own microphone.
                    continue
                if event == "stop":
                    await self._spoken_stop("spoken stop wake detector")
                    continue
                if event == "wake":
                    await self._conversation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # CoreAudio intermittently refuses new input streams under
                # churn or after a device change; a tight 1s retry loop makes
                # it worse. Back off, and after repeated failures force a full
                # device re-probe — the microphone may have moved or vanished.
                self._wake_failures = getattr(self, "_wake_failures", 0) + 1
                LOG.exception("Voice wake loop error (%d in a row): %s", self._wake_failures, exc)
                if self._wake_failures >= 3:
                    self.audio._device_ready = False
                    LOG.warning("Re-probing audio devices after repeated stream failures")
                    self._wake_failures = 0
                await asyncio.sleep(min(6.0, 1.0 + self._wake_failures * 2.0))

    async def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "health":
            return {
                "ok": True,
                "asr_provider": self.asr.provider_name,
                "asr_model_arch": self.asr.model_arch,
                "asr_ready": self.asr.available()[0],
                "asr_detail": self.asr.available()[1],
                "tts_model_exists": self.tts.model.exists(),
                "wake_provider": self.settings.get("voice.wake.provider", "disabled"),
                "wake_phrase": self.settings.get("voice.wake.phrase", "kendra"),
                "stop_wake_provider": self.settings.get("voice.stop_wake.provider", "disabled"),
                "stream_responses": self.stream_responses,
                "local_only": True,
            }
        if method == "speak":
            text = str(params["text"]).strip()
            if not text:
                raise ValueError("text cannot be empty")
            if bool(params.get("only_if_idle")) and (
                self._capture_lock.locked() or self._manual_capture_active.is_set()
            ):
                # Polite announcements (movement commentary, arrivals) never
                # talk over an active conversation — charter social conduct.
                return {"ok": False, "reason": "conversation_active"}
            await self._speak_with_barge_in(text, str(params.get("affect", "warm")))
            if bool(params.get("listen_after")):
                # She just spoke to someone unprompted (a curious question):
                # her ears open for the answer — Jonathan must never need
                # the wake word to reply to her own question.
                task = asyncio.create_task(
                    self._followup_loop({"heard": "(kendra spoke first)", "response": text}),
                    name="kendra-listen-after-speech",
                )
                task.add_done_callback(lambda _t: None)
            return {"ok": True}
        if method == "busy":
            # Is a conversation live right now? Ambient vision asks before
            # spending 20+ seconds of CPU on a describe: Moondream and Gemma
            # contending for cores was the "her sight takes forever" bug.
            return {
                "busy": self._capture_lock.locked()
                or self._manual_capture_active.is_set()
                or time.time() < getattr(self, "_speaking_until", 0.0)
            }
        if method == "listen_once":
            return await self.one_turn()
        if method == "desktop_capture_begin":
            return await self.desktop_capture_begin()
        if method == "desktop_capture_end":
            return await self.desktop_capture_end()
        if method == "transcribe":
            path = Path(str(params["path"])).expanduser().resolve()
            return {"text": await self.asr.transcribe(path)}
        if method == "stop_speaking":
            await self._spoken_stop("voice RPC stop")
            return {"ok": True}
        raise KeyError(f"Unknown voice method: {method}")

    async def run(self) -> None:
        await self.server.start()
        # Warm the KV cache at startup so the first turn of the day does not
        # pay the full cold prefill.
        warm_task = asyncio.create_task(self.streaming_agent.prewarm_conversation())
        warm_task.add_done_callback(lambda _t: None)
        ack_task = asyncio.create_task(self.acks.prepare())
        ack_task.add_done_callback(lambda _t: None)
        wake_task = asyncio.create_task(self.wake_loop())
        try:
            assert self.server.server is not None
            async with self.server.server:
                await self.server.server.serve_forever()
        finally:
            wake_task.cancel()
            self.tts.stop()


async def voice_console(settings: Settings) -> None:
    service = VoiceService(settings)
    while True:
        await asyncio.to_thread(input, "Press Enter to let Kendra listen, or Ctrl-C to stop... ")
        result = await service.one_turn()
        print(f"Heard: {result['heard']}\nKendra ({result.get('affect','neutral')}): {result['response']}")


def run(settings: Settings) -> None:
    asyncio.run(VoiceService(settings).run())
