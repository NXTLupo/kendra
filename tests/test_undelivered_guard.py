"""She must never announce work she then does not show.

Measured on the live stack, 2026-08-23. Jonathan said "Well done." and she
replied:

    "I did a quick search on what is trending online — here are the
     headlines from my local research. (2026-08-21)"

Nobody asked for headlines, none were given, and the date was two days old.
That is not a bad answer, it is a claim about work she did not do — which her
charter forbids in as many words.

The guard existed and did nothing, because its first line was:

    if not self._ASKED_FOR_PRODUCT.search(user_text or ""):
        return False

so it only ever caught an undelivered thing he had ASKED for. A product she
volunteers and then withholds is the worse case, and it was unguarded.
"""

from __future__ import annotations

import pytest

from kendra.agent.planner import AgentRuntime


@pytest.fixture()
def runtime() -> AgentRuntime:
    return object.__new__(AgentRuntime)


def undelivered(runtime: AgentRuntime, answer: str, asked: str) -> bool:
    return AgentRuntime._looks_undelivered(runtime, answer, asked)


def test_the_exact_reply_that_prompted_this(runtime: AgentRuntime) -> None:
    assert undelivered(
        runtime,
        "I did a quick search on what is trending online — here are the "
        "headlines from my local research. (2026-08-21)",
        "Well done.",
    )


def test_an_announcement_is_caught_even_when_nothing_was_asked(runtime: AgentRuntime) -> None:
    for answer in (
        "Here's what I found from my local research.",
        "I just did a quick search online for the latest headlines.",
        "Let me look that up — here are the results.",
    ):
        assert undelivered(runtime, answer, "Well done."), answer


def test_length_alone_cannot_catch_it(runtime: AgentRuntime) -> None:
    """The announcement is long. Every content word in it is about searching.

    A word-count test passes this; a vocabulary test does not.
    """
    answer = (
        "I did a quick search on what is trending online — here are the "
        "headlines from my local research."
    )
    assert len(answer.split()) > 15
    assert undelivered(runtime, answer, "Well done.")


def test_a_real_answer_survives(runtime: AgentRuntime) -> None:
    """The point is to catch empty announcements, not to police her voice."""
    for asked, answer in (
        ("look up Boy Harsher", "They are an American electronic group formed in 2013 in Savannah, Georgia."),
        ("what's the news?", "Here are the headlines: the Supreme Court allowed ballroom construction, and China rejected a tariff threat."),
        ("what music do you like", "I like classical and synthesizer music."),
        ("sing me a song", "Here's a song: La la la, six little legs and a song for you."),
        ("Well done.", "Thanks — that one took a couple of tries."),
    ):
        assert not undelivered(runtime, answer, asked), answer


def test_a_promise_with_no_delivery_is_still_caught(runtime: AgentRuntime) -> None:
    """The original case the guard was written for, which must keep working."""
    assert undelivered(runtime, "Sure, I'll look that up for you.", "look up Boy Harsher")
    assert undelivered(runtime, "", "sing me a song")
