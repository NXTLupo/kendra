"""How her context is BUILT, which is where her sense of self actually lives.

Measured on her live model (scripts/behaviour_probe.py, 10 samples per probe):

    identity paragraph + memories as a bullet list   34/50, recited itself 2x
    short line + subject-labelled memories + a
    deterministic self note when asked               41/50, recited itself 0x

The single largest move was recall of a fact already sitting in her prompt:
3/10 to 10/10. She was not forgetting it — she was being asked to work out who
"Jonathan", "you" and "mine" referred to, in one sentence, at inference time.

These tests hold the shape in place. They deliberately assert structure rather
than wording: a model's exact words are not a contract, but "every memory
states its subject" is.
"""

from __future__ import annotations

import json
import re

from kendra.agent.planner import AgentRuntime
from kendra.brain.store import _is_own_question, _shape

# --- memories arrive with their subject attached ------------------------------

def test_every_memory_states_who_it_is_about() -> None:
    block = AgentRuntime._memory_message(
        {
            "memories": [
                {"content": "Jonathan like classical music on the guitar."},
                {"content": "Kendra prefers the quiet part of the afternoon."},
                {"content": "The kettle is on the left of the sink."},
            ]
        }
    )[0]["content"]
    payload = json.loads(block[block.index("[") :])
    assert [item["about"] for item in payload] == [
        "Jonathan (the person you are speaking with)",
        "you (Kendra)",
        "unattributed",
    ]
    # The stored words are passed through untouched. Rewriting them is what
    # `_third_person()` did, and it corrupted 15% of her durable memories.
    assert payload[0]["memory"] == "Jonathan like classical music on the guitar."


def test_an_explicit_subject_column_wins_over_the_leading_word() -> None:
    block = AgentRuntime._memory_message(
        {"memories": [{"content": "prefers the guitar to the piano.", "subject": "Jonathan"}]}
    )[0]["content"]
    assert "Jonathan (the person you are speaking with)" in block


def test_the_memory_block_names_the_listener_once_and_only_once() -> None:
    """The referent is stated as a rule, not left for her to infer per row."""
    block = AgentRuntime._memory_message({"memories": [{"content": "Jonathan likes tea."}]})[0][
        "content"
    ]
    assert '"Jonathan" is the person you are speaking with' in block
    assert '"Kendra" is you' in block


def test_an_empty_retrieval_still_carries_her_clock() -> None:
    block = AgentRuntime._memory_message({"memories": []})[0]["content"]
    assert "Current date and time" in block
    assert "WHAT YOU KNOW" not in block, "no memories means no memory block"


# --- her own questions are not memories ---------------------------------------

def test_her_own_wondering_is_never_retrieved_as_a_fact() -> None:
    """Measured: asked what music he likes, three of her four context slots
    held three near-identical copies of "I found myself wondering: What kind
    of music do you like?" — the question outranking its own answer."""
    assert _is_own_question("I found myself wondering: What kind of music do you like?")
    assert _is_own_question("I wonder whether he has eaten yet")
    assert _is_own_question("Open question: what is his sister called?")
    assert _is_own_question("What kind of music do you like?")
    # Statements survive, including ones that merely contain a question mark.
    assert not _is_own_question("Jonathan like classical music on the guitar.")
    assert not _is_own_question("He asked what time it was. She told him.")


def test_duplicates_collapse_to_one_slot() -> None:
    """"Jonathan like early eighties heavy." is stored three separate times."""
    a = _shape("Jonathan like early eighties heavy.")
    b = _shape("Jonathan like early eighties heavy")
    c = _shape("Jonathan like classical music on the guitar.")
    assert a == b
    assert a != c


# --- the one question she must never get wrong --------------------------------

def test_self_questions_inject_ground_truth() -> None:
    note = AgentRuntime._self_note(AgentRuntime)[0]["content"]
    facts = json.loads(note[note.index("{") : note.rindex("}") + 1])
    assert facts["you_are"] == "Kendra"
    assert "Jonathan" in facts["who_is_speaking_to_you"]
    assert "Jonathan" in facts["you_are_not"]
    assert "Do not quote this note" in note


def test_the_self_note_is_short_enough_to_be_free() -> None:
    """It rides only on turns where he actually asked, but even then it must
    not push her real memories out of a small context."""
    note = AgentRuntime._self_note(AgentRuntime)[0]["content"]
    assert len(note) < 500, f"self note is {len(note)} characters"


