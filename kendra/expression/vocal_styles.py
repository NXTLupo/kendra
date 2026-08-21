"""Voice colouring for expressive performances.

Kokoro-82M has no musical conditioning and cannot truly sing, so singing
and rapping are produced the way the model actually responds: by shaping
the LYRICS (elongated vowels, ellipses for pacing, repeated syllables) and
the CADENCE (speed and pitch), per Jonathan's Kokoro notes.

Each style maps onto the affect profiles the TTS layer already supports,
so nothing here reaches into the synthesiser internals.
"""

from __future__ import annotations

import re

# style -> (affect used for prosody, speed multiplier, guidance for the LLM)
STYLES: dict[str, tuple[str, float, str]] = {
    "normal":    ("warm",        1.00, "Speak naturally."),
    "warm":      ("warm",        0.98, "Speak warmly and unhurriedly."),
    "playful":   ("delighted",   1.06, "Speak with a grin in your voice."),
    "excited":   ("delighted",   1.12, "Speak quickly and brightly."),
    "dry":       ("neutral",     0.98, "Deadpan, understated."),
    "dramatic":  ("reflective",  0.88, "Slow, weighted, theatrical pauses."),
    "sleepy":    ("reflective",  0.86, "Soft, slow, trailing off."),
    "whisper":   ("concern",     0.92, "Hushed and confidential."),
    "singing":   ("delighted",   0.90, "Lyrical and melodic."),
    "humming":   ("reflective",  0.88, "Wordless, contented."),
    "rapping":   ("alert",       1.18, "Rhythmic, percussive, on the beat."),
}

_VOWEL_RUN = re.compile(r"([aeiou])\1{2,}", re.I)


def shape_text(text: str, style: str) -> str:
    """Rewrite words so an ordinary TTS reads them musically.

    Kokoro will not sing on request, but it WILL stretch what is written:
    elongated vowels and ellipses become pitch contour and pacing. This is
    the whole trick behind her singing and humming.
    """
    value = (text or "").strip()
    if style == "humming":
        # Nasal, wordless, gently varied so it is not one flat drone.
        return "Hmm... hmmm... mmm... hmmmm... mm-hmm..."
    if style == "singing":
        # Lyrics become singable: line breaks turn into breath pauses and
        # the final word of each line is stretched.
        lines = [ln.strip(" .,") for ln in re.split(r"[\n.]+", value) if ln.strip()]
        sung = []
        for line in lines:
            words = line.split()
            if words:
                words[-1] = _stretch(words[-1])
            sung.append(" ".join(words))
        return _tidy("... ".join(sung) + "...")
    if style == "rapping":
        # Short percussive clauses; commas force the beat.
        lines = [ln.strip(" .,") for ln in re.split(r"[\n.]+", value) if ln.strip()]
        return _tidy(", ".join(lines) + ".")
    if style == "whisper":
        return value
    return value


def _tidy(text: str) -> str:
    """No doubled punctuation: ",." was being read aloud as a stumble."""
    text = re.sub(r"\s*,\s*\.", ".", text)
    text = re.sub(r"([.,])\1+", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _stretch(word: str) -> str:
    """Lengthen a word's last vowel: 'shine' -> 'shiiine'."""
    match = None
    for m in re.finditer(r"[aeiou]", word, re.I):
        match = m
    if match is None:
        return word
    index = match.start()
    return word[:index] + word[index] * 3 + word[index + 1:]


def affect_for(style: str) -> str:
    return STYLES.get(style, STYLES["normal"])[0]


def speed_for(style: str) -> float:
    return STYLES.get(style, STYLES["normal"])[1]


def guidance_for(style: str) -> str:
    return STYLES.get(style, STYLES["normal"])[2]
