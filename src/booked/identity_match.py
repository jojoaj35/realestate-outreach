"""Resolve payments + iMessage + Instagram into a unified contact table.

Each contact is an outreach target on a single channel (an iMessage phone number
or an Instagram thread). We attach payment evidence to contacts using, in
priority order:

    1. phone (E.164 exact)        -> definitive   (conf 1.00)
    2. email (exact)              -> very strong   (conf 0.95)
    3. full name (fuzzy, >=2 tok) -> strong        (conf 0.70-0.85)
    4. first name only (iMessage) -> weak          (conf 0.45)

A payment that matches both an iMessage contact and an Instagram contact bridges
them into the same ``person_id`` (the same agent reached on two channels).

Output: ``data/booked/identities.csv`` (one row per contact, booked flag +
payment evidence) used by ``label_booked`` and ``features``.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from .paths import (
    IDENTITIES_CSV,
    IG_THREADS_JSONL,
    IMSG_THREADS_JSONL,
    PAYMENTS_CSV,
)

# Generic business words that make single-token name matches unreliable.
_STOP_TOKENS = {
    "real", "estate", "realty", "realtor", "group", "homes", "home", "team",
    "properties", "llc", "the", "and", "co", "company", "luxury", "realtors",
    "content", "video", "photo", "photos", "shoot", "house", "listing",
}
_NAME_FUZZY_MIN = 88
_FULLNAME_CONF = 0.82
_FUZZY_CONF = 0.70
_FIRSTNAME_CONF = 0.45


def _norm_name(name: str) -> str:
    name = (name or "").lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _tokens(name: str) -> set[str]:
    return {t for t in _norm_name(name).split() if len(t) > 1}


def _content_tokens(name: str) -> set[str]:
    return _tokens(name) - _STOP_TOKENS


@dataclass
class Contact:
    channel: str            # "imsg" | "ig"
    key: str                # phone or handle
    name: str
    phone: str = ""
    handle: str = ""
    emails: tuple = ()
    norm_name: str = ""
    booked: int = 0
    payment_ids: list = field(default_factory=list)
    payment_sources: set = field(default_factory=set)
    payment_amount: float = 0.0
    payment_dates: list = field(default_factory=list)
    match_method: str = ""
    match_confidence: float = 0.0
    person_id: str = ""


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _load_payments(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def build_contacts() -> tuple[list[Contact], list[Contact]]:
    imsg = _load_jsonl(IMSG_THREADS_JSONL)
    ig = _load_jsonl(IG_THREADS_JSONL)

    imsg_contacts = [
        Contact(
            channel="imsg",
            key=r["phone"],
            name=r.get("first_name", ""),
            phone=r["phone"],
            emails=tuple(r.get("emails", [])),
            norm_name=_norm_name(r.get("first_name", "")),
        )
        for r in imsg
        if r.get("phone")
    ]
    ig_contacts = [
        Contact(
            channel="ig",
            key=r["handle"],
            name=r.get("display_name", ""),
            handle=r["handle"],
            norm_name=_norm_name(r.get("display_name", "")),
        )
        for r in ig
        if r.get("handle") and not r.get("is_group")
    ]
    return imsg_contacts, ig_contacts


def _attach(contact: Contact, pay: dict, method: str, conf: float) -> None:
    pid = pay.get("raw_id") or f"{pay['source']}:{pay['name']}:{pay['date']}"
    if pid in contact.payment_ids:
        return
    contact.booked = 1
    contact.payment_ids.append(pid)
    contact.payment_sources.add(pay["source"])
    try:
        contact.payment_amount += float(pay.get("amount") or 0)
    except (TypeError, ValueError):
        pass
    if pay.get("date"):
        contact.payment_dates.append(pay["date"])
    if conf > contact.match_confidence:
        contact.match_confidence = conf
        contact.match_method = method


def _best_ig_name_match(name: str, ig_by_token: dict, ig_contacts: list[Contact]):
    """Return (contact, score) for the best fuzzy IG display-name match."""
    ctoks = _content_tokens(name)
    if not ctoks:
        return None, 0
    # Candidate set: IG contacts sharing at least one content token.
    cand_ids = set()
    for tok in ctoks:
        cand_ids |= ig_by_token.get(tok, set())
    if not cand_ids:
        return None, 0
    target = _norm_name(name)
    best, best_score = None, 0
    for idx in cand_ids:
        c = ig_contacts[idx]
        score = fuzz.token_set_ratio(target, c.norm_name)
        if score > best_score:
            best, best_score = c, score
    return best, best_score


def match(out: Path = IDENTITIES_CSV) -> list[Contact]:
    payments = _load_payments(PAYMENTS_CSV)
    imsg_contacts, ig_contacts = build_contacts()

    # Indexes for fast lookup.
    imsg_by_phone = {c.phone: c for c in imsg_contacts if c.phone}
    imsg_by_email = {}
    for c in imsg_contacts:
        for e in c.emails:
            imsg_by_email.setdefault(e.lower(), []).append(c)
    imsg_by_first = {}
    for c in imsg_contacts:
        if c.norm_name:
            imsg_by_first.setdefault(c.norm_name, []).append(c)
    ig_by_token: dict[str, set] = {}
    for i, c in enumerate(ig_contacts):
        for tok in _content_tokens(c.name):
            ig_by_token.setdefault(tok, set()).add(i)

    person_seq = 0
    unmatched_payments: list[dict] = []

    for pay in payments:
        phone = pay.get("phone", "").strip()
        email = pay.get("email", "").strip().lower()
        name = pay.get("name", "").strip()
        note = pay.get("note", "").strip()

        imsg_hit: Contact | None = None
        ig_hit: Contact | None = None

        # 1. phone -> iMessage (definitive)
        if phone and phone in imsg_by_phone:
            imsg_hit = imsg_by_phone[phone]
            _attach(imsg_hit, pay, "phone", 1.00)

        # 2. email -> iMessage
        if not imsg_hit and email and email in imsg_by_email:
            imsg_hit = imsg_by_email[email][0]
            _attach(imsg_hit, pay, "email", 0.95)

        # 3. full-name fuzzy -> Instagram (name or cashapp note).
        # Require >=2 shared content tokens so single common first names
        # ("Ashley", "Jeff") cannot match an arbitrary IG profile.
        for candidate_name in filter(None, [name, note]):
            c, score = _best_ig_name_match(candidate_name, ig_by_token, ig_contacts)
            if not c or score < _NAME_FUZZY_MIN:
                continue
            shared = _content_tokens(candidate_name) & _content_tokens(c.name)
            if len(shared) < 2:
                continue
            conf = _FULLNAME_CONF if score >= 96 else _FUZZY_CONF
            ig_hit = c
            _attach(ig_hit, pay, f"ig_name({int(score)})", conf)
            break

        # 4. first-name only -> iMessage (weak; only if nothing better matched)
        if not imsg_hit and not ig_hit and name:
            first = _norm_name(name.split()[0])
            cands = imsg_by_first.get(first, [])
            if len(cands) == 1:  # unambiguous first-name match only
                imsg_hit = cands[0]
                _attach(imsg_hit, pay, "first_name", _FIRSTNAME_CONF)

        # bridge cross-channel identity: a payment matching both channels means
        # the same agent was reached on iMessage and Instagram.
        hits = [c for c in (imsg_hit, ig_hit) if c]
        if hits:
            pid = next((c.person_id for c in hits if c.person_id), "")
            if not pid:
                person_seq += 1
                pid = f"p{person_seq:04d}"
            for c in hits:
                c.person_id = pid
        else:
            unmatched_payments.append(pay)

    all_contacts = imsg_contacts + ig_contacts
    _write(all_contacts, out)

    booked_imsg = sum(1 for c in imsg_contacts if c.booked)
    booked_ig = sum(1 for c in ig_contacts if c.booked)
    print(
        f"[match] contacts: imsg={len(imsg_contacts)} ig={len(ig_contacts)}\n"
        f"  payments matched: imsg={booked_imsg} ig={booked_ig}  "
        f"unmatched_payments={len(unmatched_payments)}/{len(payments)}\n"
        f"  -> {out}"
    )
    return all_contacts


def _write(contacts: list[Contact], out: Path) -> None:
    fields = [
        "person_id", "channel", "key", "name", "phone", "handle", "emails",
        "booked", "payment_sources", "payment_count", "payment_amount",
        "payment_dates", "payment_ids", "match_method", "match_confidence",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in contacts:
            w.writerow({
                "person_id": c.person_id,
                "channel": c.channel,
                "key": c.key,
                "name": c.name,
                "phone": c.phone,
                "handle": c.handle,
                "emails": ";".join(c.emails),
                "booked": c.booked,
                "payment_sources": ";".join(sorted(c.payment_sources)),
                "payment_count": len(c.payment_ids),
                "payment_amount": round(c.payment_amount, 2),
                "payment_dates": ";".join(c.payment_dates),
                "payment_ids": ";".join(c.payment_ids),
                "match_method": c.match_method,
                "match_confidence": round(c.match_confidence, 2),
            })


if __name__ == "__main__":
    match()
