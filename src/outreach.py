"""Outreach runner: read queued listings, send cold iMessages with guardrails.

Guardrails (all enforced here, in one place):
  - Do-not-contact list checked before every send.
  - Daily cap per Apple ID (counts today's sends already in the store).
  - Business-hours window (skipped in dry-run).
  - iMessage-only: numbers that look SMS-only are skipped, never green-bubbled.
  - Random pacing between real sends.
  - Opt-out language lives in every template.

Safe by default: runs as a DRY RUN unless you pass ``--send``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import imessage
import templates
from config import LOGS_DIR, settings
from store import get_store

LOG_PATH = LOGS_DIR / "outreach.log"


def _log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def _sent_today(store) -> int:
    today = dt.date.today().isoformat()
    return sum(
        1 for r in store.all()
        if r.get("status") == "sent" and str(r.get("sent_at", "")).startswith(today)
    )


def within_business_hours(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now()
    return settings.send_hour_start <= now.hour < settings.send_hour_end


def run(max_sends: int | None = None, dry_run: bool = True, force_hours: bool = False) -> dict:
    store = get_store()
    stats = {"attempted": 0, "sent": 0, "skipped": 0, "dnc": 0}

    if not dry_run and not force_hours and not within_business_hours():
        _log(f"Outside business hours ({settings.send_hour_start}:00-"
             f"{settings.send_hour_end}:00). Skipping. Use --force to override.")
        return stats

    sent_today = _sent_today(store)
    remaining = max(0, settings.daily_send_cap - sent_today)
    if remaining == 0:
        _log(f"Daily cap reached ({settings.daily_send_cap}). Nothing to send.")
        return stats
    budget = min(remaining, max_sends) if max_sends else remaining

    queued = store.get_by_status("queued")
    _log(f"{'DRY RUN — ' if dry_run else ''}queued={len(queued)} "
         f"sent_today={sent_today} budget={budget}")

    for row in queued:
        if stats["sent"] >= budget:
            _log(f"Hit budget of {budget} sends for this run.")
            break

        lid = row.get("listing_id")
        phone = row.get("agent_phone", "")
        stats["attempted"] += 1

        if not phone:
            store.update(lid, status="skipped", reply_sentiment="no phone")
            stats["skipped"] += 1
            continue
        if store.is_dnc(phone):
            store.update(lid, status="dnc")
            stats["dnc"] += 1
            _log(f"DNC skip {phone}")
            continue
        if not imessage.available(phone):
            store.update(lid, status="skipped", reply_sentiment="not iMessage")
            stats["skipped"] += 1
            _log(f"Not iMessage, skip {phone}")
            continue

        message = templates.render(row)
        ok, info = imessage.send(phone, message, dry_run=dry_run)
        if not ok:
            store.update(lid, status="skipped", reply_sentiment=f"send failed: {info[:40]}")
            stats["skipped"] += 1
            _log(f"Send failed {phone}: {info}")
            continue

        if dry_run:
            _log(info)
            stats["sent"] += 1  # counted so budget preview is realistic
            continue

        store.update(lid, status="sent", sent_at=dt.datetime.now().isoformat(timespec="seconds"),
                     message_sent=message)
        stats["sent"] += 1
        _log(f"SENT -> {phone} ({row.get('address', '')})")

        if stats["sent"] < budget:
            gap = random.uniform(settings.send_min_gap_seconds, settings.send_max_gap_seconds)
            _log(f"pacing {gap:.0f}s ...")
            time.sleep(gap)

    _log(f"Done. {stats}")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Send cold iMessages to queued agents.")
    ap.add_argument("--max", type=int, default=None, help="max sends this run (capped by daily cap)")
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--force", action="store_true", help="ignore business-hours window")
    args = ap.parse_args()
    run(max_sends=args.max, dry_run=not args.send, force_hours=args.force)


if __name__ == "__main__":
    main()
