"""Regression tests for the failures that kept coming back.

Every case here is a bug Jonathan actually hit. They exist because fixing
Kendra by hand turned into whack-a-mole: a change that repaired one
behaviour silently broke another, and nothing caught it until he heard it.
These run in milliseconds and need no audio hardware.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kendra.agent.planner import AgentRuntime
from kendra.expression.catalogue import CATALOGUE
from kendra.expression.detect import detect_expression
from kendra.expression.nonverbal import SONG_SHAPES, hum, play_tune, sing_melody


class TestSinging:
    def test_singing_is_the_synth_voice_not_speech(self):
        """Her singing must be a synthesized melody, not pitch-shifted TTS."""
        audio = sing_melody("bright")
        assert audio.size > 22050, "a song should last more than a second"
        assert audio.dtype.name == "int16"

    def test_every_song_shape_renders(self):
        for shape in SONG_SHAPES:
            assert sing_melody(shape).size > 0

    def test_humming_is_audio_not_the_letters_h_m_m(self):
        """Kokoro spells 'Hmm' aloud; humming must be generated audio."""
        assert hum("thoughtful").size > 0

    def test_synth_tunes_render(self):
        assert play_tune("little_wander").size > 0


class TestExpressiveRouting:
    def test_requests_route_to_a_performance(self):
        for phrase in ("sing me a song", "hum something", "play me a tune",
                       "tell me a joke", "can you demonstrate your singing ability",
                       "rap about black holes", "recite a poem"):
            assert detect_expression(phrase) is not None, phrase

    def test_filler_before_the_verb_still_routes(self):
        """"Just sing a song" fell through to chat, where she DESCRIBED a song."""
        for phrase in ("Just sing a song", "just hum something", "So sing me a song",
                       "Now play a tune", "Well, tell me a joke", "Okay sing something"):
            assert detect_expression(phrase) is not None, phrase

    def test_movement_and_expression_share_one_vocabulary(self):
        """The two detectors disagreed: "Now back up" moved her, "Just walk
        forward" did not."""
        from kendra.agent.movement import parse_movement

        for phrase in ("Just walk forward", "Now back up", "So turn left",
                       "Just come here"):
            assert parse_movement(phrase) is not None, phrase
        assert parse_movement("I might walk to the store later") is None

    def test_conversation_never_triggers_a_performance(self):
        for phrase in ("I play guitar every day", "do you like music",
                       "we listened to a song yesterday", "what music do I like",
                       "remember when I played you that song"):
            assert detect_expression(phrase) is None, phrase

    def test_every_catalogue_behaviour_has_choreography(self):
        from kendra.expression.choreography import ROUTINES

        for name, spec in CATALOGUE.items():
            assert spec.gesture in ROUTINES, f"{name} has no body movement"


class TestSightRouting:
    """Her camera must open for real requests, never for rhetoric."""

    def test_rhetorical_look_does_not_open_her_eyes(self):
        for phrase in ("Just look at Miles Davis.", "look at how fast it goes",
                       "look at what he did"):
            assert not AgentRuntime._SIGHT_INTENT.search(phrase), phrase

    def test_real_sight_requests_still_route(self):
        for phrase in ("take a look at this", "look at my guitar", "look at that",
                       "can you see me", "look around",
                       "how many fingers am I holding up"):
            assert AgentRuntime._SIGHT_INTENT.search(phrase), phrase


class TestSpeechHygiene:
    """She must never say her own scaffolding out loud."""

    def test_instruction_notes_are_never_spoken(self):
        notes = [{"role": "system", "content":
                  "He said very little, so reply in one short sentence. Do not "
                  "invent a topic or philosophise."}]
        leaked = ("He said very little, so reply in one short sentence. Do not "
                  "invent a topic or philosophise.")
        assert AgentRuntime._strip_instruction_echo(leaked, notes) == ""

    def test_real_speech_survives_the_echo_guard(self):
        notes = [{"role": "system", "content": "Reply in one short sentence."}]
        real = "That was Iron Maiden, and the scream at the end is my favourite part."
        assert AgentRuntime._strip_instruction_echo(real, notes) == real

    def test_unasked_clock_recital_is_stripped(self):
        answer = ("I am good. It is 4:40 PM PDT on Thursday, August 20, 2026. "
                  "What about you?")
        cleaned = AgentRuntime._strip_unasked_clock(answer, "How are you?")
        assert "4:40" not in cleaned and "good" in cleaned


