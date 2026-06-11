"""Parse the Instagram data-export DM threads (HTML) into structured JSONL.

The export stores each conversation as ``inbox/<handle>_<threadid>/message_*.html``
(and ``message_requests/`` for unaccepted threads). Each message is a
``div._a6-g`` block with an ``h2._a6-h`` sender name, a ``div._a6-p`` content
body, and a ``div._a6-o`` timestamp. The account owner is "San Antonio
Realestate Media"; every other sender is a counterparty.

Output: ``data/booked/ig_threads.jsonl`` (one JSON object per thread).
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

from .paths import IG_MESSAGES_DIR, IG_THREADS_JSONL

OWNER_NAME = os.getenv("BOOKED_IG_OWNER", "San Antonio Realestate Media")
_URL_RE = re.compile(r"https?://[^\s)]+")
_FOLDER_RE = re.compile(r"^(.*)_(\d+)$")
_TEXT_CAP = 6000


def _parse_ts(text: str) -> str:
    """'Mar 05, 2026 2:09 pm' -> ISO 8601; '' on failure."""
    if not text:
        return ""
    text = text.replace("\u202f", " ").strip()
    for fmt in ("%b %d, %Y %I:%M %p", "%b %d, %Y %I:%M%p"):
        try:
            return dt.datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return ""


def handle_from_folder(folder_name: str) -> str:
    m = _FOLDER_RE.match(folder_name)
    if not m:
        return ""
    handle = m.group(1)
    # Pure-numeric prefixes are group/system threads with no real handle.
    return "" if handle.isdigit() else handle.lower()


def parse_thread(folder: Path, source: str) -> dict | None:
    files = sorted(glob.glob(str(folder / "message_*.html")))
    if not files:
        return None

    display_name = ""
    senders: set[str] = set()
    owner_msgs: list[str] = []
    their_msgs: list[str] = []
    links: list[str] = []
    timestamps: list[str] = []

    for fp in files:
        html = Path(fp).read_text(encoding="utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        if not display_name and soup.title and soup.title.string:
            display_name = soup.title.string.strip()
        for block in soup.find_all("div", class_="_a6-g"):
            h = block.find("h2", class_="_a6-h")
            sender = h.get_text(strip=True) if h else ""
            body = block.find("div", class_="_a6-p")
            text = body.get_text(" ", strip=True) if body else ""
            tnode = block.find("div", class_="_a6-o")
            ts = _parse_ts(tnode.get_text(strip=True) if tnode else "")
            if ts:
                timestamps.append(ts)
            if sender:
                senders.add(sender)
            if not text:
                continue
            links.extend(_URL_RE.findall(text))
            if sender == OWNER_NAME:
                owner_msgs.append(text)
            else:
                their_msgs.append(text)

    counterparties = sorted(s for s in senders if s and s != OWNER_NAME)
    timestamps.sort()
    return {
        "source": source,
        "folder": folder.name,
        "handle": handle_from_folder(folder.name),
        "display_name": display_name,
        "counterparties": counterparties,
        "is_group": len(counterparties) > 1,
        "num_messages": len(owner_msgs) + len(their_msgs),
        "num_owner_msgs": len(owner_msgs),
        "num_their_msgs": len(their_msgs),
        "replied": len(their_msgs) > 0,
        "first_ts": timestamps[0] if timestamps else "",
        "last_ts": timestamps[-1] if timestamps else "",
        "owner_text": " \u2502 ".join(owner_msgs)[:_TEXT_CAP],
        "their_text": " \u2502 ".join(their_msgs)[:_TEXT_CAP],
        "num_links": len(links),
    }


def ingest(messages_dir: Path = IG_MESSAGES_DIR, out: Path = IG_THREADS_JSONL) -> int:
    if not messages_dir.exists():
        print(f"[dm] IG messages dir not found: {messages_dir}")
        return 0

    threads: list[dict] = []
    for source, sub in (("inbox", "inbox"), ("request", "message_requests")):
        base = messages_dir / sub
        if not base.exists():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir()):
            rec = parse_thread(folder, source)
            if rec:
                threads.append(rec)

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for t in threads:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    replied = sum(1 for t in threads if t["replied"] and not t["is_group"])
    groups = sum(1 for t in threads if t["is_group"])
    with_handle = sum(1 for t in threads if t["handle"])
    print(
        f"[dm] {len(threads)} threads -> {out}\n"
        f"  replied(1:1)={replied}  groups={groups}  with_handle={with_handle}"
    )
    return len(threads)


if __name__ == "__main__":
    ingest()
