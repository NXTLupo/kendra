from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from ..config import Settings
from ..paths import resolve_path


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


class DisabledWakeWord:
    def predict(self, pcm16: np.ndarray) -> float:
        return 0.0


class OpenWakeWordProvider:
    def __init__(self, settings: Settings, section: str = "voice.wake"):
        try:
            from openwakeword.model import Model
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        paths = [str(resolve_path(path, settings.root)) for path in settings.get(f"{section}.model_paths", [])]
        if not paths:
            raise RuntimeError(f"{section} is enabled but no local model_paths are configured")
        self.model = Model(wakeword_models=paths, inference_framework="onnx")

    def predict(self, pcm16: np.ndarray) -> float:
        prediction: dict[str, Any] = self.model.predict(pcm16)
        return max((float(value) for value in prediction.values()), default=0.0)


class VoskKeywordProvider:
    """Fully local phrase detector used for the default 'Kendra' wake phrase.

    Vosk is not a cloud service. The recognizer is constrained to a tiny local
    grammar so it can recognize a phrase without sending audio anywhere.
    """

    def __init__(self, settings: Settings, section: str = "voice.wake"):
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as exc:
            raise RuntimeError("Install the voice extra: pip install -e '.[voice]'") from exc
        SetLogLevel(-1)
        model_path = resolve_path(settings.require(f"{section}.vosk_model"), settings.root)
        if not model_path.exists():
            raise FileNotFoundError(f"Vosk model not found: {model_path}")
        phrase = _normalize(str(settings.require(f"{section}.phrase")))
        alternates = [_normalize(str(v)) for v in settings.get(f"{section}.alternate_phrases", [])]
        self.phrases = [p for p in [phrase, *alternates] if p]
        self.sample_rate = int(settings.get("voice.capture.sample_rate", 16000))
        self._KaldiRecognizer = KaldiRecognizer
        self.model = Model(str(model_path))
        self._new_recognizer()

    def _new_recognizer(self) -> None:
        grammar = json.dumps([*self.phrases, "[unk]"])
        self.recognizer = self._KaldiRecognizer(self.model, self.sample_rate, grammar)

    def _contains_phrase(self, text: str) -> bool:
        value = _normalize(text)
        return any(phrase in value for phrase in self.phrases)

    def predict(self, pcm16: np.ndarray) -> float:
        raw = np.asarray(pcm16, dtype=np.int16).tobytes()
        matched = False
        if self.recognizer.AcceptWaveform(raw):
            result = json.loads(self.recognizer.Result() or "{}")
            matched = self._contains_phrase(str(result.get("text", "")))
        else:
            partial = json.loads(self.recognizer.PartialResult() or "{}")
            matched = self._contains_phrase(str(partial.get("partial", "")))
        if matched:
            self._new_recognizer()
            return 1.0
        return 0.0


def build_wake_provider(settings: Settings, section: str = "voice.wake"):
    provider = str(settings.get(f"{section}.provider", "disabled"))
    if provider == "disabled":
        return DisabledWakeWord()
    if provider == "openwakeword":
        return OpenWakeWordProvider(settings, section)
    if provider == "vosk_keyword":
        return VoskKeywordProvider(settings, section)
    raise ValueError(f"Unknown wake provider: {provider}")
