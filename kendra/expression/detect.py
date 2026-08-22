"""Recognise an expressive request without asking the model.

Deterministic, because these must be instant and must never be answered
with "what kind of song were you thinking about?" — the exact deflection
that made her feel like a voice assistant. Measured evidence: "sing me a
song Mary Had a Little Lamb" and "recite a poem about your life" both fell
through to plain chat, where she asked for information he had just given.

The SUBJECT is captured too, so "rap about black holes" performs about
black holes rather than triggering another question.
"""

from __future__ import annotations

import re

from ..phrasing import LEAD_IN, REQUEST_OPENERS

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Checked before "sing": "play me a song" is music, not lyrics.
    ("music", re.compile(r"\bplay\s+(?:me\s+|us\s+)?(?:some\s+|a\s+|the\s+)?"
                         r"(?:music|tune|song|something|melody)\b"
                         r"|\bplay your synth\w*\b|\bmake some music\b", re.I)),
    ("sing", re.compile(r"\b(?:sing|sings|singing|serenade)\b", re.I)),
    ("hum", re.compile(r"\bhum(?:ming)?\b(?!an)", re.I)),
    ("rap", re.compile(r"\brap(?:ping)?\b(?!id|port|t)|\bfreestyle\b|\bspit (?:a |some )?(?:bars|rhymes)\b", re.I)),
    ("poem", re.compile(r"\b(?:poem|poetry|haiku|verse|limerick|sonnet|rhyme)\b", re.I)),
    ("riddle", re.compile(r"\briddle\b|\bbrain ?teaser\b", re.I)),
    ("joke", re.compile(r"\b(?:joke|something funny|make me laugh|pun)\b", re.I)),
    ("story", re.compile(r"\b(?:story|tale|tell me about a time)\b", re.I)),
    ("laugh", re.compile(r"\blaugh\b", re.I)),
    ("dance", re.compile(r"\bdanc(?:e|ing)\b|\bboogie\b|\bgroove\b", re.I)),
    ("whisper", re.compile(r"\bwhisper\b", re.I)),
    ("bow", re.compile(r"\btake a bow\b|\bbow\b", re.I)),
    ("stretch", re.compile(r"\bstretch\b", re.I)),
    ("celebrate", re.compile(r"\bcelebrate\b|\bdo a little dance\b", re.I)),
]

# The request must be addressed to her, not narration ("I sang today").
_ADDRESSED = re.compile(
    # A request may open with filler: "Just sing a song", "So play a tune".
    # Without this, five of six natural phrasings fell through to chat,
    # where she described the song instead of singing it.
    rf"^{LEAD_IN}(?:{REQUEST_OPENERS})\b"
    rf"|^{LEAD_IN}(?:sing|hum|rap|dance|laugh|whisper|bow|stretch|play|"
    rf"recite|tell|perform)\b"
    rf"|^{LEAD_IN}\w+\s+(?:me|us)\b",
    re.I,
)

_SUBJECT = re.compile(
    r"\b(?:about|on|regarding|for)\s+(?P<subject>[^.?!,]{2,60})", re.I,
)
_NAMED_SONG = re.compile(
    r"\b(?:song|poem|story|rap)\s+(?:called\s+)?[\"']?(?P<title>[A-Z][^.?!,\"']{2,50})", re.I,
)


def detect_expression(text: str) -> tuple[str, str | None] | None:
    """Return (behavior, subject) for an expressive request, else None."""
    value = (text or "").strip()
    if not value:
        return None
    if not _ADDRESSED.search(value):
        return None
    for behavior, pattern in _PATTERNS:
        if pattern.search(value):
            subject = None
            named = _NAMED_SONG.search(value)
            about = _SUBJECT.search(value)
            if about:
                subject = about.group("subject").strip()
            elif named:
                subject = named.group("title").strip()
            else:
                # "sing me Mary Had a Little Lamb" — the tail after the verb.
                tail = pattern.split(value, maxsplit=1)
                if len(tail) > 1:
                    rest = re.sub(r"^\s*(?:me|us|a|an|the|some|something)\b", "", tail[1], flags=re.I)
                    rest = rest.strip(" .,!?")
                    if len(rest) > 3:
                        subject = rest
            if subject:
                # "your singing ability" and "me a song" are not topics; a
                # junk subject made her sing ABOUT the word "ability".
                cleaned = re.sub(
                    r"\b(?:ability|abilities|skills?|voice|talent|thing|something|"
                    r"anything|song|tune|music|poem|rap|for (?:me|us)|please)\b",
                    " ", subject, flags=re.I,
                ).strip(" .,!?-")
                subject = cleaned if len(cleaned) > 2 else None
            return behavior, subject
    return None
