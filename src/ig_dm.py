"""Instagram DM outreach with the same guardrails as iMessage outreach.

Modes (``IG_SEND_MODE`` in .env):
  - assisted (default): opens each profile, copies message to clipboard — you paste & send
  - automated: Playwright clicks Message and sends (higher ban risk)

Safe by default: DRY RUN unless ``--send``.
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import templates
from config import LOGS_DIR, settings
from ig_browser import (
    copy_to_clipboard,
    ensure_logged_in,
    instagram_context,
    polite_sleep,
    send_dm_automated,
)
from ig_store import get_ig_store, normalize_handle
from outreach import within_business_hours

LOG_PATH = LOGS_DIR / "ig_outreach.log"


def _log(msg: str) -> None:
    line = f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def _book_score(row: dict) -> float:
    """Booking-propensity score for queue ordering (0 if unscored)."""
    try:
        return float(row.get("book_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sent_today(store) -> int:
    today = dt.date.today().isoformat()
    return sum(
        1 for r in store.all()
        if r.get("status") == "sent" and str(r.get("sent_at", "")).startswith(today)
    )


def _contacted_set(store) -> set[str]:
    return {normalize_handle(r.get("ig_handle", ""))
            for r in store.all() if r.get("status") == "sent"}


def _record(results: list | None, handle: str, name: str, outcome: str,
            detail: str, message: str = "") -> None:
    if results is not None:
        results.append({
            "ig_handle": handle,
            "display_name": name,
            "outcome": outcome,
            "detail": detail,
            "message": message,
        })


def _send_assisted(row: dict, message: str, dry_run: bool) -> tuple[bool, str]:
    handle = normalize_handle(row.get("ig_handle", ""))
    url = row.get("profile_url") or f"https://www.instagram.com/{handle}/"
    if dry_run:
        return True, f"[dry run] would open {url} and copy message to clipboard"
    if not copy_to_clipboard(message):
        return False, "clipboard copy failed (pbcopy)"
    webbrowser.open(url)
    return True, f"opened {url} — message copied to clipboard; paste in Instagram DM and send"


def _send_rows(store, rows, dry_run, budget, stats, contacted, automated: bool,
               pace=True, results: list | None = None, page=None) -> dict:
    for row in rows:
        if stats["sent"] >= budget:
            _log(f"Hit budget of {budget} DMs for this run.")
            break

        handle = normalize_handle(row.get("ig_handle", ""))
        name = row.get("display_name", "")
        stats["attempted"] += 1

        if not handle:
            stats["skipped"] += 1
            _record(results, "", name, "skipped", "no handle")
            continue
        if handle in contacted:
            if not dry_run:
                store.update(handle, status="skipped")
            stats["skipped"] += 1
            _log(f"Duplicate handle, skip @{handle}")
            _record(results, handle, name, "skipped", "duplicate handle")
            continue
        if store.is_dnc(handle):
            if not dry_run:
                store.update(handle, status="dnc")
            stats["dnc"] += 1
            _log(f"DNC skip @{handle}")
            _record(results, handle, name, "dnc", "on do-not-contact list")
            continue

        seed = hash(handle) & 0xFFFF
        message = templates.render_instagram(row, seed=seed)

        if automated and not dry_run:
            if page is None:
                _record(results, handle, name, "skipped", "no browser page")
                stats["skipped"] += 1
                continue
            ok, info = send_dm_automated(page, handle, message)
        else:
            ok, info = _send_assisted(row, message, dry_run=dry_run)

        if not ok:
            if not dry_run:
                store.update(handle, status="skipped")
            stats["skipped"] += 1
            _log(f"Send failed @{handle}: {info}")
            _record(results, handle, name, "skipped", info[:120], message)
            continue

        contacted.add(handle)
        if dry_run:
            _log(info)
            stats["sent"] += 1
            _record(results, handle, name, "preview", info, message)
            if automated:
                continue
            if pace and stats["sent"] < budget:
                _log("Assisted dry run — no browser opened.")
            continue

        store.update(
            handle,
            status="sent",
            sent_at=dt.datetime.now().isoformat(timespec="seconds"),
            message_sent=message,
        )
        stats["sent"] += 1
        _log(f"SENT -> @{handle} ({name})")
        _record(results, handle, name, "sent", info, message)

        if pace and stats["sent"] < budget:
            gap = random.uniform(settings.ig_min_gap_seconds, settings.ig_max_gap_seconds)
            _log(f"pacing {gap:.0f}s …")
            time.sleep(gap)

    _log(f"Done. {stats}")
    return stats


def run(max_sends: int | None = None, dry_run: bool = True, force_hours: bool = False,
        automated: bool | None = None) -> dict:
    store = get_ig_store()
    stats = {"attempted": 0, "sent": 0, "skipped": 0, "dnc": 0}
    automated = (settings.ig_send_mode == "automated") if automated is None else automated

    if not dry_run and not force_hours and not within_business_hours():
        _log(f"Outside business hours ({settings.send_hour_start}:00-"
             f"{settings.send_hour_end}:00). Skipping. Use --force to override.")
        return stats

    sent_today = _sent_today(store)
    remaining = max(0, settings.ig_daily_dm_cap - sent_today)
    if remaining == 0 and not dry_run:
        _log(f"Daily IG cap reached ({settings.ig_daily_dm_cap}). Nothing to send.")
        return stats
    budget = min(remaining, max_sends) if max_sends else (remaining if not dry_run else 9999)

    queued = store.get_by_status("queued")
    queued.sort(key=_book_score, reverse=True)  # highest booking propensity first
    mode = "automated" if automated else "assisted"
    _log(f"{'DRY RUN — ' if dry_run else ''}mode={mode} queued={len(queued)} "
         f"sent_today={sent_today} budget={budget}")

    contacted = _contacted_set(store)

    if automated and not dry_run:
        with instagram_context(headless=False) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not ensure_logged_in(page):
                _log("Instagram login required.")
                return stats
            return _send_rows(store, queued, dry_run, budget, stats, contacted,
                              automated=True, page=page)
    return _send_rows(store, queued, dry_run, budget, stats, contacted, automated=False)


def send_selected(handles: list[str], dry_run: bool = True, force_hours: bool = False,
                  automated: bool | None = None) -> dict:
    return send_selected_detailed(handles, dry_run=dry_run, force_hours=force_hours,
                                  automated=automated)["stats"]


def send_selected_detailed(handles: list[str], dry_run: bool = True, force_hours: bool = False,
                           automated: bool | None = None) -> dict:
    store = get_ig_store()
    stats = {"attempted": 0, "sent": 0, "skipped": 0, "dnc": 0}
    results: list[dict] = []
    automated = (settings.ig_send_mode == "automated") if automated is None else automated

    if not dry_run and not force_hours and not within_business_hours():
        stats["error"] = (f"Outside business hours "
                          f"({settings.send_hour_start}:00-{settings.send_hour_end}:00). "
                          f"Check Force to override.")
        _log(stats["error"])
        return {"stats": stats, "results": results}

    sent_today = _sent_today(store)
    remaining = max(0, settings.ig_daily_dm_cap - sent_today)
    if remaining == 0 and not dry_run:
        stats["error"] = f"Daily IG cap reached ({settings.ig_daily_dm_cap})."
        _log(stats["error"])
        return {"stats": stats, "results": results}
    budget = min(remaining if not dry_run else len(handles), len(handles))

    by_handle = {normalize_handle(r["ig_handle"]): r for r in store.all()}
    rows = [by_handle[normalize_handle(h)] for h in handles if normalize_handle(h) in by_handle]
    missing = [h for h in handles if normalize_handle(h) not in by_handle]
    for mh in missing:
        _record(results, mh, "", "skipped", "profile not found in ig queue")

    if not rows:
        stats["error"] = "No matching profiles found — refresh and try again."
        return {"stats": stats, "results": results}

    mode = "automated" if automated else "assisted"
    _log(f"{'DRY RUN — ' if dry_run else ''}mode={mode} selected={len(rows)} "
         f"sent_today={sent_today} budget={budget}")

    contacted = _contacted_set(store)

    if automated and not dry_run:
        with instagram_context(headless=False) as ctx:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            if not ensure_logged_in(page):
                stats["error"] = "Instagram login required."
                return {"stats": stats, "results": results}
            _send_rows(store, rows, dry_run, budget, stats, contacted,
                       automated=True, page=page, results=results)
    else:
        _send_rows(store, rows, dry_run, budget, stats, contacted,
                   automated=False, results=results)

    return {"stats": stats, "results": results, "dry_run": dry_run}


def main() -> None:
    ap = argparse.ArgumentParser(description="Send Instagram DMs to queued realtor profiles.")
    ap.add_argument("--max", type=int, default=None, help="max DMs this run")
    ap.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    ap.add_argument("--force", action="store_true", help="ignore business-hours window")
    ap.add_argument("--automated", action="store_true",
                    help="use Playwright to send (overrides IG_SEND_MODE=assisted)")
    args = ap.parse_args()
    run(max_sends=args.max, dry_run=not args.send, force_hours=args.force,
        automated=True if args.automated else None)


if __name__ == "__main__":
    main()
