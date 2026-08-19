from kendra.agent.movement import announce, parse_movement
from kendra.body.locomotion import DEFAULT_PROFILE, feet_to_metres, segment_plan


def test_full_command_vocabulary():
    cases = {
        "Come here.": ("approach", None, None),
        "Come here girl!": ("approach", None, None),
        "Go away.": ("retreat", 0.6, None),
        "Back up.": ("backward", None, None),
        "Back up two feet": ("backward", feet_to_metres(2), None),
        "Turn left.": ("turn", None, -90.0),
        "Turn to your right": ("turn", None, 90.0),
        "Turn around.": ("turn", None, 180.0),
        "Go forward": ("forward", None, None),
        "Go forward about 4 feet.": ("forward", feet_to_metres(4), None),
        "walk forward 30 inches": ("forward", feet_to_metres(2.5), None),
        "Go to the guitar": ("goto", None, None),
        "Stop": ("stop", None, None),
        "Kendra stop": ("stop", None, None),
    }
    for text, (mode, distance, angle) in cases.items():
        intent = parse_movement(text)
        assert intent is not None, text
        assert intent.mode == mode, (text, intent.mode)
        if distance is not None:
            assert intent.distance_m == __import__("pytest").approx(distance, rel=0.02), text
        if angle is not None:
            assert intent.angle_deg == angle, text


def test_non_movement_text_is_ignored():
    for text in ["What do you see?", "Tell me about heavy metal", "I might go for a walk later"]:
        assert parse_movement(text) is None, text


def test_goto_target_is_captured():
    intent = parse_movement("Go to the kitchen table please")
    assert intent is not None and intent.mode == "goto"
    assert "kitchen" in (intent.target or "")


def test_announcements_are_warm_and_short():
    for text in ["Come here", "Back up", "Turn around", "Go forward about 4 feet"]:
        intent = parse_movement(text)
        assert intent is not None
        said = announce(intent)
        assert 2 <= len(said.split()) <= 12  # short enough to say before moving
        assert not any(word in said.lower() for word in ("servo", "gait", "pwm", "degrees", "meters"))


def test_distance_maps_to_bounded_segments():
    cycles = DEFAULT_PROFILE.cycles_for_distance(feet_to_metres(4))
    assert cycles > 1
    plan = segment_plan(cycles, per_segment=4)
    assert sum(plan) == cycles and max(plan) <= 4
