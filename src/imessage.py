"""Send iMessages from this Mac via AppleScript (osascript).

Design choices that keep this safe:
  - We bind the send to the **iMessage** service account explicitly, so an
    undeliverable number errors out instead of silently going green-bubble SMS
    (which would cost money and look unprofessional).
  - ``available()`` is a best-effort pre-check against the local Messages DB;
    a brand-new number can't be verified until first contact, so the real
    guard is the bound-service send above.

This module only sends. Guardrails (DNC, daily cap, pacing, hours) live in the
outreach runner so they're enforced in one place.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from pathlib import Path

from contacts import normalize_phone

CHAT_DB = Path.home() / "Library" / "Messages" / "chat.db"


def _osascript(script: str, timeout: int = 30) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)
    out = (proc.stdout or proc.stderr).strip()
    return proc.returncode == 0, out


def available(phone: str) -> bool:
    """Best-effort: True if we've seen this handle on the iMessage service.

    Returns True for unknown numbers (optimistic) — the bound-service send
    is what actually prevents SMS fallback. Returns False only if the local
    DB shows the handle as SMS-only.
    """
    p = normalize_phone(phone)
    if not p or not CHAT_DB.exists():
        return True
    try:
        con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT service FROM handle WHERE id = ?", (p,)
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return True
    if not rows:
        return True
    services = {r[0] for r in rows}
    return "iMessage" in services


def send(phone: str, message: str, dry_run: bool = True) -> tuple[bool, str]:
    """Send one iMessage. In dry_run mode, just report what would be sent."""
    p = normalize_phone(phone)
    if not p:
        return False, "invalid phone"
    if dry_run:
        preview = message.replace("\n", " ")
        return True, f"[DRY RUN] would send to {p}: {preview[:90]}..."

    if shutil.which("osascript") is None:
        return False, "osascript not found (are you on macOS?)"

    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{p}" of targetService
        send "{safe}" to targetBuddy
    end tell
    '''
    ok, out = _osascript(script)
    return (True, "sent") if ok else (False, out or "applescript error")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Send a test iMessage.")
    ap.add_argument("phone")
    ap.add_argument("message")
    ap.add_argument("--send", action="store_true", help="actually send (default dry run)")
    args = ap.parse_args()
    ok, info = send(args.phone, args.message, dry_run=not args.send)
    print(("OK  " if ok else "ERR ") + info)
