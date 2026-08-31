"""The Expressive Behavior Engine: voice + body + lights, as one act.

Two rules from the brief drive the whole design:

1. Every expressive act is a coordinated performance. Voice and body run
   CONCURRENTLY here — she sways while she sings rather than singing and
   then swaying, which is what makes it read as alive.
2. Everything is interruptible. "Stop" cancels the choreography task, the
   speech, and returns her to a neutral pose; the physical e-stop remains
   entirely independent of this layer.

The engine owns no policy about WHEN to perform — that is the planner (on
request) and the spontaneity scheduler (on her own initiative).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from typing import Any

from .catalogue import CATALOGUE, FIXED_TEXT
from .choreography import perform
from .plan import ExpressionPlan
from .vocal_styles import affect_for, shape_text

LOG = logging.getLogger(__name__)


class ExpressionEngine:
    def __init__(self, body: Any, leds: Any = None, render_line: Any = None,
                 on_audio: Any = None):
        self.body = body
        self.leds = leds
        # async (text, affect) -> int16 numpy audio, used for sung lines.
        self.render_line = render_line
        # (phase, text, seconds, kind) -> None. How her face learns a
        # performance is happening: every one of these plays raw PCM through
        # `nonverbal.play`, which never touches the TTS engine, so her mouth
        # sat perfectly still through every song she has ever sung.
        self.on_audio = on_audio
        self._last_hum: str | None = None
        self._last_tune: str | None = None
        self._last_song: str | None = None
        self._current: asyncio.Task | None = None
        self._last_performed: dict[str, float] = {}
        self._recent: list[str] = []

    async def _play_audio(self, audio: Any, text: str = "", kind: str = "song") -> None:
        """Play a performance, and tell whatever is drawing her that it started.

        The duration is the array's own length over the sample rate — exact,
        like every other timing in her voice path. `kind` distinguishes sung
        words (which have syllables to shape a mouth) from a hum or a tune
        (which have none, and want a sustained open mouth instead).
        """
        from .nonverbal import SAMPLE_RATE, play

        seconds = 0.0
        try:
            seconds = float(len(audio)) / float(SAMPLE_RATE)
        except (TypeError, ValueError):
            pass
        if self.on_audio is not None:
            try:
                self.on_audio("start", text, seconds, kind)
            except Exception:
                LOG.debug("face event dropped", exc_info=True)
        try:
            await asyncio.to_thread(play, audio)
        finally:
            if self.on_audio is not None:
                try:
                    self.on_audio("end", "", 0.0, kind)
                except Exception:
                    LOG.debug("face event dropped", exc_info=True)

    # ---------------------------------------------------------- planning
    def plan_for(self, behavior: str, text: str | None = None,
                 reason: str | None = None) -> ExpressionPlan | None:
        spec = CATALOGUE.get(behavior)
        if spec is None:
            return None
        if text is None and not spec.generate:
            choices = FIXED_TEXT.get(behavior) or [""]
            # Never the same variant twice in a row.
            recent = self._recent[-3:]
            fresh = [c for c in choices if c not in recent] or choices
            text = random.choice(fresh)
        return ExpressionPlan(
            behavior=behavior,
            text=text,
            vocal_style=spec.vocal_style,
            motion_choreography=spec.gesture,
            motion_intensity=spec.intensity,
            head_behavior=spec.head,
            light_behavior=spec.light,
            tempo_bpm=spec.tempo_bpm,
            duration_limit_s=spec.duration_s,
            spontaneity_reason=reason,
        ).validated()

    # --------------------------------------------------------- execution
    async def execute(self, plan: ExpressionPlan, speak) -> dict[str, Any]:
        """Run one performance. `speak` is an async (text, affect) callable."""
        started = time.time()
        spoken = shape_text(plan.text or "", plan.vocal_style)
        affect = affect_for(plan.vocal_style)

        await self._light(plan.light_behavior)

        # Wordless sounds and melody are AUDIO problems, not text problems.
        # Kokoro spells "Hmm" as H-M-M because its phonemizer has no
        # grapheme for a closed mouth, and it renders one flat pitch per
        # utterance, so a "song" came out as speech with stretched vowels.
        # Both are handled below by generating or reshaping waveforms.
        async def choreograph() -> None:
            # Body loops for the whole performance rather than gesturing
            # once at the start: she should still be moving on the last line.
            deadline = time.time() + plan.duration_limit_s
            while time.time() < deadline:
                await perform(self.body, plan.motion_choreography, plan.motion_intensity)
                if plan.head_behavior:
                    await perform(self.body, plan.head_behavior, plan.motion_intensity * 0.8)

        # Motion starts FIRST so the body is already moving while the audio
        # plays. Previously the sung and hummed audio was produced before
        # the choreography task existed, so she performed and then danced.
        motion = asyncio.create_task(choreograph(), name=f"kendra-express-{plan.behavior}")
        try:
            # Wordless sounds and melody are AUDIO problems, not text
            # problems: Kokoro spells "Hmm" as H-M-M, and renders one flat
            # pitch per utterance so a "song" came out as speech with
            # stretched vowels. These branches play waveforms and must NOT
            # then be spoken — singing did exactly that, rendering the
            # lyrics twice and cutting itself off mid-performance.
            audio_only = False
            if plan.vocal_style == "humming":
                spoken = await self._perform_hum(plan)
                audio_only = True
            elif plan.behavior == "music":
                spoken = await self._perform_music(plan)
                audio_only = True
            elif plan.vocal_style == "singing":
                spoken = await self._perform_singing(plan)
                audio_only = True
            if spoken and not audio_only:
                await speak(spoken, affect)
        finally:
            motion.cancel()
            with_suppressed = asyncio.gather(motion, return_exceptions=True)
            await with_suppressed
            await perform(self.body, "neutral", 0.2)
            await self._light(None)

        self._last_performed[plan.behavior] = time.time()
        self._recent.append(plan.text or plan.behavior)
        del self._recent[:-8]
        elapsed = round(time.time() - started, 1)
        LOG.info("Performed %s (%s) in %.1fs", plan.behavior, plan.vocal_style, elapsed)
        return {"ok": True, "behavior": plan.behavior, "seconds": elapsed,
                "spoken": spoken, "plan": plan.as_dict()}

    async def _perform_hum(self, plan: ExpressionPlan) -> str:
        """A real hum: synthesized tone with vibrato, never spoken letters."""
        import random as _random

        from .nonverbal import CONTOURS, hum

        style = _random.choice([s for s in CONTOURS if s != self._last_hum] or list(CONTOURS))
        self._last_hum = style
        audio = hum(style)
        await self._play_audio(audio, kind="hum")
        return f"(hums, {style})"

    async def _perform_music(self, plan: ExpressionPlan) -> str:
        """Play a tune on her own synthesizer — the ambient tone voice,
        used musically. No model, no TTS: instant, and identical on the Pi."""
        import random as _random

        from .nonverbal import TUNES, play_tune

        name = _random.choice([n for n in TUNES if n != self._last_tune] or list(TUNES))
        self._last_tune = name
        await self._play_audio(play_tune(name), kind="tune")
        return f"(plays {name.replace('_', ' ')})"

    async def _perform_singing(self, plan: ExpressionPlan) -> str:
        """Her singing voice: the synth tone carrying a tune.

        Jonathan's correction, and it is right: speeding speech up and down
        is not singing. Her voice is the synthesized tone — the same nasal
        vibrato timbre as her humming — carrying an actual melody. She is a
        robot, so this IS her singing voice rather than an imitation of a
        human one. The generated lyrics still reach the transcript so the
        words are there to read; the AUDIO is her own instrument.
        """
        import random as _random

        from .nonverbal import SONG_SHAPES, sing_melody

        shape = _random.choice(
            [s for s in SONG_SHAPES if s != self._last_song] or list(SONG_SHAPES)
        )
        self._last_song = shape
        tempo = plan.tempo_bpm or 96
        words = (plan.text or "").strip()
        await self._play_audio(sing_melody(shape, bpm=tempo), words, kind="song")
        return f"(sings, {shape}) {words}" if words else f"(sings, {shape})"

    async def _perform_song(self, plan: ExpressionPlan) -> str | None:
        """Sing by giving each line its own pitch — a melody, not monotone."""
        import numpy as np

        from .nonverbal import apply_melody

        lines = [ln.strip() for ln in re.split(r"[\n.]+", plan.text or "") if ln.strip()]
        if not lines:
            return None
        rendered: list[np.ndarray] = []
        for line in lines[:8]:
            audio = await self.render_line(line, "delighted")
            if audio is not None and audio.size:
                rendered.append(audio)
        if not rendered:
            return None
        melody = "lullaby" if plan.motion_intensity < 0.35 else "simple"
        await self._play_audio(
            apply_melody(rendered, melody),
            " ".join(line.rstrip(".") + "." for line in lines[:8]),
            kind="song",
        )
        # Joined with a slash this went into her transcript AND her history,
        # and she then read the punctuation out: "why do you keep saying
        # slash?". Transcripts must contain only speakable text.
        return " ".join(line.rstrip(".") + "." for line in lines)

    def stop(self) -> None:
        """Interruptibility is mandatory: cancel mid-performance."""
        if self._current is not None and not self._current.done():
            self._current.cancel()
            self._current = None

    def seconds_since(self, behavior: str) -> float:
        last = self._last_performed.get(behavior)
        return 1e9 if last is None else time.time() - last

    async def _light(self, behavior: str | None) -> None:
        if self.leds is None:
            return
        try:
            await self.leds.call("express", {"state": "warm" if behavior else "neutral"})
        except Exception:
            LOG.debug("expressive lighting unavailable", exc_info=True)
