"""Cold-message templates built from Joel's real pitch.

All variants say the same thing (same offer, same phone, same services) but
vary the wording so a single Apple ID isn't sending identical texts all day.
Content (phone, offer, website, area) comes from .env via config so you edit it
in one place.

Personalization:
  - time-aware greeting (Good morning / afternoon / evening)
  - the agent's first name when we have it
"""
from __future__ import annotations

import datetime as dt
import random

from config import settings

# Optional, low-key opt-out line (toggle with INCLUDE_OPT_OUT in .env).
OPT_OUT = " Reply STOP and I won't text again."

# Each body uses: {greeting} {sender} {area} {offer} {phone} {website_clause} {opt_out}
TEMPLATES = [
    "{greeting} My name is {sender} and I'm a real estate photographer based in "
    "the {area}. I'm reaching out to see if you'd be interested in professional "
    "photos for any of your current or future listings. I offer a variety of "
    "services, including 360-degree tour walkthrough videos, drone footage, and "
    "more.\n\nSpecial offer: {offer}\n\nYou can call or text me at {phone} to "
    "learn more{website_clause}.{opt_out}",

    "{greeting} I'm {sender}, a real estate photographer covering the {area}. I'd "
    "love to help your current or upcoming listings stand out with professional "
    "photos — I also do 360-degree walkthrough tours, drone footage, and more."
    "\n\nSpecial offer: {offer}\n\nText or call me at {phone} to learn more"
    "{website_clause}.{opt_out}",

    "{greeting} This is {sender} — I'm a real estate photographer in the {area}. "
    "Are you interested in professional photography for any current or future "
    "listings? Services include 360-degree tour videos, drone footage, and more."
    "\n\nSpecial offer: {offer}\n\nReach me anytime at {phone}{website_clause}."
    "{opt_out}",

    "{greeting} My name's {sender} and I shoot real estate photography around the "
    "{area}. I wanted to see if you'd be interested in professional photos for "
    "your listings — I also offer 360-degree walkthrough tours, drone footage, "
    "and more.\n\nSpecial offer: {offer}\n\nCall or text {phone} to learn more"
    "{website_clause}.{opt_out}",

    "{greeting} I'm {sender}, a local real estate photographer here in the "
    "{area}. If you've got listings coming up, I'd be glad to handle the photos — "
    "I also do drone footage and 360-degree walkthrough tours.\n\n"
    "Current special: {offer}\n\nFeel free to text or call {phone}{website_clause}."
    "{opt_out}",

    "{greeting} {sender} here — I do real estate photography across the {area} "
    "and wanted to introduce myself in case you ever need listing photos, drone "
    "shots, or 360 tours.\n\nRight now I'm offering {offer}\n\n"
    "You can reach me at {phone}{website_clause}.{opt_out}",

    "{greeting} I'm {sender}, a photographer specializing in real estate in the "
    "{area}. Would professional photos help any of your current or upcoming "
    "listings? I shoot stills, drone, and 360-degree walkthroughs.\n\n"
    "Special offer: {offer}\n\nText or call {phone} anytime{website_clause}."
    "{opt_out}",
]


def _greeting(name: str, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now()
    if now.hour < 12:
        base = "Good morning"
    elif now.hour < 17:
        base = "Good afternoon"
    else:
        base = "Good evening"
    return f"{base}, {name}!" if name else f"{base}!"


def _website_clause() -> str:
    if settings.website:
        return f", as well as visit {settings.website} to view my portfolio"
    return " and see my portfolio"


def render(row: dict, seed: int | None = None, now: dt.datetime | None = None) -> str:
    """Build a personalized message from a queue row."""
    rng = random.Random(seed)
    template = rng.choice(TEMPLATES)
    name = (row.get("agent_name") or "").split(" ")[0]
    return template.format(
        greeting=_greeting(name, now),
        sender=settings.sender_first_name,
        area=settings.service_area,
        offer=settings.special_offer,
        phone=settings.contact_phone,
        website_clause=_website_clause(),
        opt_out=OPT_OUT if settings.include_opt_out else "",
    )


# Instagram DMs — shorter, conversational (no SMS-style STOP line).
IG_OPT_OUT = " Just let me know if you're not interested."

IG_TEMPLATES = [
    "{greeting} I'm {sender}, a real estate photographer in the {area}. "
    "Would you be open to professional photos for your listings? "
    "I also do 360 walkthroughs and drone footage.\n\n"
    "Special offer: {offer}\n\n"
    "Text or call me at {phone}{website_clause}.{ig_opt_out}",

    "{greeting} This is {sender} — I shoot real estate around the {area}. "
    "Wanted to reach out and see if you ever need listing photos, drone, "
    "or 360 tour videos.\n\n"
    "Right now I'm running: {offer}\n\n"
    "Happy to share my portfolio — {phone}{website_clause}.{ig_opt_out}",

    "{greeting} I'm {sender}, a photographer covering the {area}. "
    "If you have any listings that could use fresh photos, I'd love to help. "
    "I do stills, drone, and walkthrough tours.\n\n"
    "{offer}\n\n"
    "Reach me at {phone}{website_clause}.{ig_opt_out}",

    "{greeting} My name's {sender} and I'm a real estate photographer in the "
    "{area}. Are you taking on listings that need photos? I offer drone, "
    "360 tours, and standard shoots.\n\n"
    "Special: {offer}\n\n"
    "{phone}{website_clause}.{ig_opt_out}",
]


def render_instagram(row: dict, seed: int | None = None, now: dt.datetime | None = None) -> str:
    """Build a personalized Instagram DM from an ig_queue row."""
    rng = random.Random(seed)
    template = rng.choice(IG_TEMPLATES)
    name = (row.get("display_name") or row.get("agent_name") or "").split(" ")[0]
    return template.format(
        greeting=_greeting(name, now),
        sender=settings.sender_first_name,
        area=settings.service_area,
        offer=settings.special_offer,
        phone=settings.contact_phone,
        website_clause=_website_clause(),
        ig_opt_out=IG_OPT_OUT,
    )
