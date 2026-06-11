"""Instagram outreach queue — separate from the listing queue.

Profiles discovered on Instagram live in ``data/ig_queue.csv``; opted-out handles
in ``data/ig_dnc.csv``. Same CSV pattern as ``store.py``.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
from pathlib import Path
from typing import Iterable

from config import DATA_DIR

IG_FIELDNAMES = [
    "ig_handle",
    "display_name",
    "city",
    "bio",
    "profile_url",
    "follower_count",
    "source",
    "status",
    "match_score",
    "city_score",
    "book_score",
    "rank_reasons",
    "sent_at",
    "message_sent",
    "updated_at",
]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def normalize_handle(handle: str) -> str:
    """Lowercase handle without @ or URL fragments."""
    h = (handle or "").strip().lower()
    h = h.lstrip("@")
    if "instagram.com/" in h:
        m = re.search(r"instagram\.com/([^/?#]+)", h)
        if m:
            h = m.group(1)
    h = h.split("?")[0].strip("/")
    if h in {"p", "reel", "stories", "explore", "accounts", "direct"}:
        return ""
    return h


def profile_to_row(profile: dict) -> dict:
    handle = normalize_handle(profile.get("ig_handle") or profile.get("handle", ""))
    rank_reasons = profile.get("rank_reasons", "")
    if isinstance(rank_reasons, list):
        rank_reasons = json.dumps(rank_reasons)
    return {
        "ig_handle": handle,
        "display_name": profile.get("display_name", ""),
        "city": profile.get("city", ""),
        "bio": profile.get("bio", ""),
        "profile_url": profile.get("profile_url") or (f"https://www.instagram.com/{handle}/" if handle else ""),
        "follower_count": profile.get("follower_count", ""),
        "source": profile.get("source", ""),
        "status": profile.get("status", "queued"),
        "match_score": profile.get("match_score", ""),
        "city_score": profile.get("city_score", ""),
        "book_score": profile.get("book_score", ""),
        "rank_reasons": rank_reasons,
        "sent_at": profile.get("sent_at", ""),
        "message_sent": profile.get("message_sent", ""),
        "updated_at": _now(),
    }


class IgStore:
    """CSV-backed Instagram profile queue."""

    def __init__(self) -> None:
        self.queue_path = DATA_DIR / "ig_queue.csv"
        self.dnc_path = DATA_DIR / "ig_dnc.csv"
        if not self.queue_path.exists():
            self._write_all([])
        if not self.dnc_path.exists():
            self.dnc_path.write_text("handle,reason,added_at\n")

    def _read_all(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        with self.queue_path.open(newline="") as f:
            return list(csv.DictReader(f))

    def _write_all(self, rows: Iterable[dict]) -> None:
        with self.queue_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=IG_FIELDNAMES)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in IG_FIELDNAMES})

    def all(self) -> list[dict]:
        return self._read_all()

    def get(self, handle: str) -> dict | None:
        target = normalize_handle(handle)
        if not target:
            return None
        for r in self._read_all():
            if normalize_handle(r.get("ig_handle", "")) == target:
                return r
        return None

    def get_by_status(self, status: str) -> list[dict]:
        return [r for r in self._read_all() if r.get("status") == status]

    def upsert_profiles(self, profiles: list[dict]) -> tuple[int, int]:
        rows = self._read_all()
        by_handle = {normalize_handle(r["ig_handle"]): r for r in rows if r.get("ig_handle")}
        added = updated = 0
        for profile in profiles:
            row = profile_to_row(profile)
            handle = row["ig_handle"]
            if not handle:
                continue
            if handle in by_handle:
                existing = by_handle[handle]
                for k in (
                    "display_name", "city", "bio", "follower_count", "source", "profile_url",
                    "match_score", "city_score", "book_score", "rank_reasons",
                ):
                    if row.get(k):
                        existing[k] = row[k]
                if existing.get("status") in ("", "discovered"):
                    existing["status"] = row.get("status") or existing["status"]
                existing["updated_at"] = _now()
                updated += 1
            else:
                by_handle[handle] = row
                added += 1
        self._write_all(by_handle.values())
        return added, updated

    def update(self, handle: str, **fields) -> None:
        target = normalize_handle(handle)
        rows = self._read_all()
        for r in rows:
            if normalize_handle(r.get("ig_handle", "")) == target:
                r.update({k: v for k, v in fields.items() if k in IG_FIELDNAMES})
                r["updated_at"] = _now()
                break
        self._write_all(rows)

    def _dnc_handles(self) -> set[str]:
        if not self.dnc_path.exists():
            return set()
        with self.dnc_path.open(newline="") as f:
            return {normalize_handle(row["handle"]) for row in csv.DictReader(f) if row.get("handle")}

    def is_dnc(self, handle: str) -> bool:
        h = normalize_handle(handle)
        return bool(h) and h in self._dnc_handles()

    def add_dnc(self, handle: str, reason: str = "") -> None:
        h = normalize_handle(handle)
        if not h or h in self._dnc_handles():
            return
        with self.dnc_path.open("a", newline="") as f:
            csv.writer(f).writerow([h, reason, _now()])
        row = self.get(h)
        if row:
            self.update(h, status="dnc")


_ig_store: IgStore | None = None


def get_ig_store() -> IgStore:
    global _ig_store
    if _ig_store is None:
        _ig_store = IgStore()
    return _ig_store