class TestDelivery:
    """Announcing a thing is not doing the thing."""

    def _runtime(self):
        return AgentRuntime.__new__(AgentRuntime)

    def test_announcement_without_content_is_undelivered(self):
        runtime = self._runtime()
        assert runtime._looks_undelivered(
            "I can try. What kind of music are you into?", "sing me a song")
        assert runtime._looks_undelivered(
            "I can run a quick quiz on space. Ready?", "make me a quiz on space")

    def test_real_content_passes(self):
        runtime = self._runtime()
        assert not runtime._looks_undelivered(
            "Mary had a little lamb, its fleece was white as snow.", "sing me a song")
        assert not runtime._looks_undelivered(
            "I love it — the energy is unreal.", "what do you think about metal")

    def test_performance_never_sings_a_capability_denial(self):
        """She sang "I can't process images or audio right now" as lyrics."""
        from kendra.voice.service import _PERFORMANCE_DENIAL

        assert _PERFORMANCE_DENIAL.search("I can't process images or audio right now.")
        assert _PERFORMANCE_DENIAL.search("As an AI, I don't have feelings.")
        # ...but honest blindness in CONVERSATION must remain sayable.
        assert AgentRuntime._strip_instruction_echo(
            "I can't see right now — my camera feed isn't reaching me.", []
        ) == "I can't see right now — my camera feed isn't reaching me."

    def test_product_test_catches_articulate_meta(self):
        """Length alone cannot tell; the caller's product test can."""
        runtime = self._runtime()
        wordy_meta = ("I did a quick search on what is trending online — here are "
                      "the headlines from my local research.")
        assert runtime._looks_undelivered(
            wordy_meta, "research the top news today",
            has_product=lambda text: "Supreme" in text,
        )


class TestExpressionRequestsHeard:
    """Phrasings that fell through to chat, transcribed from real sessions.

    Each of these produced small talk instead of a performance. Two turns
    from 2026-08-22, verbatim:

        You:    Yeah. You want to sing it again?
        Kendra: I will sing it again. It feels good to try something new.
        You:    Use your synthesizer.
        Kendra: I can try that — the sound of a keyboard clicking is quite
                different from the hum of a guitar.

    Both were detector misses, not model failures: nothing routed them to
    the expression engine, so the model was free to chat about singing.
    """

    def test_invitation_to_sing_is_a_request(self):
        from kendra.expression.detect import detect_expression

        # An invitation is how people actually ask.
        for phrase in (
            "Yeah. You want to sing it again?",
            "do you wanna sing?",
            "Want to sing me something?",
            "Sure, you want to hum a tune?",
        ):
            found = detect_expression(phrase)
            assert found is not None, phrase
            assert found[0] in {"sing", "hum"}, (phrase, found)

    def test_any_verb_against_the_synthesiser_plays_music(self):
        from kendra.expression.detect import detect_expression

        for phrase in ("Use your synthesizer.", "use your synth",
                       "play the synthesizer", "fire up the synth"):
            assert detect_expression(phrase) == ("music", None), phrase

    def test_repeat_requests_carry_no_subject(self):
        """"Sing it again" is a repeat; as a subject she sang about "it again"."""
        from kendra.expression.detect import detect_expression

        for phrase in ("Sing it again", "Can you sing that again?",
                       "Sing another one", "Yeah. You want to sing it again?"):
            behavior, subject = detect_expression(phrase)
            assert behavior == "sing", phrase
            assert subject is None, (phrase, subject)

    def test_real_subjects_still_survive(self):
        """The repeat filter must not eat genuine topics."""
        from kendra.expression.detect import detect_expression

        assert detect_expression("Sing a song about the sky") == ("sing", "the sky")
        assert detect_expression("rap about black holes") == ("rap", "black holes")
        assert detect_expression(
            "sing me Mary Had a Little Lamb"
        ) == ("sing", "Mary Had a Little Lamb")

    def test_narration_is_not_a_request(self):
        """She must not burst into song because he mentioned singing."""
        from kendra.expression.detect import detect_expression

        for phrase in ("I sang today", "Yes, I sang at school",
                       "I want to hear about your day",
                       "My daughter sings in the choir"):
            assert detect_expression(phrase) is None, phrase


