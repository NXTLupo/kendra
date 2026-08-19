from __future__ import annotations

import asyncio
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..config import Settings
from ..paths import resolve_path

# Markdown emphasis and code fences that a text model emits but which have no
# spoken form. Piper would either read the punctuation aloud or stumble on it.
_MARKDOWN = re.compile(r"[*_`#]+")
_WHITESPACE = re.compile(r"\s+")


def speakable(text: str) -> str:
    """Strip anything that must never be spoken aloud.

    Emoji and pictographs are the important case: Piper pronounces them as
    their Unicode names or as noise, so a cheerful "Sure! 😊" becomes an
    audible artifact in the middle of Kendra's sentence. Voice output is
    speech, not a chat transcript.
    """
    cleaned = []
    for character in text:
        category = unicodedata.category(character)
        # So = symbol/other (emoji, pictographs, dingbats); Sk = modifier
        # symbols (skin tones, variation selectors); Cf = format controls
        # (zero-width joiner, which glues multi-part emoji together).
        if category in {"So", "Sk", "Cf", "Cs", "Co"}:
            cleaned.append(" ")
            continue
        cleaned.append(character)
    value = _MARKDOWN.sub(" ", "".join(cleaned))
    return _WHITESPACE.sub(" ", value).strip()


@dataclass(frozen=True, slots=True)
class AffectProfile:
    length_scale: float
    noise_scale: float
    noise_w_scale: float
    volume: float


AFFECTS: dict[str, AffectProfile] = {
    "neutral": AffectProfile(1.00, 0.667, 0.80, 0.95),
    "warm": AffectProfile(1.04, 0.70, 0.90, 1.00),
    "curious": AffectProfile(0.96, 0.72, 0.94, 1.00),
    "concern": AffectProfile(1.10, 0.58, 0.72, 0.92),
    "alert": AffectProfile(0.88, 0.50, 0.65, 1.05),
    "delighted": AffectProfile(0.92, 0.76, 1.00, 1.02),
    "reflective": AffectProfile(1.13, 0.60, 0.76, 0.94),
}


class PiperTTS:
    """Persistent, local Piper voice with chunk-streamed playback.

    The voice model is loaded once and kept in memory. Piper's Python streaming
    API yields audio chunks as they are synthesized, which avoids paying the
    latency of starting a subprocess and writing a complete WAV for every turn.
    Affect modifies local synthesis parameters only; no remote API is involved.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = resolve_path(settings.require("voice.tts.model"), settings.root)
        self.default_affect = str(settings.get("voice.tts.default_affect", "warm"))
        self._voice: Any | None = None
        self._load_lock = threading.Lock()
        self._speak_lock = asyncio.Lock()
        self._stop_event = threading.Event()

    def _load(self):
        if self._voice is not None:
            return self._voice
        if not self.model.exists():
            raise FileNotFoundError(f"Piper voice model not found: {self.model}")
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        with self._load_lock:
            if self._voice is None:
                self._voice = PiperVoice.load(str(self.model), use_cuda=False)
        return self._voice

    def _profile(self, affect: str | None) -> AffectProfile:
        return AFFECTS.get(str(affect or self.default_affect).lower(), AFFECTS["neutral"])

    def stop(self) -> None:
        self._stop_event.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass

    def _speak_blocking(self, text: str, affect: str | None) -> None:
        try:
            import sounddevice as sd
            from piper import SynthesisConfig
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        voice = self._load()
        profile = self._profile(affect)
        config = SynthesisConfig(
            length_scale=profile.length_scale,
            noise_scale=profile.noise_scale,
            noise_w_scale=profile.noise_w_scale,
            volume=profile.volume,
            normalize_audio=True,
        )
        self._stop_event.clear()
        stream = None
        try:
            for chunk in voice.synthesize(text, syn_config=config):
                if self._stop_event.is_set():
                    break
                audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
                if stream is None:
                    stream = sd.RawOutputStream(
                        samplerate=chunk.sample_rate,
                        channels=chunk.sample_channels,
                        dtype="int16",
                        blocksize=0,
                    )
                    stream.start()
                stream.write(audio.tobytes())
        finally:
            if stream is not None:
                stream.stop()
                stream.close()

    async def speak(self, text: str, affect: str | None = None) -> None:
        value = speakable(text)
        if not value:
            return
        async with self._speak_lock:
            await asyncio.to_thread(self._speak_blocking, value, affect)

    async def synthesize(self, text: str, output: Path, affect: str | None = None) -> Path:
        """Compatibility helper for tests/exports that need a WAV file."""
        try:
            import wave

            from piper import SynthesisConfig
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        voice = self._load()
        profile = self._profile(affect)
        config = SynthesisConfig(
            length_scale=profile.length_scale,
            noise_scale=profile.noise_scale,
            noise_w_scale=profile.noise_w_scale,
            volume=profile.volume,
            normalize_audio=True,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            voice.synthesize_wav(speakable(text), wav, syn_config=config)
        return output
