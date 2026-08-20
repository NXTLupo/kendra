from __future__ import annotations

import re


class PhraseAccumulator:
    """Turn streaming text deltas into speech-sized phrases.

    Piper starts as soon as a useful clause boundary is available instead of
    waiting for the whole answer. The accumulator avoids tiny fragments while
    enforcing a maximum buffer so a punctuation-free model output cannot stall
    speech indefinitely.
    """

    _boundary = re.compile(r"[.!?;:]\s|,\s")
    _sentence_end = re.compile(r"[.!?]\s")

    def __init__(self, min_chars: int = 28, max_chars: int = 140,
                 first_min_chars: int | None = None):
        self.min_chars = max(8, int(min_chars))
        self.max_chars = max(self.min_chars + 8, int(max_chars))
        # Time-to-first-audio is the felt latency; every later phrase is
        # hidden behind speech already playing. So the FIRST phrase may be
        # much shorter — "Oh, absolutely." starts her talking a second
        # sooner — while later phrases keep their length for prosody.
        self.first_min_chars = max(6, int(first_min_chars if first_min_chars is not None else 14))
        self.emitted = 0
        self.buffer = ""

    @property
    def _active_min(self) -> int:
        return self.first_min_chars if self.emitted == 0 else self.min_chars

    def feed(self, delta: str) -> list[str]:
        if not delta:
            return []
        self.buffer += delta
        ready: list[str] = []
        while True:
            cut = self._find_boundary()
            if cut is None:
                if len(self.buffer) < self.max_chars:
                    break
                cut = self._fallback_cut()
            phrase = self.buffer[:cut].strip()
            self.buffer = self.buffer[cut:].lstrip()
            if phrase:
                self.emitted += 1
                ready.append(phrase)
        return ready

    def _find_boundary(self) -> int | None:
        # A finished sentence is speakable immediately, however short: holding
        # "Hey." until min_chars accumulate means a short reply is not spoken
        # until the whole generation ends, which is exactly the first-packet
        # latency streaming synthesis exists to avoid (Qwen3-TTS streams from
        # the first token for the same reason). Clause boundaries like commas
        # still wait for min_chars so speech is not chopped into fragments.
        for match in self._sentence_end.finditer(self.buffer):
            if match.end() >= 3:
                return match.end()
        for match in self._boundary.finditer(self.buffer):
            end = match.end()
            if end >= self._active_min:
                return end
        return None

    def _fallback_cut(self) -> int:
        window = self.buffer[: self.max_chars]
        space = window.rfind(" ")
        return space + 1 if space >= self._active_min else min(len(self.buffer), self.max_chars)

    def flush(self) -> str:
        value = self.buffer.strip()
        self.buffer = ""
        return value
