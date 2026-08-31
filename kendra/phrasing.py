"""Shared speech shapes, so her detectors cannot drift apart.

Movement and expressive requests each grew their own idea of what may
precede a command, and they disagreed: "Now back up" moved her but "Just
walk forward" did not, and "Just sing a song" fell through to chat where
she narrated a song instead of singing one. One vocabulary, used by both.
"""

from __future__ import annotations

import re

# Discourse particles and politeness that can precede a real command.
# "Just sing a song", "So turn left", "Okay, play me a tune".
# Agreement particles belong here too. "Yeah. You want to sing it again?"
# fell through to plain chat and she answered by TALKING about singing,
# because "yeah" was not recognised as filler.
LEAD_IN = (
    r"(?:\s*(?:hey|hi|okay|ok|so|now|then|and|but|well|just|please|alright|"
    r"c'?mon|come on|go ahead and|maybe|kendra|girl|yeah|yea|yes|yep|sure|"
    r"cool|nice|great|awesome|oh)[,!.\s]*)*"
)

# Ways of addressing a request to her.
# Invitations count as requests. Asking "you want to sing it again?" is how
# people actually ask, and it read as small talk until it was listed here.
REQUEST_OPENERS = (
    r"can you|could you|would you|will you|i want you to|i need you to|"
    r"i'?d like you to|how about|why don'?t you|let'?s hear|lets hear|"
    r"give me|make up|write|recite|perform|demonstrate|show me|show us|try|"
    r"do you want to|do you wanna|you want to|you wanna|wanna|want to|"
    r"use your|play your|fire up"
)


def leading_particles_only(text: str) -> bool:
    """True when everything before a verb is filler, not another clause."""
    return bool(re.fullmatch(LEAD_IN, text or "", re.I))
