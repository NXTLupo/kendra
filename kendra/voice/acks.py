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
    """Soft ambient blips while Kendra works — three distinct voices.

    Jonathan asked to HEAR what she is doing, not just that she is busy.
    Each palette is synthesized with numpy at startup (no assets, no
    network, same stack on the Pi) and deliberately differentiated by
    register, contour, and pace rather than volume:

    - think    : the familiar warm mid pair (G4 -> C5) and a low E4.
                 Steady 1.6s pulse — "I'm composing an answer."
    - research : low, slow, outward-reaching. A rising fifth (D3 -> A3)
                 with a quiet distant echo, and a lone D3. Sparse 2.2s
                 pulse — "I'm off somewhere fetching this."
    - sight    : bright, quick, attentive. A close two-note lens tick
                 (C5 -> E5) and a single soft E5, short decay. Brisk 1.2s
                 pulse — "I'm looking right now."

    Volume stays uniform and low so the difference reads as character,
    not loudness. start()/set_mode() switch palettes mid-loop without
    restarting the cadence.
    """

    def __init__(self, settings: Settings):
        self.enabled = bool(settings.get("voice.thinking_sounds.enabled", True))
        self.interval = float(settings.get("voice.thinking_sounds.interval_seconds", 1.6))
        self.volume = float(settings.get("voice.thinking_sounds.volume", 0.12))
        self.sample_rate = 22050
        self._task = None
        self._mode = "think"
        self._palettes: dict[str, list[np.ndarray]] = self._build_palettes() if self.enabled else {}
        self._intervals = {
            "think": self.interval,
            "research": self.interval * 1.4,
            "sight": self.interval * 0.75,
        }

    def _tone(self, f0: float, seconds: float = 0.22, gain: float = 1.0, decay: float = 0.07) -> np.ndarray:
        t = np.linspace(0.0, seconds, int(self.sample_rate * seconds), endpoint=False)
        # Fundamental plus a quiet octave for warmth; fast attack, soft decay.
        wave_ = np.sin(2 * np.pi * f0 * t) + 0.35 * np.sin(2 * np.pi * f0 * 2 * t)
        envelope = np.minimum(t / 0.012, 1.0) * np.exp(-t / decay)
        pcm = wave_ * envelope * self.volume * gain
        return (pcm * 32767).astype(np.int16)

    def _gap(self, seconds: float) -> np.ndarray:
        return np.zeros(int(self.sample_rate * seconds), dtype=np.int16)

    def _build_palettes(self) -> dict[str, list[np.ndarray]]:
        think = [
            np.concatenate([self._tone(392.0), self._tone(523.25)]),   # G4 -> C5
            self._tone(329.63, seconds=0.28),                          # E4
        ]
        research = [
            # Rising fifth with a distant echo: reaching outward.
            np.concatenate([
                self._tone(146.83, seconds=0.26, decay=0.09),          # D3
                self._gap(0.05),
                self._tone(220.0, seconds=0.26, decay=0.09),           # A3
                self._gap(0.16),
                self._tone(220.0, seconds=0.20, gain=0.35, decay=0.06),  # echo
            ]),
            self._tone(146.83, seconds=0.30, gain=0.8, decay=0.10),    # lone D3
        ]
        sight = [
            # Quick bright lens tick: close, attentive, short decay.
            np.concatenate([
                self._tone(523.25, seconds=0.11, decay=0.035),         # C5
                self._gap(0.035),
                self._tone(659.25, seconds=0.13, decay=0.04),          # E5
            ]),
            self._tone(659.25, seconds=0.12, gain=0.7, decay=0.035),   # single E5
        ]
        return {"think": think, "research": research, "sight": sight}

    def set_mode(self, mode: str) -> None:
        """Switch palette mid-loop (no cadence restart, no extra latency)."""
        if mode in self._palettes:
            self._mode = mode

    def start(self, mode: str | None = None) -> None:
        if mode:
            self.set_mode(mode)
        if not self.enabled or not self._palettes:
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
                    blips = self._palettes.get(self._mode) or self._palettes["think"]
                    sd.play(blips[index % len(blips)], self.sample_rate)
                    index += 1
                    await asyncio.sleep(self._intervals.get(self._mode, self.interval))
                LOG.warning("Thinking sounds hit their 90s safety limit; going quiet")
            except asyncio.CancelledError:
                raise
            except Exception:
                LOG.debug("Thinking sounds unavailable", exc_info=True)

        self._task = asyncio.get_running_loop().create_task(loop())

    def cue(self, mode: str | None = None) -> None:
        """Single soft tone: the floor is open, Kendra is still listening."""
        if not self.enabled or not self._palettes:
            return
        try:
            import sounddevice as sd

            blips = self._palettes.get(mode or self._mode) or self._palettes["think"]
            sd.play(blips[-1], self.sample_rate)
        except Exception:
            LOG.debug("Listening cue unavailable", exc_info=True)

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
