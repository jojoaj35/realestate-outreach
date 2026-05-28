r"""Queue store — the single source of truth for listings + outreach status.

Two interchangeable backends:
  - LocalStore  : CSV files under ./data (zero setup, the default)
  - SheetStore  : Google Sheets via gspread (set STORE_BACKEND=sheets)

Both expose the same interface so the rest of the pipeline doesn't care which
is active. Pick one with ``get_store()`` (reads STORE_BACKEND from .env).

Status lifecycle:
  new -> queued -> sent -> replied -> booked
                       \-> dnc        (opt-out / do-not-contact)
                       \-> skipped    (no phone / not iMessage)
"""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Iterable

from config import DATA_DIR, settings
from contacts import normalize_phone

FIELDNAMES = [
    "listing_id",
    "address",
    "city",
    "state",
    "list_price",
    "agent_name",
    "agent_phone",
    "agent_email",
    "url",
    "photo_count",
    "score",
    "score_reasons",
    "status",
    "sent_at",
    "message_sent",
    "reply_text",
    "reply_sentiment",
    "updated_at",
]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def listing_to_row(listing: dict) -> dict:
    """Map a scored listing dict into a queue row (status decided by score)."""
    score = listing.get("score")
    status = "new"
    if isinstance(score, (int, float)):
        status = "queued" if score <= settings.outreach_score_threshold else "new"
    return {
        "listing_id": str(listing.get("listing_id", "")),
        "address": listing.get("address", ""),
        "city": listing.get("city", ""),
        "state": listing.get("state", ""),
        "list_price": listing.get("list_price", ""),
        "agent_name": listing.get("agent_name", ""),
        "agent_phone": normalize_phone(listing.get("agent_phone", "")),
        "agent_email": listing.get("agent_email", ""),
        "url": listing.get("url", ""),
        "photo_count": listing.get("photo_count", ""),
        "score": listing.get("score", ""),
        "score_reasons": listing.get("score_reasons", ""),
        "status": status,
        "sent_at": "",
        "message_sent": "",
        "reply_text": "",
        "reply_sentiment": "",
        "updated_at": _now(),
    }


class LocalStore:
    """CSV-backed store. queue.csv = listings; dnc.csv = opted-out numbers."""

    def __init__(self) -> None:
        self.queue_path = DATA_DIR / "queue.csv"
        self.dnc_path = DATA_DIR / "dnc.csv"
        if not self.queue_path.exists():
            self._write_all([])
        if not self.dnc_path.exists():
            self.dnc_path.write_text("phone,reason,added_at\n")

    # ---- low-level io -----------------------------------------------------
    def _read_all(self) -> list[dict]:
        if not self.queue_path.exists():
            return []
        with self.queue_path.open(newline="") as f:
            return list(csv.DictReader(f))

    def _write_all(self, rows: Iterable[dict]) -> None:
        with self.queue_path.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in FIELDNAMES})

    # ---- queue api --------------------------------------------------------
    def all(self) -> list[dict]:
        return self._read_all()

    def get(self, listing_id: str) -> dict | None:
        for r in self._read_all():
            if r["listing_id"] == str(listing_id):
                return r
        return None

    def get_by_status(self, status: str) -> list[dict]:
        return [r for r in self._read_all() if r.get("status") == status]

    def upsert_listings(self, listings: list[dict]) -> tuple[int, int]:
        rows = self._read_all()
        by_id = {r["listing_id"]: r for r in rows}
        added = updated = 0
        for listing in listings:
            row = listing_to_row(listing)
            lid = row["listing_id"]
            if not lid:
                continue
            if lid in by_id:
                # Refresh score fields but never clobber outreach progress.
                existing = by_id[lid]
                for k in ("score", "score_reasons", "photo_count", "agent_phone",
                          "agent_email", "agent_name"):
                    if row.get(k):
                        existing[k] = row[k]
                existing["updated_at"] = _now()
                updated += 1
            else:
                by_id[lid] = row
                added += 1
        self._write_all(by_id.values())
        return added, updated

    def update(self, listing_id: str, **fields) -> None:
        rows = self._read_all()
        for r in rows:
            if r["listing_id"] == str(listing_id):
                r.update({k: v for k, v in fields.items() if k in FIELDNAMES})
                r["updated_at"] = _now()
                break
        self._write_all(rows)

    def find_by_phone(self, phone: str) -> dict | None:
        target = normalize_phone(phone)
        if not target:
            return None
        for r in self._read_all():
            if normalize_phone(r.get("agent_phone", "")) == target:
                return r
        return None

    # ---- do-not-contact ---------------------------------------------------
    def _dnc_numbers(self) -> set[str]:
        if not self.dnc_path.exists():
            return set()
        with self.dnc_path.open(newline="") as f:
            return {normalize_phone(row["phone"]) for row in csv.DictReader(f) if row.get("phone")}

    def is_dnc(self, phone: str) -> bool:
        p = normalize_phone(phone)
        return bool(p) and p in self._dnc_numbers()

    def add_dnc(self, phone: str, reason: str = "") -> None:
        p = normalize_phone(phone)
        if not p or p in self._dnc_numbers():
            return
        with self.dnc_path.open("a", newline="") as f:
            csv.writer(f).writerow([p, reason, _now()])