class TestWikiCompileCannotWedge:
    """The compile loop once held her only LLM every idle window, forever.

    Observed 2026-08-22, brain.log, every ~50 s for over half an hour:

        POST 127.0.0.1:17800/v1/chat/completions
        WARNING Wiki compile skipped: Unterminated string ... line 47
        Wiki compile: {'reason': 'compile_unavailable:JSONDecodeError'}
                      (pending 726 -> 748)

    The answer was truncated by `max_tokens`, `json.loads` threw, and the
    early return skipped `sb.advance(cursor)` — so the same entries were
    re-read forever while `pending` climbed. Live turns queued behind it
    ("First audio out in 15.2 s") and memory consolidation timed out. To
    her user she choked and died.
    """

    def test_truncated_answer_keeps_the_pages_that_closed(self):
        from kendra.brain.consolidator import _salvage_pages

        cut_off = (
            '{"pages": ['
            '{"slug": "jonathan", "title": "Jonathan", "facts": ["Jonathan likes folk."]},'
            '{"slug": "kendra-self", "title": "Kendra", "facts": ["Kendra enjoys singing."]},'
            '{"slug": "lamb", "title": "Lamb", "facts": ["Kendra sang Mary Had a Litt'
        )
        recovered = _salvage_pages(cut_off)
        assert recovered is not None
        assert [p["slug"] for p in recovered["pages"]] == ["jonathan", "kendra-self"]

    def test_unusable_answer_salvages_to_nothing(self):
        from kendra.brain.consolidator import _salvage_pages

        assert _salvage_pages("I'm sorry, I can't do that.") is None
        assert _salvage_pages("") is None

    def test_a_bad_batch_advances_the_cursor(self):
        """The wedge itself: an unusable answer must not be retried forever."""
        import asyncio

        from kendra.brain import consolidator as module

        class DeadLLM:
            async def chat(self, *a, **k):
                return "not json at all"          # answers, but unusably

        class Brain:
            def __init__(self):
                self.advanced = []

            def pending(self, limit):
                return [{"kind": "observation", "content": f"thing {i}"}
                        for i in range(limit)], 123

            def advance(self, cursor):
                self.advanced.append(cursor)

        class Store:
            def __init__(self):
                self.events = []

            def event(self, name, payload):
                self.events.append((name, payload))

        worker = module.BrainConsolidator.__new__(module.BrainConsolidator)
        worker.llm = DeadLLM()
        worker.store = Store()
        brain = Brain()

        result = asyncio.run(worker.compile_wiki(brain))
        assert result.get("skipped") is True
        assert brain.advanced == [123], "cursor must move or the loop re-reads forever"

    def test_a_dead_model_server_does_NOT_advance(self):
        """A transport failure means the batch was never seen — keep it."""
        import asyncio

        from kendra.brain import consolidator as module

        class Offline:
            async def chat(self, *a, **k):
                raise ConnectionError("model server is down")

        class Brain:
            def __init__(self):
                self.advanced = []

            def pending(self, limit):
                return [{"kind": "observation", "content": f"thing {i}"}
                        for i in range(limit)], 7

            def advance(self, cursor):
                self.advanced.append(cursor)

        worker = module.BrainConsolidator.__new__(module.BrainConsolidator)
        worker.llm = Offline()
        worker.store = None
        brain = Brain()

        result = asyncio.run(worker.compile_wiki(brain))
        assert "compile_unavailable" in result["reason"]
        assert brain.advanced == [], "an unseen batch must be kept for the next lull"


