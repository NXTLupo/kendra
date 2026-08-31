"""One set of names for what she is doing, shared by everything that shows it.

Her thinking tones, her LED ring and her face all display the same three
states. They must agree on what those states are CALLED, because nothing
errors when they do not -- the mismatched name simply matches nothing and
falls through to the default.

That is exactly what happened: the renderer was written against invented
names ("search", "look") while her voice service has always emitted
"research" and "sight". The thinking bubble worked, so the whole event chain
looked healthy, and the research and sight animations never appeared once.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: The vocabulary, defined by her LED service, which had it first.
MODES = ("think", "research", "sight")


def test_the_led_service_defines_these_three() -> None:
    leds = (ROOT / "kendra/leds/service.py").read_text(encoding="utf-8")
    for mode in MODES:
        assert f'"{mode}"' in leds, f"the LED ring does not know {mode!r}"


def test_the_voice_service_emits_exactly_these() -> None:
    """`_turn_mode` picks the state; nothing else may invent a fourth."""
    voice = (ROOT / "kendra/voice/service.py").read_text(encoding="utf-8")
    body = voice[voice.index("def _turn_mode("):]
    body = body[: body.index("\ndef ")]
    emitted = set(re.findall(r'return "([a-z]+)"', body))
    assert emitted == set(MODES), f"voice emits {sorted(emitted)}, expected {sorted(MODES)}"


def test_the_face_understands_every_one_of_them() -> None:
    """The bug: the renderer matched names her services never send."""
    body = (ROOT / "dashboard/src/KendraBody.tsx").read_text(encoding="utf-8")
    selector = body[body.index("thinkingMode.current ==="):]
    selector = selector[: selector.index(";")]
    for mode in MODES:
        if mode == "think":
            continue          # the fall-through default
        assert f'"{mode}"' in selector, (
            f"her face does not handle {mode!r}, so that animation can never appear"
        )
    # And it must not be matching names nothing sends.
    for invented in ("search", "look"):
        assert f'"{invented}"' not in selector, (
            f"{invented!r} is not a mode any service emits"
        )


def test_the_sprite_draws_every_one_of_them() -> None:
    sprite = (ROOT / "dashboard/src/kendraSprite.ts").read_text(encoding="utf-8")
    declared = re.search(r"export type ThinkingMode =([^;]+);", sprite)
    assert declared, "ThinkingMode is not declared"
    names = set(re.findall(r'"([a-z]+)"', declared.group(1)))
    assert names == set(MODES), f"sprite declares {sorted(names)}, expected {sorted(MODES)}"
    # Each one has to be drawn differently, or they are aliases in disguise.
    bubble = sprite[sprite.index("private drawThoughtBubble"):]
    bubble = bubble[: bubble.index("\n  /**", 10)]
    assert '"research"' in bubble and '"sight"' in bubble


def test_nothing_publishes_a_mode_the_face_cannot_draw() -> None:
    """The whole chain, checked as one vocabulary."""
    voice = (ROOT / "kendra/voice/service.py").read_text(encoding="utf-8")
    published = set(re.findall(r'thinking_mode\s*=\s*"([a-z]+)"', voice))
    published |= set(re.findall(r'_leds\(thinking=True, thinking_mode="([a-z]+)"', voice))
    for mode in published:
        assert mode in MODES, f"voice publishes {mode!r}, which is outside the vocabulary"


def test_her_idle_gaze_asks_a_short_question_of_the_onnx_eye() -> None:
    """Ambient vision was paying the slow, inventive prompt every few minutes.

    Measured in kendra/vision/moondream_onnx.py: free-form description is
    12.5 s and "invents detail -- avoid"; a short targeted question is ~1 s
    and correct. Ambient checks the room is quiet BEFORE it looks, but nothing
    interrupts it once it has, so every ambient look opened a window in which
    a real sight question queued behind it. A shorter look is a shorter
    window -- and less invention in what she claims to have seen.
    """
    import inspect

    from kendra.vision.service import VisionService

    chooser = inspect.getsource(VisionService._ambient_question)
    assert "moondream_onnx" in chooser
    assert "AMBIENT_LOOK" in chooser and "STRUCTURED_LOOK" in chooser
    # Short means short.
    assert len(VisionService.AMBIENT_LOOK.split()) <= 10
    assert len(VisionService.STRUCTURED_LOOK.split()) > 15
    # And the caption-only eye keeps the long one, because it needs it.
    body = inspect.getsource(VisionService._ambient_tick)
    assert "_ambient_question()" in body, "ambient must go through the chooser"
