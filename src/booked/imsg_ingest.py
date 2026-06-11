"""Parse the iMessage outreach CSV into structured per-contact threads.

The CSV has one row per phone number with the full conversation in a single
``Conversation`` field formatted as ``[YYYY-MM-DD HH:MM:SS] Speaker: text``
where Speaker is "Joel" (owner) or "Them" (counterparty). There are no contact
names, so we recover the counterparty's first name from Joel's opening greeting
and pull any email addresses they shared (e.g. "send photos to ...").

Output: ``data/booked/imsg_threads.jsonl`` (one JSON object per phone number).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from contacts import normalize_phone

from .paths import IMESSAGE_CSV, IMSG_THREADS_JSONL

_MSG_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\s*(Joel|Them):\s*", re.I)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_GREETING_RE = re.compile(
    r"\b(?:hi|hello|hey|good\s+morning|good\s+afternoon|good\s+evening)\s+([A-Z][a-zA-Z]+)",
    re.I,
)
# Words that follow a greeting but are not names.
_NOT_NAME = {
    "this", "im", "i", "there", "good", "how", "are", "hope", "just",
    "is", "can", "you", "the", "u", "do", "did", "my", "we", "its", "it",
    "hope", "sorry", "again", "happy", "have", "thanks", "thank",
}


def _split_messages(conversation: str) -> list[tuple[str, str, str]]:
    """Return list of (timestamp, speaker, text)."""
    msgs: list[tuple[str, str, str]] = []
    matches = list(_MSG_RE.finditer(conversation))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(conversation)
        text = conversation[start:end].strip()
        msgs.append((m.group(1), m.group(2).lower(), text))
    return msgs


def extract_first_name(owner_msgs: list[str]) -> str:
    for text in owner_msgs:
        m = _GREETING_RE.search(text)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in _NOT_NAME:
            continue
        return name
    return ""


def parse_row(row: dict) -> dict:
    phone = normalize_phone(row.get("Contact", ""))
    conversation = row.get("Conversation", "") or ""
    msgs = _split_messages(conversation)
    owner_msgs = [t for _, spk, t in msgs if spk == "joel"]
    their_msgs = [t for _, spk, t in msgs if spk == "them"]
    their_ts = [ts for ts, spk, _ in msgs if spk == "them"]
    emails = sorted(set(_EMAIL_RE.findall(conversation)))

    return {
        "phone": phone,
        "raw_contact": row.get("Contact", ""),
        "first_name": extract_first_name(owner_msgs),
        "emails": emails,
        "first_contacted": row.get("First Contacted", ""),
        "last_message": row.get("Last Message", ""),
        "replied": (row.get("Replied?", "").strip().lower() == "yes"),
        "num_owner_msgs": len(owner_msgs),
        "num_their_msgs": len(their_msgs),
        "last_their_ts": max(their_ts) if their_ts else "",
        "owner_text": " \u2502 ".join(owner_msgs)[:6000],
        "their_text": " \u2502 ".join(their_msgs)[:6000],
    }


def ingest(csv_path: Path = IMESSAGE_CSV, out: Path = IMSG_THREADS_JSONL) -> int:
    if not csv_path.exists():
        print(f"[imsg] iMessage CSV not found: {csv_path}")
        return 0

    rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            rec = parse_row(raw)
            if rec["phone"]:
                rows.append(rec)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    replied = sum(1 for r in rows if r["replied"])
    named = sum(1 for r in rows if r["first_name"])
    with_email = sum(1 for r in rows if r["emails"])
    print(
        f"[imsg] {len(rows)} contacts -> {out}\n"
        f"  replied={replied}  with_first_name={named}  with_email={with_email}"
    )
    return len(rows)


if __name__ == "__main__":
    ingest()