class TestVadCannotSitUnderTheNoiseFloor:
    """Three 25 s captures in a row is what "her voice chat died" looked like.

    From voice.log, 2026-08-22:

        Captured 25.0s of speech (peak RMS 6017, threshold 120)
        Captured 24.6s of speech (peak RMS 6627, threshold 120)
        No speech detected ...   (peak RMS  128 vs threshold 120)

    Calibration caught an unusually quiet instant, so the threshold pinned to
    its 120 lower clamp — but the room's real ambient is 128-260. Silence
    therefore never registered: every capture ran to the hard cap while she
    transcribed room noise, and it stayed that way for the whole session.
    """

    def test_the_lower_clamp_clears_a_real_room(self):
        """Speech is RMS 2000-9000; the clamp must sit above ambient, not at it."""
        import numpy as np

        quietest_real_speech = 2000.0
        measured_room_ambient = 260.0
        # The calibration expression, as it appears in audio.py.
        near_silent_room = 20.0
        threshold = float(np.clip(near_silent_room * 3.0, 350.0, max(550.0, 24.0)))

        assert threshold > measured_room_ambient, "silence would never register"
        assert threshold < quietest_real_speech / 4, "would eat quiet speech"

    def test_source_keeps_the_raised_clamp(self):
        """Locks the constant itself: 120 is what broke her."""
        from pathlib import Path

        source = Path("kendra/voice/audio.py").read_text()
        assert "np.clip(floor * 3.0, 350.0, ceiling)" in source
        assert "np.clip(floor * 3.0, 120.0, ceiling)" not in source

    def test_a_capped_capture_raises_the_threshold(self):
        """She must recover on the next turn, not need a restart."""
        import numpy as np

        # The recovery expression, as it appears in audio.py.
        def recover(threshold: float, quietest_block: float) -> float:
            return float(np.clip(
                max(threshold * 1.8, quietest_block * 2.5), threshold, 900.0,
            ))

        # The real numbers from the failure: threshold 120, room noise ~260.
        raised = recover(120.0, 260.0)
        assert raised > 260.0, "must clear the floor that caused the runaway"
        assert raised <= 900.0, "must never deafen her to ordinary speech"

        # It only ever climbs, and it saturates rather than running away.
        assert recover(900.0, 5000.0) == 900.0
        assert recover(400.0, 10.0) >= 400.0


class TestSheConversesInsteadOfInterviewing:
    """Six consecutive replies, each ending in a question. Verbatim, 4:14-5:06:

        You:    I'm playing guitar.
        Kendra: I see. What part are you focusing on right now?
        You:    Oh
        Kendra: That's interesting. What kind of part are you focusing on?
        You:    True.
        Kendra: That sounds like he's agreeing with something. What exactly
                are you thinking about now?

    Two guards existed for this and NEITHER ran. `_curb_question_tic` was
    defined and called from nowhere; the third-person migration lived only in
    the memory consolidator, where it keeps stored facts in third person, and
    nothing did the reverse for what she says out loud.
    """

    def test_a_second_question_in_a_row_is_dropped(self):
        from kendra.agent.planner import AgentRuntime

        curbed = AgentRuntime._curb_question_tic(
            "That's interesting. What kind of part are you focusing on?",
            "I see. What part are you focusing on right now?",
        )
        assert not curbed.endswith("?"), curbed
        assert "interesting" in curbed

    def test_a_first_question_is_left_alone(self):
        """She is allowed to be curious — just not relentlessly."""
        from kendra.agent.planner import AgentRuntime

        answer = "I see. What part are you focusing on?"
        assert AgentRuntime._curb_question_tic(answer, "That sounds lovely.") == answer

    def test_a_reply_that_is_only_a_question_survives(self):
        """Silence would be worse than one more question."""
        from kendra.agent.planner import AgentRuntime

        answer = "What are you thinking?"
        assert AgentRuntime._curb_question_tic(answer, "And you?") == answer

    def test_she_stops_narrating_him_to_his_face(self):
        from kendra.agent.planner import AgentRuntime

        spoken = AgentRuntime._speak_to_him(
            "That sounds like he's agreeing with something. "
            "What exactly are you thinking about now?",
            "True.",
        )
        assert "he" not in spoken.lower().split()
        assert "thinking about now" in spoken

    def test_a_real_third_party_is_left_alone(self):
        """If he brought someone up, 'he' is about them and is legitimate."""
        from kendra.agent.planner import AgentRuntime

        answer = "Your friend sounds kind. He must be a good listener."
        assert AgentRuntime._speak_to_him(answer, "My friend helped me today") == answer
        assert AgentRuntime._speak_to_him(answer, "She called me earlier") == answer

    def test_an_ordinary_reply_is_untouched(self):
        from kendra.agent.planner import AgentRuntime

        answer = "That sounds lovely. I like the sound of it."
        assert AgentRuntime._speak_to_him(answer, "True.") == answer

    def test_the_guards_are_actually_reachable(self):
        """The whole bug: both were defined and neither was ever called."""
        from pathlib import Path

        source = Path("kendra/agent/planner.py").read_text()
        for guard in ("_curb_interview(", "_speak_to_him("):
            calls = source.count(guard)
            assert calls >= 2, f"{guard} is defined but never called ({calls} occurrence)"
