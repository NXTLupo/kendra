"""Deterministic sky facts — computed, never searched.

Kendra confidently told Jonathan the moon was "a new moon in Cancer" when it
was a first-quarter moon in Scorpius. Two failures stacked: the question was
not routed to research at all, and when it finally was, the web search
returned JavaScript star-map sites whose text contains no data — only
marketing copy ("see tonight's moon phase, computed in your browser"). She
had nothing to report and invented the specifics.

Astronomy does not need scraping. Moon phase and the moon's position along
the ecliptic are closed-form functions of the date, so this module computes
them locally: correct offline, identical on the Pi, no network, no scraping,
sub-millisecond.

Accuracy check against an independent source for 2026-08-20:
    computed  First Quarter, 50% illuminated, Scorpius
    reference First Quarter, ~53% illuminated, Scorpius (near Antares)

Precision is deliberately modest (Meeus's truncated lunar series): good to
roughly a degree of ecliptic longitude and a few percent of illumination,
which is far beyond what a spoken answer needs. Rise/set times and planet
positions are NOT computed here — they depend on the observer's latitude and
deserve a real ephemeris rather than a guess.
"""

from __future__ import annotations

import datetime as dt
import math

SYNODIC_MONTH = 29.530588853
# A known new moon, used as the phase anchor.
NEW_MOON_EPOCH = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.UTC)
J2000 = dt.datetime(2000, 1, 1, 12, 0, tzinfo=dt.UTC)

# Phase names by fraction of the lunation (0 = new, 0.5 = full).
_PHASES = [
    (0.02, "new moon"), (0.24, "waxing crescent"), (0.28, "first quarter"),
    (0.48, "waxing gibbous"), (0.52, "full moon"), (0.72, "waning gibbous"),
    (0.78, "last quarter"), (0.98, "waning crescent"), (1.01, "new moon"),
]

# IAU constellation boundaries along the ecliptic, as UPPER limits of
# ecliptic longitude. Ophiuchus is included: the ecliptic genuinely passes
# through it, whatever astrology says.
_ECLIPTIC_BOUNDS = [
    (28.7, "Pisces"), (53.4, "Aries"), (90.4, "Taurus"), (118.0, "Gemini"),
    (138.1, "Cancer"), (174.0, "Leo"), (217.8, "Virgo"), (241.1, "Libra"),
    (247.7, "Scorpius"), (266.6, "Ophiuchus"), (299.7, "Sagittarius"),
    (327.6, "Capricornus"), (351.6, "Aquarius"), (360.1, "Pisces"),
]

# The bright star the moon passes near, per constellation — the detail that
# makes a spoken answer useful rather than technical.
_LANDMARKS = {
    "Taurus": "Aldebaran", "Gemini": "Pollux", "Leo": "Regulus",
    "Virgo": "Spica", "Scorpius": "Antares", "Sagittarius": "Nunki",
    "Aquarius": "Sadalsuud", "Pisces": "Alrescha", "Aries": "Hamal",
    "Cancer": "the Beehive Cluster", "Libra": "Zubenelgenubi",
    "Ophiuchus": "Rasalhague", "Capricornus": "Deneb Algedi",
}


def moon_state(now: dt.datetime | None = None) -> dict[str, object]:
    """Moon phase, illumination and constellation for a moment in time."""
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)

    age_days = (now - NEW_MOON_EPOCH).total_seconds() / 86400.0 % SYNODIC_MONTH
    fraction = age_days / SYNODIC_MONTH
    illumination = (1 - math.cos(2 * math.pi * fraction)) / 2
    phase = next(name for limit, name in _PHASES if fraction < limit)

    # Ecliptic longitude from J2000 (a different epoch from the phase
    # anchor above — mixing them up put the moon 64 degrees off, in the
    # wrong constellation entirely).
    days = (now - J2000).total_seconds() / 86400.0
    mean_longitude = (218.316 + 13.176396 * days) % 360
    mean_anomaly = math.radians((134.963 + 13.064993 * days) % 360)
    longitude = (mean_longitude + 6.289 * math.sin(mean_anomaly)) % 360
    constellation = next(name for limit, name in _ECLIPTIC_BOUNDS if longitude < limit)

    return {
        "phase": phase,
        "illumination_pct": round(illumination * 100),
        "age_days": round(age_days, 1),
        "waxing": fraction < 0.5,
        "constellation": constellation,
        "near_star": _LANDMARKS.get(constellation),
        "ecliptic_longitude": round(longitude, 1),
    }


def spoken_sky_note(now: dt.datetime | None = None) -> str:
    """One compact, speakable line of fact for her prompt."""
    state = moon_state(now)
    near = state["near_star"]
    near_text = f", near {near}" if near else ""
    return (
        f"Tonight the moon is a {state['phase']} at about "
        f"{state['illumination_pct']}% illumination, "
        f"{'waxing' if state['waxing'] else 'waning'}, currently in the "
        f"constellation {state['constellation']}{near_text}."
    )
