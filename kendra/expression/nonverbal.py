"""Synthesized nonverbal vocalizations — hums, sighs, small sounds.

Kokoro spells "Hmm" out loud as H-M-M because its phonemizer has no
grapheme for a closed-mouth hum. Text is the wrong medium for a wordless
sound, so these are generated directly as waveforms: a warm fundamental
with a nasal harmonic stack, gentle vibrato, and a soft envelope.

Pure numpy, so it costs nothing, needs no model, and behaves identically
on the Pi. Several contours exist because one repeated hum is exactly how
a companion becomes irritating.
"""

from __future__ import annotations

import logging

import numpy as np

LOG = logging.getLogger(__name__)

SAMPLE_RATE = 22050

# Small melodic contours: (semitone offset, seconds) relative to the base.
CONTOURS: dict[str, list[tuple[float, float]]] = {
    # Falling — "I'm mulling this over".
    "thoughtful": [(0, 0.55), (-2, 0.75)],
    # Rising, brighter — pleased.
    "happy": [(0, 0.35), (4, 0.35), (7, 0.55)],
    # Single rising note — "hm?".
    "questioning": [(0, 0.28), (5, 0.42)],
    # Steady and warm, held.
    "contented": [(0, 1.15)],
    # Playful little skip.
    "mischievous": [(0, 0.25), (3, 0.22), (0, 0.3), (5, 0.45)],
}


def _note(freq: float, seconds: float, volume: float) -> np.ndarray:
    t = np.linspace(0.0, seconds, int(SAMPLE_RATE * seconds), endpoint=False)
    # Vibrato: a hum is never perfectly steady.
    vibrato = 1.0 + 0.006 * np.sin(2 * np.pi * 5.2 * t)
    phase = 2 * np.pi * freq * vibrato * t
    # Closed-mouth timbre: strong fundamental, present second harmonic,
    # quiet third; higher partials are what make a voice sound open.
    wave = (
        1.00 * np.sin(phase)
        + 0.38 * np.sin(2 * phase)
        + 0.12 * np.sin(3 * phase)
        + 0.04 * np.sin(4 * phase)
    )
    # Soft attack and release so notes join instead of clicking.
    attack = np.minimum(t / 0.06, 1.0)
    release = np.minimum((seconds - t) / 0.10, 1.0)
    envelope = np.clip(attack * release, 0.0, 1.0)
    return wave * envelope * volume


def hum(style: str = "thoughtful", base_hz: float = 233.0, volume: float = 0.20) -> np.ndarray:
    """One hummed phrase as int16 PCM."""
    contour = CONTOURS.get(style) or CONTOURS["thoughtful"]
    parts = [_note(base_hz * (2 ** (semitones / 12.0)), seconds, volume)
             for semitones, seconds in contour]
    audio = np.concatenate(parts)
    peak = float(np.max(np.abs(audio))) or 1.0
    audio = audio / peak * volume
    return (audio * 32767).astype(np.int16)


def play(audio: np.ndarray) -> None:
    """Blocking playback, so a performance can be timed around it."""
    try:
        import sounddevice as sd

        sd.play(audio, SAMPLE_RATE)
        sd.wait()
    except Exception:
        LOG.debug("nonverbal playback unavailable", exc_info=True)


# --------------------------------------------------------------- singing
# A melody is a sequence of pitch multipliers applied to successive sung
# lines. Kokoro renders one flat intonation per utterance, so the melody
# has to be imposed afterwards by resampling each line — the same
# mechanism as her existing pitch knob, applied per phrase instead of
# globally. Resampling shifts speed with pitch, which is exactly the
# character of talk-singing.
MELODIES: dict[str, list[float]] = {
    # Gentle rise and fall — nursery-rhyme shaped.
    "simple": [1.00, 1.12, 1.26, 1.12, 1.00, 0.94, 1.00],
    # Brighter, more excited.
    "bright": [1.06, 1.19, 1.26, 1.19, 1.33, 1.19, 1.06],
    # Low and slow, a lullaby.
    "lullaby": [0.94, 1.00, 0.94, 0.89, 0.94, 1.00, 0.94],
}