class SheetStore:
    """Google Sheets backend (same interface as LocalStore).

    Requires a service-account JSON (GOOGLE_SHEETS_CREDENTIALS) shared with the
    target sheet (GOOGLE_SHEET_ID). Tabs: 'queue' and 'dnc'.
    """

    def __init__(self) -> None:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_path = Path(settings.google_sheets_credentials)
        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google Sheets credentials not found at {creds_path}. "
                "Set GOOGLE_SHEETS_CREDENTIALS or switch STORE_BACKEND=local."
            )
        if not settings.google_sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is empty in .env")

        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(str(creds_path), scopes=scopes)
        self._gc = gspread.authorize(creds)
        self._sh = self._gc.open_by_key(settings.google_sheet_id)
        self._queue = self._ensure_ws("queue", FIELDNAMES)
        self._dnc = self._ensure_ws("dnc", ["phone", "reason", "added_at"])

    def _ensure_ws(self, title: str, header: list[str]):
        try:
            ws = self._sh.worksheet(title)
        except Exception:
            ws = self._sh.add_worksheet(title=title, rows=1000, cols=max(len(header), 20))
        if ws.row_values(1) != header:
            ws.update("A1", [header])
        return ws

    def _rows(self) -> list[dict]:
        return self._queue.get_all_records()

    def all(self) -> list[dict]:
        return self._rows()

    def get(self, listing_id: str) -> dict | None:
        return next((r for r in self._rows() if str(r.get("listing_id")) == str(listing_id)), None)

    def get_by_status(self, status: str) -> list[dict]:
        return [r for r in self._rows() if r.get("status") == status]

    def _row_index(self, listing_id: str) -> int | None:
        for i, r in enumerate(self._rows(), start=2):  # row 1 is header
            if str(r.get("listing_id")) == str(listing_id):
                return i
        return None

    def upsert_listings(self, listings: list[dict]) -> tuple[int, int]:
        existing = {str(r.get("listing_id")): i for i, r in enumerate(self._rows(), start=2)}
        added = updated = 0
        new_rows = []
        for listing in listings:
            row = listing_to_row(listing)
            lid = row["listing_id"]
            if not lid:
                continue
            if lid in existing:
                self.update(lid, score=row["score"], score_reasons=row["score_reasons"],
                            photo_count=row["photo_count"])
                updated += 1
            else:
                new_rows.append([row.get(k, "") for k in FIELDNAMES])
                added += 1
        if new_rows:
            self._queue.append_rows(new_rows, value_input_option="RAW")
        return added, updated

    def update(self, listing_id: str, **fields) -> None:
        idx = self._row_index(listing_id)
        if idx is None:
            return
        fields["updated_at"] = _now()
        current = self._queue.row_values(idx)
        current += [""] * (len(FIELDNAMES) - len(current))
        for k, v in fields.items():
            if k in FIELDNAMES:
                current[FIELDNAMES.index(k)] = v
        self._queue.update(f"A{idx}", [current])

    def find_by_phone(self, phone: str) -> dict | None:
        target = normalize_phone(phone)
        if not target:
            return None
        return next(
            (r for r in self._rows() if normalize_phone(str(r.get("agent_phone", ""))) == target),
            None,
        )

    def _dnc_numbers(self) -> set[str]:
        return {normalize_phone(str(r.get("phone"))) for r in self._dnc.get_all_records() if r.get("phone")}

    def is_dnc(self, phone: str) -> bool:
        p = normalize_phone(phone)
        return bool(p) and p in self._dnc_numbers()

    def add_dnc(self, phone: str, reason: str = "") -> None:
        p = normalize_phone(phone)
        if not p or p in self._dnc_numbers():
            return
        self._dnc.append_row([p, reason, _now()], value_input_option="RAW")


def get_store():
    """Return the configured store backend."""
    if settings.store_backend == "sheets":
        return SheetStore()
    return LocalStore()
