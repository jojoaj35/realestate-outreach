"""Read delivery state of outbound messages from the local Messages DB.

This is the core fix for the "Not Delivered" problem: after we send a message
we don't *assume* it worked, we read ``~/Library/Messages/chat.db`` back and
check whether Apple actually delivered it (or returned a red error). That lets
the sender fall back to SMS for numbers that aren't on iMessage, and skip the
ones that fail on every channel instead of silently failing.

Read-only: we open the DB in immutable mode and never write to it. Reading
``chat.db`` requires the running process (your terminal / the app) to have
**Full Disk Access** in System Settings > Privacy & Security.

chat.db columns we rely on (per outbound row in ``message``):
  - ``service``       "iMessage" or "SMS"
  - ``is_sent``       1 once handed to the transport
  - ``is_delivered``  1 when Apple confirms delivery (iMessage; SMS rarely sets it)
  - ``date_delivered``nanoseconds since 2001-01-01, 0 if not delivered
  - ``error``         0 = ok, non-zero = failed ("Not Delivered" / red)
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from contacts import normalize_phone

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"


@dataclass
class DeliveryState:
    """What we could learn about the most recent outbound message to a number."""

    found: bool = False          # did we locate an outbound row at all?
    service: str = ""            # "iMessage" / "SMS"
    is_sent: bool = False
    is_delivered: bool = False
    error: int = 0               # non-zero == Apple/carrier returned a failure
    readable: bool = True        # False if chat.db couldn't be opened/read

    @property
    def failed(self) -> bool:
        """True only when we have positive evidence the send failed."""
        return self.found and self.error != 0

    @property
    def ok(self) -> bool:
        """True when the message looks delivered (or sent, for SMS).

        iMessage gives a real delivery receipt; SMS usually doesn't, so for SMS
        we treat "sent with no error" as success.
        """
        if not self.found or self.error != 0:
            return False
        if self.service == "iMessage":
            return self.is_delivered
        return self.is_sent  # SMS / unknown service

    @property
    def pending(self) -> bool:
        """Sent, no error yet, but not confirmed delivered (still in flight)."""
        return self.found and self.error == 0 and not self.ok


def latest_outbound(phone: str, min_rowid: int = 0) -> DeliveryState:
    """Return the delivery state of the most recent message we sent to ``phone``.

    ``min_rowid`` lets callers ignore older messages: pass the ROWID captured
    *before* sending so we only inspect the row our send created.
    """
    p = normalize_phone(phone)
    if not p:
        return DeliveryState(readable=True, found=False)
    if not CHAT_DB.exists():
        return DeliveryState(readable=False, found=False)

    try:
        con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro&immutable=1", uri=True)
        try:
            row = con.execute(
                """
                SELECT m.ROWID, m.service, m.is_sent, m.is_delivered, m.error
                FROM message AS m
                JOIN handle AS h ON m.handle_id = h.ROWID
                WHERE h.id = ? AND m.is_from_me = 1 AND m.ROWID > ?
                ORDER BY m.ROWID DESC
                LIMIT 1
                """,
                (p, min_rowid),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return DeliveryState(readable=False, found=False)

    if not row:
        return DeliveryState(readable=True, found=False)

    _rowid, service, is_sent, is_delivered, error = row
    return DeliveryState(
        found=True,
        service=service or "",
        is_sent=bool(is_sent),
        is_delivered=bool(is_delivered),
        error=int(error or 0),
        readable=True,
    )


def max_rowid() -> int:
    """Current highest message ROWID, captured before a send as a watermark.

    Returns 0 if the DB can't be read (callers then just inspect the latest row).
    """
    if not CHAT_DB.exists():
        return 0
    try:
        con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro&immutable=1", uri=True)
        try:
            row = con.execute("SELECT MAX(ROWID) FROM message").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Inspect last outbound delivery state.")
    ap.add_argument("phone")
    args = ap.parse_args()
    st = latest_outbound(args.phone)
    print(st)
    print(f"ok={st.ok} failed={st.failed} pending={st.pending} readable={st.readable}")