def pitch_shift(audio: np.ndarray, factor: float) -> np.ndarray:
    """Resample to move pitch. Speed moves with it, as in talk-singing."""
    if abs(factor - 1.0) < 0.01 or audio.size == 0:
        return audio
    source = np.arange(audio.size, dtype=np.float64)
    target = np.arange(0, audio.size, factor, dtype=np.float64)
    shifted = np.interp(target, source, audio.astype(np.float64))
    return shifted.astype(np.int16)


# Notes WITHIN a line, in semitones. One pitch per line only produced
# spoken word at varying pitches; a voice is heard as singing when the
# pitch moves across the phrase and the last note is held.
PHRASE_NOTES: dict[str, list[float]] = {
    "simple":  [0, 2, 4, 2, 0, -1, 0],
    "bright":  [0, 4, 7, 4, 7, 9, 7],
    "lullaby": [0, -2, -3, -2, 0, -2, -3],
}


def sing_line(audio: np.ndarray, notes: list[float], hold_end: float = 1.35) -> np.ndarray:
    """Bend one spoken line into a sung phrase.

    Resampling at a VARYING rate moves the pitch continuously through the
    line instead of transposing it as a block, and slowing the tail holds
    the final note the way a singer does.
    """
    if audio.size == 0:
        return audio
    length = audio.size
    # A rate curve across the line, one step per note, smoothly interpolated.
    steps = np.array([2 ** (semitone / 12.0) for semitone in notes], dtype=np.float64)
    curve = np.interp(
        np.linspace(0, len(steps) - 1, num=length),
        np.arange(len(steps)),
        steps,
    )
    # Hold the last note: ease the rate down over the final fifth.
    tail = max(1, length // 5)
    curve[-tail:] *= np.linspace(1.0, 1.0 / hold_end, tail)
    # Variable-rate resampling: walk the source at the rate the curve asks.
    positions = np.cumsum(curve)
    positions = positions[positions < length - 1]
    if positions.size == 0:
        return audio
    return np.interp(positions, np.arange(length), audio.astype(np.float64)).astype(np.int16)


def apply_melody(lines_audio: list[np.ndarray], melody: str = "simple") -> np.ndarray:
    """Give a sequence of spoken lines a rising-and-falling contour."""
    pattern = MELODIES.get(melody) or MELODIES["simple"]
    notes = PHRASE_NOTES.get(melody) or PHRASE_NOTES["simple"]
    gap = np.zeros(int(SAMPLE_RATE * 0.16), dtype=np.int16)
    pieces: list[np.ndarray] = []
    for index, audio in enumerate(lines_audio):
        if audio.size == 0:
            continue
        # Melody within the line, then the line's own place in the tune:
        # verses rise and fall against each other as well as internally.
        phrase = sing_line(audio, [n + (index % 2) * 2 for n in notes])
        pieces.append(pitch_shift(phrase, pattern[index % len(pattern)]))
        pieces.append(gap)
    if not pieces:
        return np.zeros(0, dtype=np.int16)
    return np.concatenate(pieces)


# ----------------------------------------------------------------- music
# Her own instrument: the same warm tone voice as the thinking blips,
# played as actual tunes. No model and no TTS, so "play me something" is
# instant and sounds identical on the Pi.
_A4 = 440.0
_SCALE = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}


def _pitch(note: str) -> float:
    """'C5' / 'A4' / 'F#4' -> hertz."""
    letter, sharp, octave = note[0].upper(), "#" in note, int(note[-1])
    semitones = _SCALE[letter] + (1 if sharp else 0) + (octave - 4) * 12
    return _A4 * (2 ** (semitones / 12.0))


