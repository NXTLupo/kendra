from __future__ import annotations

from kendra.brain.consolidator import BrainConsolidator


def test_user_stated_quote_must_exist_in_user_text():
    assert BrainConsolidator._quote_supported("I prefer coffee", "Actually, I prefer coffee in the morning.")
    assert not BrainConsolidator._quote_supported("I prefer tea", "Actually, I prefer coffee in the morning.")


def test_empty_quote_is_not_supported():
    assert not BrainConsolidator._quote_supported(None, "anything")
    assert not BrainConsolidator._quote_supported("", "anything")
