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
