"""Cold-message templates + personalization.

Five rotating variants, each ending with the required opt-out line. The
``specific_issue`` is derived from the scorer's reasons so the message feels
observed rather than mass-blasted.
"""
from __future__ import annotations

import random

from config import settings

OPT_OUT = "Reply STOP and I won't message again."

TEMPLATES = [
    "Hi {first_name}, I'm {sender} with {business}. I came across your listing at "
    "{address} and noticed {specific_issue}. We do fast, high-end real estate "
    "photography that helps listings show better online. Open to a quick look at "
    "what we'd do differently? {opt_out}",

    "Hey {first_name} — {sender} here ({business}). Saw {address} pop up and "
    "thought the photos could be working harder for you ({specific_issue}). "
    "We turn listings around in 24-48h. Worth a quick chat? {opt_out}",

    "Hi {first_name}, this is {sender} from {business}. Your {address} listing "
    "caught my eye — {specific_issue}. Great photos are usually the cheapest way "
    "to get more showings. Happy to send a couple of before/after examples. "
    "{opt_out}",

    "{first_name}, hi! {sender} with {business}. Noticed {specific_issue} on your "
    "listing at {address}. We specialize in making homes look their best for the "
    "MLS. Can I send over our pricing? {opt_out}",

    "Hi {first_name}, {sender} from {business} here. I help agents win listings "
    "with standout photography. On {address} I noticed {specific_issue} — an easy "
    "fix that tends to lift click-through. Interested in a sample? {opt_out}",
]

# Fallback when the scorer produced no specific reason.
_GENERIC_ISSUE = "the photos could be sharper and brighter"


def issue_phrase(score_reasons: str) -> str:
    """Turn the scorer's reason string into a short, natural phrase."""
    r = (score_reasons or "").lower()
    if "blurry" in r:
        return "a few of the shots looked a little soft/blurry"
    if "exposed" in r or "exposure" in r:
        return "some rooms looked under/over-exposed"
    if "only" in r and "photo" in r:
        return "the listing had only a handful of photos"
    if "professional style" in r:
        return "the photos could pop a lot more"
    if "portrait" in r:
        return "some photos were shot in portrait instead of wide"
    return _GENERIC_ISSUE


def render(row: dict, seed: int | None = None) -> str:
    """Build a personalized message from a queue row."""
    rng = random.Random(seed)
    template = rng.choice(TEMPLATES)
    name = (row.get("agent_name") or "").split(" ")[0] or "there"
    return template.format(
        first_name=name,
        sender=settings.sender_first_name,
        business=settings.business_name,
        address=row.get("address") or "your recent listing",
        specific_issue=issue_phrase(row.get("score_reasons", "")),
        opt_out=OPT_OUT,
    )
