"""Two identity layers: a small resident kernel, a retrieved deep document.

Per kendra.identity.core.v1. On a 1.7B, permanent context should establish who
Kendra IS without burying her under hundreds of identity tokens every turn.

The spec asked for the ultra-compressed kernel to be A/B tested first, so it
was, against her live model (scripts/behaviour_probe.py, N=8):

    full charter as Slot 0, 1462 tok ->  10/24
    identity kernel,          490 tok ->  19/24

The charter arm also FABRICATED a llama.cpp version number and a memory of
reading about it, which is the failure mode the kernel's explicit
research-capability clause fixes.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from kendra.brain.second_brain import SecondBrain, stem

ROOT = Path(__file__).resolve().parents[1]
KERNEL = ROOT / "charter/kernel.md"


# --- layer 1: the resident kernel -------------------------------------------

def test_the_kernel_is_slot_zero() -> None:
    from kendra.config import Settings

    settings = Settings.load("config/pc.yaml")
    assert str(settings.get("paths.charter")).endswith("kernel.md")
    # The full charter is retained as the canonical source document.
    assert str(settings.get("paths.charter_full")).endswith("charter.md")


def test_the_kernel_stays_small() -> None:
    """It is byte-identical every turn and paid on every cold prefill."""
    text = KERNEL.read_text(encoding="utf-8")
    assert len(text.split()) < 420, "the whole point is that this is not the charter"
    charter = (ROOT / "charter/charter.md").read_text(encoding="utf-8")
    assert len(text) < len(charter) / 2


def test_the_kernel_carries_what_must_be_permanent() -> None:
    """§39: identity, relationship, temperament, expertise, truthfulness,
    register, embodiment, safety, continuity."""
    text = KERNEL.read_text(encoding="utf-8").lower()
    for required in ("kendra", "jonathan", "curious", "ai", "evidence",
                     "hexapod", "safety"):
        assert required in text, required
    assert "recognisably kendra" in text or "recognizably kendra" in text


def test_the_kernel_asserts_the_research_CAPABILITY_not_just_the_habit() -> None:
    """Measured: the first kernel scored 1/5 on research and answered "I don't
    have access to real-time information" — generic-assistant language, and
    false, since she has working research tools. Stating the disposition was
    not enough to beat the base model's prior; stating the capability was."""
    text = KERNEL.read_text(encoding="utf-8").lower()
    assert "research tools" in text
    assert "live internet" in text or "network is up" in text
    assert "never say you lack access" in text


def test_the_kernel_makes_disagreement_concrete() -> None:
    """"disagree constructively" lost to the agreeable prior at 0/5. A
    concrete form — say so first, give the reason, offer the alternative —
    reached 4/8."""
    text = KERNEL.read_text(encoding="utf-8").lower()
    assert "i wouldn't" in text
    assert "never open by praising" in text


def test_the_kernel_keeps_her_speakable() -> None:
    """Everything she says goes through TTS. Dropping these would put markdown
    and emoji into her speaker."""
    text = KERNEL.read_text(encoding="utf-8").lower()
    for rule in ("spoken aloud", "no emoji", "no markdown", "cannot be said aloud"):
        assert rule in text, rule


def test_the_kernel_states_the_priority_order() -> None:
    text = KERNEL.read_text(encoding="utf-8").lower()
    line = next(ln for ln in text.splitlines() if ln.startswith("priority:"))
    order = ["safety", "truth", "collaboration", "curiosity", "playfulness"]
    positions = [line.index(word) for word in order]
    assert positions == sorted(positions), f"priority order is wrong: {line}"


# --- layer 2: the retrieved deep document -----------------------------------

@pytest.fixture()
def wiki() -> SecondBrain:
    with tempfile.TemporaryDirectory() as directory:
        brain = SecondBrain(Path(directory))
        brain.upsert_page(
            "kendra-identity-embodiment", "Kendra Embodiment",
            ["Kendra's movement should communicate attention, intention, curiosity or action."],
            aliases=["body", "move", "movement", "safe", "safety", "hexapod"],
            tags=["embodiment", "movement"], authority="canonical",
        )
        brain.upsert_page(
            "research-just-some-passing-remark", "Just Some Passing Remark",
            ["Something unrelated was mentioned once. (2026-08-01)"],
        )
        yield brain


def test_aliases_make_a_page_findable_by_what_it_is_about(wiki: SecondBrain) -> None:
    """"kendra" is a stop word for scoring — it appears on every page — so an
    identity page could not be reached by its own name."""
    hits = wiki.lookup("can you move over there safely?", limit=2)
    assert hits and hits[0]["slug"] == "kendra-identity-embodiment"


def test_stemming_reaches_the_obvious_variants() -> None:
    assert stem("moving") == stem("movement") == stem("move") or stem("moving") == stem("movement")
    assert stem("researching") == stem("research")


def test_a_canonical_page_outranks_a_compiled_one_at_equal_relevance(wiki: SecondBrain) -> None:
    """Ties were broken by whichever slug sorted higher alphabetically, so a
    page named after a passing remark beat the canonical page on the same
    subject. A genuine tie: one title word matched on each side.
    """
    wiki.upsert_page(
        "research-movement-chatter", "Movement Chatter",
        ["Someone said something about movement once. (2026-08-01)"],
    )
    hits = wiki.lookup("movement", limit=2)
    slugs = [hit["slug"] for hit in hits]
    assert slugs[0] == "kendra-identity-embodiment", slugs
    assert "research-movement-chatter" in slugs, "the compiled page should still be found"


def test_the_deep_layer_stays_out_of_unrelated_turns(wiki: SecondBrain) -> None:
    """It is retrieved only when the conversation calls for it. Otherwise it
    is exactly the identity prose the two-layer split exists to avoid."""
    for question in ("where does iced coffee come from?", "what time is it?"):
        for hit in wiki.lookup(question, limit=2):
            assert not hit["slug"].startswith("kendra-identity")


def test_the_shipped_identity_pages_are_canonical_and_aliased() -> None:
    pages = sorted((ROOT / "data/second_brain/wiki").glob("kendra-identity-*.md"))
    assert len(pages) >= 5, "the deep identity document should be split by topic"
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert "authority: canonical" in text, page.name
        assert re.search(r"^aliases: .+$", text, re.M), page.name
        assert re.search(r"^- .+$", text, re.M), f"{page.name} has no facts"
