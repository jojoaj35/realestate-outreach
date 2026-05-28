"""Poll Messages.app for replies, classify them, and update the queue.

Reads ~/Library/Messages/chat.db read-only, finds inbound messages from
numbers we've texted, classifies each into interested / not_now / stop / other
(Claude Haiku, with a keyword fallback), and updates the store. A "stop" reply
is added to the do-not-contact list immediately.

Needs Full Disk Access for your terminal/Python (System Settings > Privacy &
Security > Full Disk Access) to read chat.db.

Usage:
    python src/reply_poller.py           # process new replies since last run
    python src/reply_poller.py --hours 24
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, settings
from contacts import normalize_phone
from store import get_store

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"
STATE_PATH = DATA_DIR / "reply_state.json"
APPLE_EPOCH = 978307200  # 2001-01-01 in unix seconds

STOP_WORDS = {"stop", "unsubscribe", "remove me", "do not text", "opt out", "leave me alone"}
YES_WORDS = {"yes", "interested", "sure", "tell me more", "how much", "pricing", "price",
             "info", "send", "let's", "lets", "call me", "sounds good"}


def _apple_ts_to_unix(ts: int) -> float:
    # macOS stores nanoseconds since 2001; older versions used seconds.
    seconds = ts / 1e9 if ts > 1e12 else ts
    return seconds + APPLE_EPOCH


def _load_state() -> float:
    if STATE_PATH.exists():
        try:
            return float(json.loads(STATE_PATH.read_text()).get("last_unix", 0))
        except (json.JSONDecodeError, ValueError):
            return 0.0
    return 0.0


def _save_state(last_unix: float) -> None:
    STATE_PATH.write_text(json.dumps({"last_unix": last_unix}, indent=2))


def fetch_incoming(since_unix: float) -> list[dict]:
    """Return inbound messages newer than ``since_unix`` as {phone, text, unix}."""
    if not CHAT_DB.exists():
        raise FileNotFoundError(f"chat.db not found at {CHAT_DB} (are you on macOS?)")
    con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT h.id, m.text, m.date
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
            WHERE m.is_from_me = 0 AND m.text IS NOT NULL
            ORDER BY m.date ASC
            """
        ).fetchall()
    except sqlite3.OperationalError as e:
        con.close()
        raise PermissionError(
            f"Could not read chat.db ({e}). Grant Full Disk Access to your terminal."
        )
    con.close()

    out = []
    for handle_id, text, date in rows:
        unix = _apple_ts_to_unix(date)
        if unix > since_unix and text:
            out.append({"phone": handle_id, "text": text, "unix": unix})
    return out


def classify_keyword(text: str) -> str:
    t = text.lower()
    if any(w in t for w in STOP_WORDS):
        return "stop"
    if any(w in t for w in YES_WORDS):
        return "interested"
    return "other"


def classify_llm(text: str) -> str:
    if not settings.anthropic_api_key:
        return classify_keyword(text)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        resp = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=8,
            system=(
                "Classify the real-estate agent's reply to a cold photography pitch "
                "into exactly one label: interested, not_now, stop, other. "
                "Reply with only the label."
            ),
            messages=[{"role": "user", "content": text[:1000]}],
        )
        label = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip().lower()
        return label if label in {"interested", "not_now", "stop", "other"} else classify_keyword(text)
    except Exception:
        return classify_keyword(text)


def poll(hours: int | None = None) -> dict:
    store = get_store()
    since = _load_state()
    if hours is not None:
        since = (dt.datetime.now() - dt.timedelta(hours=hours)).timestamp()

    messages = fetch_incoming(since)
    stats = {"messages": len(messages), "matched": 0, "stop": 0, "interested": 0}
    max_unix = since

    for msg in messages:
        max_unix = max(max_unix, msg["unix"])
        phone = normalize_phone(msg["phone"])
        if not phone:
            continue
        row = store.find_by_phone(phone)
        if not row:
            continue  # not one of our outreach targets

        stats["matched"] += 1
        label = classify_llm(msg["text"])
        fields = {"reply_text": msg["text"][:500], "reply_sentiment": label}

        if label == "stop":
            store.add_dnc(phone, reason="replied STOP")
            fields["status"] = "dnc"
            stats["stop"] += 1
        elif label == "interested":
            fields["status"] = "replied"
            stats["interested"] += 1
            print(f"  ⭐ INTERESTED: {row.get('agent_name','?')} ({phone}) — {msg['text'][:80]}")
        else:
            fields["status"] = "replied"

        store.update(row["listing_id"], **fields)

    _save_state(max_unix)
    print(f"Replies processed: {stats}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Poll Messages for replies.")
    ap.add_argument("--hours", type=int, default=None,
                    help="look back N hours instead of since last run")
    args = ap.parse_args()
    poll(hours=args.hours)


if __name__ == "__main__":
    main()
