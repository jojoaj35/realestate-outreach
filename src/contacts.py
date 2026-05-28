"""Phone-number normalization helpers (E.164), shared across modules."""
from __future__ import annotations

import phonenumbers


def normalize_phone(raw: str, default_region: str = "US") -> str:
    """Return an E.164 string (e.g. +12105551234), or "" if unparseable."""
    if not raw:
        return ""
    raw = str(raw).strip()
    try:
        num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(num):
        return ""
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def same_number(a: str, b: str) -> bool:
    """Compare two phone numbers after normalization."""
    na, nb = normalize_phone(a), normalize_phone(b)
    return bool(na) and na == nb
