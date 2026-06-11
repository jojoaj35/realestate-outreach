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


def _human_gap() -> float:
    """A randomized pause between sends: one text every 90s, ± 15s of jitter.

    Defaults to a uniform 75-105s gap (1m30s ± 15s, set via
    ``SEND_MIN_GAP_SECONDS`` / ``SEND_MAX_GAP_SECONDS``).
    """
    return random.uniform(settings.send_min_gap_seconds, settings.send_max_gap_seconds)


def _record(results: list | None, listing_id: str, address: str, phone: str,
            outcome: str, detail: str, message: str = "") -> None:
    if results is not None:
        results.append({
            "listing_id": listing_id,
            "address": address,
            "phone": phone,
            "outcome": outcome,
            "detail": detail,
            "message": message,
        })


def _send_rows(store, rows, dry_run, budget, stats, contacted, pace=True,
               results: list | None = None) -> dict:
    """Apply every guardrail and send to each eligible row, up to ``budget``."""
    from contacts import normalize_phone

    for row in rows:
        if stats["sent"] >= budget:
            _log(f"Hit budget of {budget} sends for this run.")
            break

        lid = row.get("listing_id")
        phone = row.get("agent_phone", "")
        addr = row.get("address", "")
        stats["attempted"] += 1

        agent_name = (row.get("agent_name") or "").strip()
        if not phone or not agent_name or agent_name.lower() == "redfin":
            if not dry_run:
                store.update(lid, status="skipped",
                             reply_sentiment="no listing agent contact")
            stats["skipped"] += 1
            _record(results, lid, addr, "", "skipped", "no listing agent contact")
            continue
        if normalize_phone(phone) in contacted:
            if not dry_run:
                store.update(lid, status="skipped", reply_sentiment="duplicate agent")
            stats["skipped"] += 1
            _log(f"Duplicate agent, skip {phone}")
            _record(results, lid, addr, phone, "skipped", "duplicate agent")
            continue
        if store.is_dnc(phone):
            if not dry_run:
                store.update(lid, status="dnc")
            stats["dnc"] += 1
            _log(f"DNC skip {phone}")
            _record(results, lid, addr, phone, "dnc", "on do-not-contact list")
            continue
        if not imessage.available(phone):
            if not dry_run:
                store.update(lid, status="skipped", reply_sentiment="not iMessage")
            stats["skipped"] += 1
            _log(f"Not iMessage, skip {phone}")
            _record(results, lid, addr, phone, "skipped", "not iMessage")
            continue

        message = templates.render(row)
        ok, channel, info = imessage.send_smart(phone, message, dry_run=dry_run)
        if not ok:
            # iMessage didn't deliver (recipient likely isn't on iMessage).
            # Don't keep hammering it — mark undeliverable and move on.
            if not dry_run:
                store.update(lid, status="skipped",
                             reply_sentiment=f"undeliverable: {info[:60]}")
            stats["skipped"] += 1
            _log(f"Undeliverable {phone}: {info}")
            _record(results, lid, addr, phone, "skipped", info[:160], message)
            continue

        contacted.add(normalize_phone(phone))  # block dupes within this run too
        if dry_run:
            _log(info)
            stats["sent"] += 1
            _record(results, lid, addr, phone, "preview", info, message)
            continue

        store.update(lid, status="sent",
                     sent_at=dt.datetime.now().isoformat(timespec="seconds"),
                     channel=channel, message_sent=message)
        stats["sent"] += 1
        stats[channel] = stats.get(channel, 0) + 1  # per-channel tally
        _log(f"SENT via {channel} -> {phone} ({addr}) [{info}]")
        _record(results, lid, addr, phone, "sent", f"{channel}: {info}", message)

        if pace and stats["sent"] < budget:
            gap = _human_gap()
            _log(f"pacing {gap:.0f}s ...")
            time.sleep(gap)

    _log(f"Done. {stats}")
    return stats


def _contacted_set(store):
    from contacts import normalize_phone
    contacted = {normalize_phone(r.get("agent_phone", ""))
                 for r in store.all() if r.get("status") == "sent"}
    contacted.discard("")
    return contacted


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

    contacted = _contacted_set(store)
    return _send_rows(store, queued, dry_run, budget, stats, contacted)


def send_selected(listing_ids: list[str], dry_run: bool = True,
                  force_hours: bool = False) -> dict:
    """Send to a specific set of listing IDs (used by the web UI)."""
    return send_selected_detailed(listing_ids, dry_run=dry_run, force_hours=force_hours)["stats"]


def send_selected_detailed(listing_ids: list[str], dry_run: bool = True,
                           force_hours: bool = False) -> dict:
    """Like send_selected but returns per-listing outcomes for the dashboard."""
    store = get_store()
    stats = {"attempted": 0, "sent": 0, "skipped": 0, "dnc": 0}
    results: list[dict] = []

    if not dry_run and not force_hours and not within_business_hours():
        stats["error"] = (f"Outside business hours "
                          f"({settings.send_hour_start}:00-{settings.send_hour_end}:00). "
                          f"Check Force to override.")
        _log(stats["error"])
        return {"stats": stats, "results": results}

    sent_today = _sent_today(store)
    remaining = max(0, settings.daily_send_cap - sent_today)
    if remaining == 0 and not dry_run:
        stats["error"] = f"Daily cap reached ({settings.daily_send_cap})."
        _log(stats["error"])
        return {"stats": stats, "results": results}
    budget = min(remaining if not dry_run else len(listing_ids), len(listing_ids))

    by_id = {r.get("listing_id"): r for r in store.all()}
    rows = [by_id[i] for i in listing_ids if i in by_id]
    missing = [i for i in listing_ids if i not in by_id]
    for mid in missing:
        _record(results, mid, "", "", "skipped", "listing not found in queue")

    if not rows:
        stats["error"] = "No matching listings found — refresh the page and try again."
        return {"stats": stats, "results": results}

    _log(f"{'DRY RUN — ' if dry_run else ''}selected={len(rows)} "
         f"sent_today={sent_today} budget={budget}")

    contacted = _contacted_set(store)
    _send_rows(store, rows, dry_run, budget, stats, contacted, results=results)
    return {"stats": stats, "results": results, "dry_run": dry_run}


def main() -> None:
    ap = argparse.ArgumentParser(description="Send cold iMessages to queued agents.")
    ap.add_argument("--max", type=int, default=None, help="max sends this run (capped by daily cap)")
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--force", action="store_true", help="ignore business-hours window")
    args = ap.parse_args()
    run(max_sends=args.max, dry_run=not args.send, force_hours=args.force)


if __name__ == "__main__":
    main()