# (note, beats) — short original phrases with a little rhythm.
TUNES: dict[str, list[tuple[str, float]]] = {
    "little_wander": [("C5", 1), ("E5", 1), ("G5", 1), ("E5", 1),
                      ("F5", 1), ("D5", 1), ("C5", 2)],
    "curious_climb": [("D5", 0.5), ("E5", 0.5), ("F5", 1), ("A5", 1),
                      ("G5", 0.5), ("F5", 0.5), ("E5", 2)],
    "sleepy_drift":  [("A4", 1.5), ("G4", 0.5), ("F4", 1), ("E4", 1),
                      ("D4", 1.5), ("C4", 2.5)],
    "happy_skip":    [("G4", 0.5), ("C5", 0.5), ("E5", 0.5), ("G5", 0.5),
                      ("E5", 0.5), ("C5", 0.5), ("D5", 1), ("C5", 1.5)],
}


def play_tune(name: str = "little_wander", bpm: int = 108, volume: float = 0.18) -> np.ndarray:
    """Render one tune to PCM using her warm tone voice."""
    tune = TUNES.get(name) or TUNES["little_wander"]
    beat = 60.0 / max(40, bpm)
    pieces: list[np.ndarray] = []
    for note, beats in tune:
        seconds = beat * beats
        pieces.append(_note(_pitch(note), seconds, volume))
        # A breath between notes so they articulate instead of smearing.
        pieces.append(np.zeros(int(SAMPLE_RATE * 0.035)))
    audio = np.concatenate(pieces)
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak * volume * 32767).astype(np.int16)


# ------------------------------------------------------------- her singing
# Jonathan's correction, and it is the right one: speeding speech up and
# down is not singing. Her singing voice is the synthesized tone voice —
# the same nasal, vibrato timbre as her humming — carrying an actual tune.
# She is a robot; this IS her voice, not an imitation of a human one.
#
# Songs are built from a contour of scale degrees over a simple rhythm, so
# every performance is a different tune rather than one canned jingle.
SONG_SHAPES: dict[str, list[tuple[int, float]]] = {
    # (scale degree, beats) over a major scale
    "bright":   [(0,1),(2,1),(4,1),(2,1),(5,1),(4,1),(2,2),
                 (4,1),(5,1),(7,1),(5,1),(4,2)],
    "wistful":  [(5,1.5),(4,0.5),(2,1),(0,1),(2,1),(4,1.5),(2,0.5),(0,2)],
    "playful":  [(0,0.5),(2,0.5),(4,0.5),(5,0.5),(4,0.5),(2,0.5),(4,1),
                 (7,0.5),(5,0.5),(4,1),(2,1.5)],
    "lullaby":  [(4,1.5),(2,0.5),(0,2),(2,1),(4,1),(2,1.5),(0,2.5)],
}
_MAJOR = [0, 2, 4, 5, 7, 9, 11, 12]


def sing_melody(shape: str = "bright", root_hz: float = 293.66,
                bpm: int = 96, volume: float = 0.20) -> np.ndarray:
    """Her singing voice: a tune in her own tone, with breath between phrases."""
    contour = SONG_SHAPES.get(shape) or SONG_SHAPES["bright"]
    beat = 60.0 / max(50, bpm)
    pieces: list[np.ndarray] = []
    for index, (degree, beats) in enumerate(contour):
        semitones = _MAJOR[degree % len(_MAJOR)] + 12 * (degree // len(_MAJOR))
        freq = root_hz * (2 ** (semitones / 12.0))
        seconds = beat * beats
        # Held notes get a longer decay so they sing rather than blip.
        pieces.append(_note(freq, seconds, volume))
        # A small breath every few notes, as a singer phrases.
        if index and index % 4 == 3:
            pieces.append(np.zeros(int(SAMPLE_RATE * 0.14)))
        else:
            pieces.append(np.zeros(int(SAMPLE_RATE * 0.02)))
    audio = np.concatenate(pieces)
    peak = float(np.max(np.abs(audio))) or 1.0
    return (audio / peak * volume * 32767).astype(np.int16)
