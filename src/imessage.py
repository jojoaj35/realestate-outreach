"""Send iMessages from this Mac via AppleScript (osascript), with delivery checks.

Why this module exists in its current shape:
  - A large share of agents are NOT on iMessage (Android / landline / VOIP).
    Force-binding every send to the iMessage service made those land as red
    "Not Delivered" errors, which both failed to reach the agent and trained
    Apple's spam throttle against the account.
  - ``send_smart()`` fixes that: it sends as iMessage, then *reads chat.db back*
    to confirm delivery (see ``delivery.py``). Numbers that don't deliver are
    reported as failures so the runner can mark them undeliverable and skip
    them instead of silently failing.

This is iMessage-only (no SMS/iPhone fallback). This module only sends +
verifies one message; higher-level guardrails (DNC, daily cap, pacing,
business hours) live in the outreach runner.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import delivery
from config import settings
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
    """Best-effort: True unless the local DB shows this handle as SMS-only.

    Returns True for unknown numbers (optimistic) — the real proof now comes
    from the post-send delivery check in ``send_smart``. Returns False only if
    the local DB shows the handle exclusively on the SMS service.
    """
    p = normalize_phone(phone)
    if not p or not CHAT_DB.exists():
        return True
    try:
        con = sqlite3.connect(f"file:{CHAT_DB}?mode=ro&immutable=1", uri=True)
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


def _applescript_send(phone: str, message: str) -> tuple[bool, str]:
    """Send one message bound to the iMessage service."""
    if shutil.which("osascript") is None:
        return False, "osascript not found (are you on macOS?)"
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
        set targetService to 1st account whose service type = iMessage
        set targetBuddy to participant "{phone}" of targetService
        send "{safe}" to targetBuddy
    end tell
    '''
    ok, out = _osascript(script)
    return (True, "queued") if ok else (False, out or "applescript error")


def _try_imessage(phone: str, message: str, check_seconds: int) -> tuple[str, str]:
    """Send via iMessage and confirm via chat.db. Returns (state, detail).

    state is one of: "delivered", "failed", "pending", "script_error".
    """
    watermark = delivery.max_rowid()
    ok, info = _applescript_send(phone, message)
    if not ok:
        return "script_error", info

    # Give Apple a moment, then read the delivery state back.
    deadline = time.monotonic() + max(0, check_seconds)
    state = delivery.latest_outbound(phone, min_rowid=watermark)
    while time.monotonic() < deadline and state.pending:
        time.sleep(2)
        state = delivery.latest_outbound(phone, min_rowid=watermark)

    if not state.readable:
        # Can't read chat.db (no Full Disk Access). Trust the AppleScript send
        # but flag it so the caller knows delivery is unverified.
        return "pending", "sent (delivery unverified — grant Full Disk Access)"
    if state.failed:
        return "failed", f"not delivered (error {state.error})"
    if state.ok:
        return "delivered", "delivered"
    return "pending", "sent (no delivery confirmation yet)"


def send_smart(phone: str, message: str, dry_run: bool = True,
               check_seconds: int | None = None) -> tuple[bool, str, str]:
    """Send one iMessage and verify it actually delivered.

    Returns ``(ok, channel, detail)`` where channel is "imessage", "dry-run",
    or "none" (didn't deliver). iMessage-only: numbers that aren't reachable on
    iMessage come back as failures so the runner can skip them.
    """
    if check_seconds is None:
        check_seconds = settings.delivery_check_seconds

    p = normalize_phone(phone)
    if not p:
        return False, "none", "invalid phone"

    if dry_run:
        preview = message.replace("\n", " ")
        return True, "dry-run", f"[DRY RUN] would send (iMessage) to {p}: {preview[:80]}..."

    state, detail = _try_imessage(p, message, check_seconds)
    if state in ("delivered", "pending"):
        # "pending" means sent without a failure (or chat.db unreadable). For
        # iMessage we accept pending — failures show up as a red error, not a
        # missing receipt — so we don't double-send.
        return True, "imessage", detail

    return False, "none", f"iMessage {detail}"


def send(phone: str, message: str, dry_run: bool = True) -> tuple[bool, str]:
    """Backward-compatible wrapper. Returns ``(ok, info)``.

    Prefer ``send_smart`` for new code (it also reports the channel used).
    """
    ok, channel, detail = send_smart(phone, message, dry_run=dry_run)
    if dry_run:
        return ok, detail
    return ok, (f"sent via {channel}" if ok else detail)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Send a test iMessage.")
    ap.add_argument("phone")
    ap.add_argument("message")
    ap.add_argument("--send", action="store_true", help="actually send (default dry run)")
    args = ap.parse_args()
    ok, channel, info = send_smart(args.phone, args.message, dry_run=not args.send)
    print(("OK  " if ok else "ERR ") + f"[{channel}] " + info)
