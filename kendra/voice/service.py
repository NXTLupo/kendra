from __future__ import annotations

import asyncio
import contextlib
import hashlib
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


_NAME_PATTERN = re.compile(
    r"(?:my name(?:'s| is)|i'?m|i am|it'?s|call me|this is|they call me)\s+"
    r"([A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?)",
    re.I,
)
_NOT_NAMES = {
    "no", "yes", "nothing", "nobody", "what", "why", "who", "hey", "hi",
    "hello", "okay", "ok", "sorry", "stop", "kendra", "sure", "none",
}


# Household-name transliterations: ASR renders unfamiliar names phonetically
# ("Peiyi" arrived as "a payee"). Alias map fixes them deterministically.
_NAME_ALIASES = {
    "payee": "Peiyi", "pay e": "Peiyi", "payi": "Peiyi", "pei yi": "Peiyi",
    "pay yee": "Peiyi", "peggy e": "Peiyi", "pey e": "Peiyi",
}


def _extract_name(text: str) -> str | None:
    """Pull a plausible human name out of a spoken introduction."""
    stripped = text.strip().strip(".!?,")
    match = _NAME_PATTERN.search(stripped)
    if match:
        candidate = match.group(1)
    else:
        words = stripped.split()
        if not 1 <= len(words) <= 2 or not all(w.isalpha() for w in words):
            return None
        candidate = stripped
    if candidate.split()[0].casefold() in _NOT_NAMES:
        return None
    alias = _NAME_ALIASES.get(candidate.casefold())
    if alias:
        return alias
    return " ".join(w.title() for w in candidate.split())


_RESEARCH_WORDS = re.compile(
    r"\b(research|look\s+(?:it|that|this)?\s*up|search|google|find out|"
    r"news|headlines?|weather|forecast|what'?s happening|current|latest)\b",
    re.I,
)