def test_asking_who_she_is_is_recognised_but_asking_who_she_sees_is_not() -> None:
    for question in (
        "Who are you?",
        "and who are you",
        "Tell me who you are.",
        "What's your name?",
        "Who am I talking to?",
    ):
        assert AgentRuntime._SELF_QUESTION.search(question), question
        assert not AgentRuntime._asks_who_she_sees(question), question

    for question in (
        "Who's in front of you?",
        "Tell me who you see.",
        "Do you recognize me?",
    ):
        assert AgentRuntime._asks_who_she_sees(question), question


# --- the prompt must never become something she recites -----------------------

def test_the_conversation_prompt_stays_unquotable() -> None:
    """A long, distinctive identity paragraph was added on 2026-08-22 and
    measured roughly neutral while teaching her to recite it back — the exact
    bug commit 02a954e fixed. Anything added here must be something she would
    never want to say aloud."""
    source = AgentRuntime._conversation_prompt.__doc__ or ""
    assert "unquotable" in source.lower()

    import inspect

    body = inspect.getsource(AgentRuntime._conversation_prompt)
    returned = body[body.index('return f"""') :]
    # The charter is interpolated; what this function ADDS is the identity
    # text, and it has to stay small.
    added = returned.replace("{self.charter}", "")
    added = re.sub(r'return f"""|""".strip\(\)', "", added).strip()
    assert len(added) < 400, f"the prompt's own identity text is {len(added)} characters"
    for recitable in ("a guitar and a life outside this room", "no matter whose name appears"):
        assert recitable not in added


def test_the_stable_prompt_never_carries_per_turn_data() -> None:
    """KV-cache reuse depends on this prefix being byte-identical every turn,
    and a cache miss costs ~30 s of prefill before she can speak."""
    import inspect

    body = inspect.getsource(AgentRuntime._conversation_prompt)
    returned = body[body.index('return f"""') :]
    placeholders = set(re.findall(r"\{([a-z_.]+)\}", returned))
    assert placeholders <= {"self.charter"}, f"volatile data in the cacheable prefix: {placeholders}"


# --- storage records the subject instead of rewriting the sentence ------------

def test_the_speakers_own_words_are_never_rewritten() -> None:
    """`_third_person()` is gone, and with it the corruption it caused.

    It rewrote only the FIRST pronoun and never touched "you", "my" or "mine",
    producing rows like "Jonathan work for a diner, I don't work for you".
    """
    import inspect

    from kendra.brain import consolidator

    source = inspect.getsource(consolidator)
    assert "def _third_person" not in source, "the rewrite must not come back"
    assert "def _person_is_coherent" not in source, (
        "rejecting ambiguous memories discarded real facts; attribution replaces it"
    )


def test_who_a_memory_is_about_is_recorded_from_its_kind_and_provenance() -> None:
    from kendra.brain.consolidator import BrainConsolidator as C

    class Item:
        def __init__(self, **kw):
            self.subject = kw.get("subject")
            self.kind = kw.get("kind", "fact")
            self.content = kw.get("content", "")
            self.provenance = kw.get("provenance", "inferred")

    # He said something about himself.
    assert C._subject_for(Item(provenance="user_stated", content="I'm actually fifty five")) == "Jonathan"
    # He said something about the world. Attributing it to him would be a lie
    # repeated on every retrieval.
    assert C._subject_for(
        Item(provenance="user_stated", content="Who is the current president of the United States.")
    ) is None
    # Her own opinion is hers.
    assert C._subject_for(Item(kind="kendra_opinion", content="Kendra thinks bees vote")) == "Kendra"
    # A sight is about the room; she is only the observer.
    assert C._subject_for(Item(kind="observation", content="I saw a man holding a guitar")) is None
    # An explicit subject always wins, and the two names that matter normalise.
    assert C._subject_for(Item(subject="me", content="anything")) == "Kendra"
    assert C._subject_for(Item(subject="user", content="anything")) == "Jonathan"
    assert C._subject_for(Item(subject="Peiyi", content="anything")) == "Peiyi"


def test_a_remembered_sight_is_labelled_as_past_and_not_as_her() -> None:
    block = AgentRuntime._memory_message(
        {"memories": [{"kind": "observation", "content": "I saw: a man holding a guitar."}]}
    )[0]["content"]
    assert "something you saw earlier" in block
    assert "you (Kendra)" not in block
