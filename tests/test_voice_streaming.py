import wave

import pytest

from kendra.voice.asr import MoonshineASR
from kendra.voice.streaming import PhraseAccumulator


def test_phrase_accumulator_releases_clause_early():
    buffer = PhraseAccumulator(min_chars=12, max_chars=80)
    assert buffer.feed("Hello Jonathan, ") == ["Hello Jonathan,"]
    assert buffer.feed("I am still generating the rest") == []
    assert buffer.flush() == "I am still generating the rest"


def test_phrase_accumulator_forces_long_unpunctuated_output():
    buffer = PhraseAccumulator(min_chars=10, max_chars=24)
    ready = buffer.feed("one two three four five six seven eight")
    assert ready
    assert all(len(item) <= 24 for item in ready)


@pytest.mark.asyncio
async def test_moonshine_skips_empty_capture(settings, tmp_path):
    wav_path = tmp_path / "empty.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)

    assert await MoonshineASR(settings).transcribe(wav_path) == ""