def _turn_mode(text: str) -> str:
    """Which tone family fits this turn — heard before she answers.

    Cheap local regexes (the planner's own routing shapes), so the palette
    switches with zero added latency and matches what she is actually
    about to do.
    """
    if AgentRuntime._WHO_QUESTION.search(text) or AgentRuntime._SIGHT_INTENT.search(text):
        return "sight"
    if AgentRuntime._FORCE_RESEARCH.search(text) or _RESEARCH_WORDS.search(text):
        return "research"
    return "think"


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

    PRERENDER = (
        ("Let me take a look right now.", "curious"),
        ("On it — give me a moment to actually search.", "warm"),
        ("Loud and clear.", "warm"),
        ("Yep, I hear you.", "warm"),
        ("I hear you just fine. What's up?", "warm"),
        ("Right here. Go ahead.", "warm"),
        ("Okay.", "warm"),
        ("Oh, hi! I don't think we've met yet — I'm Kendra. What's your name?", "delighted"),
    )

    async def prerender_phrases(self) -> int:
        """Synthesize her fixed lines once at boot (ELC voiceText pattern).

        Kokoro runs near real time on this CPU, so every canned
        acknowledgment cost ~1.5 s of dead air before she made a sound.
        Pre-rendered WAVs play in milliseconds; identical win on the Pi,
        where synthesis is slower still.
        """
        directory = self.settings.runtime_dir / "phrases"
        directory.mkdir(parents=True, exist_ok=True)
        self._phrase_cache: dict[tuple[str, str], Path] = {}
        made = 0
        for text, affect in self.PRERENDER:
            digest = hashlib.sha256(f"{text}|{affect}".encode()).hexdigest()[:16]
            path = directory / f"{digest}.wav"
            if not path.exists():
                try:
                    await self.tts.synthesize(text, path, affect=affect)
                    made += 1
                except Exception:
                    LOG.debug("Could not pre-render %r", text[:40], exc_info=True)
                    continue
            self._phrase_cache[(text.strip(), affect)] = path
        LOG.info("Pre-rendered %d phrases (%d cached)", made, len(self._phrase_cache))
        return made

    def _leds(self, **fields: object) -> None:
        """Fire-and-forget light state — her body must never wait on LEDs.

        On the robot these are the two WS2812 modules; on the desktop the
        driver is a no-op, so the same calls run on both bodies.
        """
        async def send() -> None:
            try:
                client = UnixJsonClient(self.settings.runtime_dir / "leds.sock", timeout=3)
                await client.call("system", fields)
            except Exception:
                LOG.debug("LED update unavailable", exc_info=True)

        task = asyncio.create_task(send(), name="kendra-leds")
        task.add_done_callback(lambda _t: None)

    def _play_cached(self, text: str, affect: str) -> bool:
        path = getattr(self, "_phrase_cache", {}).get((text.strip(), affect))
        if path is None or not path.exists():
            return False
        try:
            import wave

            import numpy as np
            import sounddevice as sd

            with wave.open(str(path), "rb") as clip:
                rate = clip.getframerate()
                frames = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
            sd.play(frames, rate)
            sd.wait()
            return True
        except Exception:
            LOG.debug("Cached phrase playback failed", exc_info=True)
            return False

    async def _spoken_stop(self, reason: str) -> None:
        self.tts.stop()
        try:
            await self.body.call("stop", {"reason": reason})
        except Exception:
            LOG.exception("Spoken stop could not reach body service")

    async def _speak_with_barge_in(self, text: str, affect: str) -> bool:
        """Speak locally while listening for Kendra's secondary spoken stop."""

        self.thinking_sounds.stop()
        self._leds(thinking=False)
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
            if not await asyncio.to_thread(self._play_cached, text, affect):
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
        turn_started = time.time()
        while True:
            item = await queue.get()
            if item is None:
                break
            phrase, affect = item
            if not spoken_phrases:
                # ELC perceived-latency budget: first-audio-out is THE felt
                # metric; target <1.5s once the voice LoRA lands.
                LOG.info("First audio out in %.1fs", time.time() - turn_started)
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
                # never reads as a hang. start(), not cue() — cue is one
                # single listening tone, and using it here left the whole
                # 10-60s sight/research stretch after the ack in silence.
                # The loop stops when the next phrase speaks (L97) and
                # self-caps at 90s; its 0.9s onset means a quickly-arriving
                # next phrase never blips at all.
                self.thinking_sounds.start()

        if was_interrupted:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer
            # Barge-in truth (ELC interrupted-flag): the cancelled generation
            # was never stored, so record what Jonathan ACTUALLY heard —
            # otherwise the exchange vanishes from her history entirely.
            heard_text = " ".join(spoken_phrases).strip()
            if heard_text:
                async def store_truncated() -> None:
                    try:
                        brain = UnixJsonClient(self.settings.runtime_dir / "brain.sock", timeout=10)
                        await brain.call("turn", {
                            "session_id": "voice-interrupted",
                            "user_text": user_text,
                            "kendra_text": heard_text + " —(Jonathan interrupted; the rest went unspoken)",
                            "metadata": {"source": "voice", "interrupted": True},
                        })
                    except Exception:
                        LOG.debug("Could not store interrupted turn", exc_info=True)
                task = asyncio.create_task(store_truncated(), name="kendra-interrupted-turn")
                task.add_done_callback(lambda _t: None)
            return {
                "text": heard_text,
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
                self._leds(thinking=True, thinking_mode="think")
            user_text = await self.asr.transcribe(wav)
            if not user_text or _is_noise_caption(user_text):
                return {"heard": user_text, "response": ""}
            user_text = _strip_wake_prefix(
                user_text,
                [str(self.settings.get("voice.wake.phrase", "kendra")).casefold()],
            )
            # Distinct tones per kind of work: he hears what she is doing.
            mode = _turn_mode(user_text)
            self.thinking_sounds.set_mode(mode)
            self._leds(thinking=True, thinking_mode=mode)
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
            # Speaker-correction reflex: "I'm not Jonathan, I'm Steph" means
            # a new person is talking to her — greet and enroll them NOW,
            # with the name they just gave. Pure voice+face path, Pi-native.
            correction = re.search(
                r"(?:it'?s|this is|i'?m|i am) not jonathan\W*(?:it'?s|this is|i'?m|i am|my name is)?\s*([A-Za-z][a-z]+)?"
                r"|not jonathan\W+(?:i'?m|i am|it'?s|this is|my name is)\s+([A-Za-z][a-z]+)",
                lowered,
            )
            if correction:
                given = next((g for g in correction.groups() if g), None)
                name = _extract_name(given.title()) if given else None
                await self._speak_with_barge_in(
                    f"Oh! I'm so sorry — it's lovely to meet you{', ' + name if name else ''}!",
                    "delighted",
                )
                task = asyncio.create_task(
                    self._meet_person(known_name=name),
                    name="kendra-meet-correction",
                )
                task.add_done_callback(lambda _t: None)
                return {"heard": user_text, "response": "meeting a new person"}
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
                    "meet_person": bool(result.get("meet_person")),
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
        if result.get("meet_person"):
            # "Who is that?" found an unfamiliar face: the conversation
            # BECOMES the introduction.
            await self._meet_person()
            return
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
            if result.get("meet_person"):
                await self._meet_person()
                return

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

    async def _capture_transcript(self, start_timeout: float = 14.0) -> str:
        """Capture and transcribe one utterance WITHOUT running an agent turn.

        The meet ritual needs the raw answer to "what's your name?" — routing
        it through the planner would generate a whole conversational reply.
        """
        while time.time() < getattr(self, "_speaking_until", 0.0):
            await asyncio.sleep(0.2)
        async with self._capture_lock:
            self._manual_capture_active.set()
            self._wake_cancel.set()
            await asyncio.sleep(0.05)
            try:
                with tempfile.TemporaryDirectory(prefix="kendra-meet-") as directory:
                    wav = Path(directory) / "input.wav"
                    await asyncio.to_thread(
                        self.audio.capture_utterance,
                        wav,
                        start_timeout,
                        1.0,
                        self.thinking_sounds.stop,
                    )
                    if not self.audio.last_capture_speech:
                        return ""
                    return (await self.asr.transcribe(wav)).strip()
            except Exception:
                LOG.exception("Meet-ritual capture failed")
                return ""
            finally:
                self._manual_capture_active.clear()
                self._wake_cancel.clear()

    async def _meet_person(self, noticed: str = "", known_name: str | None = None) -> dict[str, Any]:
        """Her reflex when an unfamiliar face appears: walk over (the vision
        service already did), introduce herself, learn the name, celebrate,
        and remember the person everywhere — identity, brain, second brain."""
        if known_name:
            heard, name = known_name, known_name
        else:
            await self._speak_with_barge_in(
                "Oh, hi! I don't think we've met yet — I'm Kendra. What's your name?",
                "delighted",
            )
            heard = await self._capture_transcript(14.0)
            name = _extract_name(heard)
        if not name:
            await self._speak_with_barge_in(
                "No worries — it's lovely to see you anyway!", "warm"
            )
            return {"ok": False, "reason": "no_name", "heard": heard}
        await self._speak_with_barge_in(f"It was so nice to meet you, {name}!", "delighted")
        # Cute flourish: warm lights and a stretch — simulated on the desktop,
        # WS2812 ring and real servos on the robot body, same calls.
        for sock, method, payload in (
            ("leds.sock", "express", {"state": "warm"}),
            ("body.sock", "pose", {"name": "stretch"}),
        ):
            try:
                await UnixJsonClient(self.settings.runtime_dir / sock, timeout=5).call(
                    method, payload
                )
            except Exception:
                LOG.debug("Meet flourish (%s) unavailable", sock, exc_info=True)
        async def enroll_and_store() -> None:
            # The 8 spaced captures (~10s) and storage run BEHIND the
            # conversation — blocking on them left a painful silent stall
            # right after "nice to meet you". Her eyes learn the face while
            # her mouth keeps talking; the person naturally stays in frame.
            enroll: dict[str, Any] = {}
            try:
                vision = UnixJsonClient(self.settings.runtime_dir / "vision.sock", timeout=90)
                enroll = await vision.call(
                    "enroll_person",
                    {"name": name, "consent": True, "relationship": "met in person"},
                )
            except Exception:
                LOG.exception("Meet ritual: face enrollment failed for %s", name)
            try:
                brain = UnixJsonClient(self.settings.runtime_dir / "brain.sock", timeout=10)
                await brain.call(
                    "meet_person",
                    {"name": name, "person_uid": (enroll or {}).get("person_uid")},
                )
            except Exception:
                LOG.exception("Meet ritual: brain storage failed for %s", name)
            LOG.info("Meet ritual storage complete: %s (%s)", name, enroll.get("person_uid"))

        enroll_task = asyncio.create_task(enroll_and_store(), name="kendra-meet-enroll")
        enroll_task.add_done_callback(lambda _t: None)
        LOG.info("Meet ritual conversation continuing with %s", name)
        # She is inquisitive by nature: try to NOTICE something about the
        # person — clothing, an item, an instrument — and ask about that.
        # Falls back to warm small talk when her eyes or the model come up
        # short. One bounded call on the tool slot, never the conversation
        # cache (slot 0 is sacred).
        question = None
        try:
            if noticed:
                candidate = (await self.streaming_agent.llm.chat(
                    [
                        {"role": "system", "content": (
                            "You are Kendra, a warm, extremely curious robot companion. "
                            "Reply with ONE spoken question of at most 14 words, addressed "
                            "directly as 'you', about something specific you noticed. No preamble."
                        )},
                        {"role": "user", "content": f"You just met {name}. You notice: {noticed[:280]}"},
                    ],
                    max_tokens=30,
                    temperature=0.8,
                    id_slot=1,
                )).strip().strip('"')
                if candidate and "?" in candidate:
                    question = candidate
        except Exception:
            LOG.debug("Meet ritual: noticing question unavailable", exc_info=True)
        if not question:
            question = random.choice([
                f"So {name}, what brings you by today?",
                f"{name}, how do you know Jonathan?",
                f"I'm curious about basically everything, {name} — what are you into?",
            ])
        await self._speak_with_barge_in(question, "curious")
        await self._followup_loop({"heard": f"(just met {name})", "response": question})
        return {"ok": True, "name": name}

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
                "vad_threshold": float(getattr(self.audio.vad, "threshold_rms", 0.0)),
                "heard_speech_recently": bool(getattr(self.audio, "last_capture_speech", False)),
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
        if method == "meet_person":
            # Reflex greeting for an unfamiliar face. Runs as a background
            # task so the vision service is never blocked; refuses politely
            # when a conversation is already happening.
            if self._capture_lock.locked() or self._manual_capture_active.is_set():
                return {"ok": False, "reason": "conversation_active"}
            task = asyncio.create_task(
                self._meet_person(str(params.get("noticed") or "")),
                name="kendra-meet-person",
            )
            task.add_done_callback(lambda _t: None)
            return {"ok": True, "started": True}
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
        phrase_task = asyncio.create_task(self.prerender_phrases())
        phrase_task.add_done_callback(lambda _t: None)
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
