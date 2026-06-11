"""Ingest Square invoices + Cash App transactions into one normalized table.

These payment records are the *ground truth* for who actually booked. Square is
the primary source (paid invoices with customer name/phone/email); Cash App
contributes peer-to-peer payments received from clients.

Output: ``data/booked/payments.csv`` with columns
    source, name, email, phone, note, amount, date, title, raw_id
"""
from __future__ import annotations

import csv
import datetime as dt
import re
from pathlib import Path

from contacts import normalize_phone

from .paths import CASHAPP_CSV, PAYMENTS_CSV, SQUARE_CSV

PAYMENT_FIELDS = [
    "source",
    "name",
    "email",
    "phone",
    "note",
    "amount",
    "date",
    "title",
    "raw_id",
]

# Cash App rows that represent money actually received from a person.
CASHAPP_INCOMING_TYPES = {"P2P", "Cash App Pay Payment"}


def _money(value: str) -> float:
    """Parse '$1,200.00' / '-$99.00' / '' -> float (0.0 if blank)."""
    if not value:
        return 0.0
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in ("", "-", ".", "-."):
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _iso_date(value: str) -> str:
    """Normalize assorted date strings to YYYY-MM-DD (best effort)."""
    if not value:
        return ""
    value = str(value).strip()
    # Cash App: '2026-04-17 00:28:28 CDT'  Square: '2026-06-02'
    m = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    if m:
        return m.group(1)
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def parse_square(path: Path = SQUARE_CSV) -> list[dict]:
    """Paid Square invoices -> normalized payment records."""
    if not path.exists():
        print(f"[payments] Square file not found: {path}")
        return []
    out: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            status = (row.get("Status") or "").strip().lower()
            amount_paid = _money(row.get("Amount Paid"))
            # Booked = invoice was paid (status Paid OR a non-zero amount paid).
            if status != "paid" and amount_paid <= 0:
                continue
            requested = _money(row.get("Requested Amount"))
            out.append(
                {
                    "source": "square",
                    "name": (row.get("Customer Name") or "").strip(),
                    "email": (row.get("Customer Email") or "").strip().lower(),
                    "phone": normalize_phone(row.get("Customer Phone") or ""),
                    "note": "",
                    "amount": amount_paid or requested,
                    "date": _iso_date(row.get("Last Payment Date") or row.get("Invoice Date")),
                    "title": (row.get("Invoice Title") or "").strip(),
                    "raw_id": (row.get("Invoice ID") or row.get("Invoice Token") or "").strip(),
                }
            )
    return out


def parse_cashapp(path: Path = CASHAPP_CSV) -> list[dict]:
    """Incoming Cash App peer payments -> normalized payment records."""
    if not path.exists():
        print(f"[payments] Cash App file not found: {path}")
        return []
    out: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            ttype = (row.get("Transaction Type") or "").strip()
            status = (row.get("Status") or "").strip().upper()
            amount = _money(row.get("Amount"))
            if ttype not in CASHAPP_INCOMING_TYPES:
                continue
            if status != "COMPLETE" or amount <= 0:
                continue
            name = (row.get("Name of sender/receiver") or "").strip()
            note = (row.get("Notes") or "").strip()
            if not name and not note:
                continue
            out.append(
                {
                    "source": "cashapp",
                    "name": name,
                    "email": "",
                    "phone": "",
                    "note": note,
                    "amount": amount,
                    "date": _iso_date(row.get("Date")),
                    "title": note,
                    "raw_id": (row.get("Transaction ID") or "").strip(),
                }
            )
    return out


def ingest(square: Path = SQUARE_CSV, cashapp: Path = CASHAPP_CSV,
           out: Path = PAYMENTS_CSV) -> list[dict]:
    records = parse_square(square) + parse_cashapp(cashapp)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PAYMENT_FIELDS)
        w.writeheader()
        for r in records:
            w.writerow({k: r.get(k, "") for k in PAYMENT_FIELDS})

    sq = sum(1 for r in records if r["source"] == "square")
    ca = len(records) - sq
    with_phone = sum(1 for r in records if r["phone"])
    with_email = sum(1 for r in records if r["email"])
    total = sum(r["amount"] for r in records)
    print(
        f"[payments] {len(records)} payments -> {out}\n"
        f"  square={sq}  cashapp={ca}  with_phone={with_phone}  with_email={with_email}\n"
        f"  total collected=${total:,.2f}"
    )
    return records


if __name__ == "__main__":
    ingest()
