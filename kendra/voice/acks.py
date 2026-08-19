from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import wave
from pathlib import Path

import numpy as np

from ..config import Settings

LOG = logging.getLogger(__name__)

# Short, colleague-register acknowledgments. Deliberately not servile: no
# "how can I help", no exclamation marks. They exist to prove Kendra heard
# you, not to please you.
DEFAULT_PHRASES = ["Hmm.", "Okay.", "Let me think.", "One sec.", "Good question."]


class AckPlayer:
    """Instant spoken acknowledgment while Kendra thinks.

    Measured perceived dead air on a voice turn is several seconds of ASR plus
    LLM prefill. A model can't answer faster than it answers, but Kendra can
    prove she heard you immediately: these clips are synthesized once with her
    own Piper voice, cached as WAVs, and played the moment audio capture ends,
    concurrently with transcription and generation. Playback is fire-and-forget
    and must never delay or break the real turn.

    Everything here is plain Python + Piper + sounddevice, so the identical
    code runs on the Intel iMac and the Raspberry Pi body.
    """

    def __init__(self, settings: Settings, tts):
        self.tts = tts
        self.enabled = bool(settings.get("voice.acks.enabled", True))
        phrases = settings.get("voice.acks.phrases", DEFAULT_PHRASES) or []
        self.phrases = [str(p).strip() for p in phrases if str(p).strip()][:12]
        self.affect = str(settings.get("voice.acks.affect", "curious"))
        self.directory = settings.runtime_dir / "acks"
        self._prepared = False

    def _path(self, phrase: str) -> Path:
        digest = hashlib.sha256(f"{self.affect}:{phrase}".encode()).hexdigest()[:16]
        return self.directory / f"ack-{digest}.wav"

    async def prepare(self) -> int:
        """Synthesize any missing clips. Cheap; safe to call every startup."""
        if not self.enabled or not self.phrases:
            return 0
        created = 0
        for phrase in self.phrases:
            path = self._path(phrase)
            if path.exists():
                continue
            try:
                await self.tts.synthesize(phrase, path, affect=self.affect)
                created += 1
            except Exception:
                LOG.exception("Could not pre-synthesize acknowledgment %r", phrase)
        self._prepared = True
        return created

    def play_random(self) -> None:
        """Play one cached clip without blocking. Silence on any failure."""
        if not self.enabled or not self.phrases:
            return
        try:
            import sounddevice as sd

            path = self._path(random.choice(self.phrases))
            if not path.exists():
                return
            with wave.open(str(path), "rb") as clip:
                rate = clip.getframerate()
                frames = np.frombuffer(clip.readframes(clip.getnframes()), dtype=np.int16)
            # Non-blocking by design: the ack overlaps ASR/LLM work and ends
            # long before the first real response phrase needs the speaker.
            sd.play(frames, rate)
        except Exception:
            LOG.debug("Acknowledgment playback skipped", exc_info=True)


class ThinkingSounds:
    """Soft ambient blips while Kendra thinks, so silence never reads as dead.

    Synthesized at startup with numpy — a warm two-partial tone with a gentle
    exponential decay, quiet by design (default 12%% full scale). No audio
    assets to provision, no network, and the same numpy + sounddevice stack
    the Raspberry Pi body uses. Runs as a background loop: start() when turn
    processing begins, stop() the moment speech is ready.
    """

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("voice.thinking_sounds.enabled", True))
        self.interval = float(settings.get("voice.thinking_sounds.interval_seconds", 1.6))
        self.volume = float(settings.get("voice.thinking_sounds.volume", 0.12))
        self.sample_rate = 22050
        self._task = None
        self._blips = self._synthesize() if self.enabled else []

    def _tone(self, f0: float, seconds: float = 0.22) -> np.ndarray:
        t = np.linspace(0.0, seconds, int(self.sample_rate * seconds), endpoint=False)
        # Fundamental plus a quiet octave for warmth; fast attack, soft decay.
        wave_ = np.sin(2 * np.pi * f0 * t) + 0.35 * np.sin(2 * np.pi * f0 * 2 * t)
        envelope = np.minimum(t / 0.012, 1.0) * np.exp(-t / 0.07)
        pcm = wave_ * envelope * self.volume
        return (pcm * 32767).astype(np.int16)

    def _synthesize(self) -> list[np.ndarray]:
        # A small rising pair and a single low tone, alternated: musical
        # enough to feel alive, sparse and quiet enough to never be annoying.
        pair = np.concatenate([self._tone(392.0), self._tone(523.25)])  # G4 -> C5
        low = self._tone(329.63, seconds=0.28)                          # E4
        return [pair, low]

    def start(self) -> None:
        if not self.enabled or self._blips is None or not self._blips:
            return
        if self._task is not None and not self._task.done():
            return

        async def loop() -> None:
            try:
                import sounddevice as sd

                index = 0
                await asyncio.sleep(0.9)  # instant replies never blip at all
                deadline = asyncio.get_running_loop().time() + 90.0
                while asyncio.get_running_loop().time() < deadline:
                    sd.play(self._blips[index % len(self._blips)], self.sample_rate)
                    index += 1
                    await asyncio.sleep(self.interval)
                LOG.warning("Thinking sounds hit their 90s safety limit; going quiet")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.debug("Thinking sounds unavailable", exc_info=True)

        self._task = asyncio.get_running_loop().create_task(loop())

    def cue(self) -> None:
        """Single soft tone: the floor is open, Kendra is still listening."""
        if not self.enabled or not self._blips:
            return
        try:
            import sounddevice as sd

            sd.play(self._blips[-1], self.sample_rate)
        except Exception:
            LOG.debug("Listening cue unavailable", exc_info=True)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
