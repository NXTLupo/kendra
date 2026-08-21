"""What Kendra can perform, and how each performance is staged.

One table, so adding a behaviour is a data change rather than new code.
Each entry pairs a vocal style with body choreography, lights and a
duration ceiling — the "every expressive act is a coordinated
performance" rule from the brief, made declarative.

`prompt` is what her language model is asked to write. Behaviours whose
text is fixed (humming, laughing) carry `generate=False` so no inference
happens at all — those must feel instant.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Behavior:
    name: str
    vocal_style: str
    gesture: str
    head: str | None
    light: str | None
    duration_s: float
    generate: bool = True
    prompt: str = ""
    intensity: float = 0.45
    tempo_bpm: int | None = None


CATALOGUE: dict[str, Behavior] = {
    "sing": Behavior(
        "sing", "singing", "side_to_side_sway", "beat_nod", "warm_pulse", 20.0,
        prompt=("Write 4 short singable lines. If he named a song, use ITS OWN "
                "well-known words only if they are traditional or public domain "
                "(nursery rhymes, folk songs); otherwise write your own lyrics on "
                "that theme. Short lines, simple rhyme. Lyrics only."),
        intensity=0.45, tempo_bpm=92),
    "hum": Behavior(
        "hum", "humming", "thinking_shift", "curious_tilt", "slow_breathe", 6.0,
        generate=False, intensity=0.25),
    "rap": Behavior(
        "rap", "rapping", "side_to_side_sway", "beat_nod", "beat_pulse", 20.0,
        prompt=("Write 4 to 6 short rap lines with a strong beat and simple "
                "rhymes. Original words only. Lines only, no title."),
        intensity=0.6, tempo_bpm=96),
    "poem": Behavior(
        "poem", "dramatic", "side_to_side_sway", "look_away", "slow_breathe", 22.0,
        prompt=("Write a short poem of 4 to 6 lines. Original, vivid, a little "
                "wry. Lines only, no title, no preamble."),
        intensity=0.3),
    "joke": Behavior(
        "joke", "playful", "small_bounce", "curious_tilt", "warm_pulse", 12.0,
        prompt="Tell ONE short original joke, two sentences at most. Just the joke.",
        intensity=0.4),
    "riddle": Behavior(
        "riddle", "playful", "curious_tilt", "lean_forward", "warm_pulse", 14.0,
        prompt=("Ask ONE short riddle and stop — do not reveal the answer. "
                "Two sentences at most."),
        intensity=0.35),
    "story": Behavior(
        "story", "dramatic", "lean_forward", "curious_tilt", "slow_breathe", 28.0,
        prompt=("Tell a very short original story, 4 to 6 sentences, with a "
                "beginning and an ending."),
        intensity=0.3),
    "laugh": Behavior(
        "laugh", "playful", "small_bounce", None, "warm_pulse", 4.0,
        generate=False, intensity=0.4),
    "chuckle": Behavior(
        "chuckle", "dry", "curious_tilt", None, None, 3.0,
        generate=False, intensity=0.2),
    "whisper": Behavior(
        "whisper", "whisper", "lean_forward", None, "dim", 12.0,
        prompt="Say the following as a secret, in one or two short sentences.",
        intensity=0.2),
    "celebrate": Behavior(
        "celebrate", "excited", "victory_pose", "beat_nod", "bright_flash", 8.0,
        prompt="React with delight in one short sentence.",
        intensity=0.7),
    "dance": Behavior(
        "dance", "playful", "playful_spin", "beat_nod", "rainbow_sweep", 12.0,
        generate=False, intensity=0.6, tempo_bpm=104),
    "music": Behavior(
        "music", "normal", "side_to_side_sway", "beat_nod", "beat_pulse", 12.0,
        generate=False, intensity=0.5, tempo_bpm=108),
    "bow": Behavior(
        "bow", "warm", "full_bow", None, "warm_pulse", 6.0,
        generate=False, intensity=0.4),
    "stretch": Behavior(
        "stretch", "sleepy", "stretch", None, "slow_breathe", 6.0,
        generate=False, intensity=0.4),
    "think": Behavior(
        "think", "normal", "thinking_shift", "look_away", "slow_breathe", 6.0,
        generate=False, intensity=0.2),
    "surprise": Behavior(
        "surprise", "excited", "surprised_recoil", None, "bright_flash", 5.0,
        prompt="React with genuine surprise in one short sentence.",
        intensity=0.6),
}

# Fixed vocalisations: no inference, so they land instantly. Several
# variants each, because one repeated laugh is the fastest way to make a
# companion unbearable.
FIXED_TEXT: dict[str, list[str]] = {
    "laugh": ["Ha! That's good.", "Heh — okay, that got me.", "Ha ha! Oh, that's funny."],
    "chuckle": ["Heh.", "Hm. Cute.", "Heh — nice."],
    "hum": [""],  # the vocal style supplies the humming itself
    "dance": ["Okay, watch this.", "You asked for it.", "Here we go!"],
    "bow": ["Thank you, thank you.", "You're too kind.", "Ta-da."],
    "stretch": ["Mmm — that's better.", "Okay, unfolding.", "Ahh."],
    "think": ["Hmm.", "Let me think.", "Hmmm, hang on."],
}
