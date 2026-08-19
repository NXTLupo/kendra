"""Provider selection for her voice organs: explicit, never silent."""

from __future__ import annotations

import pytest

from kendra.config import Settings
from kendra.voice.asr import MoonshineOnnxASR, ParakeetOnnxASR, build_asr
from kendra.voice.tts import KokoroTTS, PiperTTS, create_tts


def _with(settings: Settings, key: str, value: str) -> Settings:
    section, leaf = key.rsplit(".", 1)
    node = settings.data
    for part in section.split("."):
        node = node.setdefault(part, {})
    node[leaf] = value
    return settings


def test_tts_defaults_to_kokoro(settings: Settings) -> None:
    # Jonathan adopted Kokoro as her voice on 2026-08-19 after A/B listening.
    assert isinstance(create_tts(settings), KokoroTTS)


def test_tts_piper_selectable(settings: Settings) -> None:
    # Piper stays the instant rollback and the Pi qualification fallback.
    _with(settings, "voice.tts.provider", "piper")
    assert isinstance(create_tts(settings), PiperTTS)


def test_tts_unknown_provider_is_loud(settings: Settings) -> None:
    _with(settings, "voice.tts.provider", "espeak")
    with pytest.raises(ValueError, match="voice.tts.provider"):
        create_tts(settings)


def test_asr_default_is_parakeet(settings: Settings) -> None:
    assert isinstance(build_asr(settings), ParakeetOnnxASR)


def test_asr_moonshine_onnx_selectable(settings: Settings) -> None:
    _with(settings, "voice.asr.provider", "moonshine_onnx")
    asr = build_asr(settings)
    assert isinstance(asr, MoonshineOnnxASR)
    # Falls back to whisper availability reporting when models are absent —
    # never a hard crash in the middle of a spoken turn.
    ok, _reason = asr.available()
    assert isinstance(ok, bool)
