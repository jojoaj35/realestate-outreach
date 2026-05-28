#!/usr/bin/env python3
"""Real Estate Photography Outreach — single entry point.

Stages:
    scrape    realtor.com search URL -> listings.json
    score     listings.json -> scored_listings.json (photo quality)
    enqueue   scored_listings.json -> queue store (Sheets or local CSV)
    pipeline  scrape + score + enqueue in one shot
    outreach  send cold iMessages to queued agents (DRY RUN unless --send)
    replies   poll Messages for replies, classify, enforce DNC
    status    print a queue summary

Examples:
    python run.py pipeline "https://www.realtor.com/realestateandhomes-search/San-Antonio_TX" --pages 2
    python run.py outreach --max 5          # dry run
    python run.py outreach --max 5 --send   # actually send
    python run.py replies
    python run.py status
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def cmd_scrape(args) -> None:
    import scrape_realtor
    from dataclasses import asdict

    listings = scrape_realtor.scrape(
        args.url, pages=args.pages, enrich=not args.no_enrich, headless=args.headless
    )
    Path(args.out).write_text(json.dumps([asdict(l) for l in listings], indent=2))
    print(f"Saved {len(listings)} listings -> {args.out}")


def cmd_score(args) -> None:
    import score_listings
    score_listings.score_file(Path(args.listings), Path(args.out))


def cmd_enqueue(args) -> None:
    from store import get_store
    scored = json.loads(Path(args.scored).read_text())
    store = get_store()
    added, updated = store.upsert_listings(scored)
    print(f"Enqueued: {added} new, {updated} updated ({len(scored)} scored input).")


def cmd_pipeline(args) -> None:
    import scrape_realtor
    import score_listings
    from dataclasses import asdict
    from store import get_store

    print("== scrape ==")
    listings = scrape_realtor.scrape(
        args.url, pages=args.pages, enrich=not args.no_enrich, headless=args.headless
    )
    raw = [asdict(l) for l in listings]
    Path(args.out).write_text(json.dumps(raw, indent=2))
    print(f"  {len(raw)} listings\n== score ==")

    scored = [score_listings.score_listing_record(l) for l in raw]
    scored.sort(key=lambda r: r.get("score", 1.0))
    Path(args.scored).write_text(json.dumps(scored, indent=2))

    print("== enqueue ==")
    added, updated = get_store().upsert_listings(scored)
    print(f"  {added} new, {updated} updated. Review the queue, then: python run.py outreach")


def cmd_outreach(args) -> None:
    import outreach
    outreach.run(max_sends=args.max, dry_run=not args.send, force_hours=args.force)


def cmd_replies(args) -> None:
    import reply_poller
    reply_poller.poll(hours=args.hours)


def cmd_status(args) -> None:
    from store import get_store
    rows = get_store().all()
    by_status = Counter(r.get("status", "?") for r in rows)
    print(f"Queue: {len(rows)} listings")
    for status, n in sorted(by_status.items()):
        print(f"  {status:10s} {n}")
    queued = [r for r in rows if r.get("status") == "queued"]
    if queued:
        print("\nNext up (lowest score first):")
        for r in sorted(queued, key=lambda r: float(r.get("score") or 1.0))[:10]:
            print(f"  {r.get('score','?'):>5}  {r.get('address','?')[:40]:40s}  "
                  f"{r.get('agent_phone','(no phone)')}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="scrape realtor.com -> listings.json")
    s.add_argument("url")
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--no-enrich", action="store_true")
    s.add_argument("--headless", action="store_true")
    s.add_argument("--out", default="listings.json")
    s.set_defaults(func=cmd_scrape)

    s = sub.add_parser("score", help="score listings.json -> scored_listings.json")
    s.add_argument("listings", nargs="?", default="listings.json")
    s.add_argument("--out", default="scored_listings.json")
    s.set_defaults(func=cmd_score)

    s = sub.add_parser("enqueue", help="load scored_listings.json into the queue store")
    s.add_argument("scored", nargs="?", default="scored_listings.json")
    s.set_defaults(func=cmd_enqueue)

    s = sub.add_parser("pipeline", help="scrape + score + enqueue")
    s.add_argument("url")
    s.add_argument("--pages", type=int, default=1)
    s.add_argument("--no-enrich", action="store_true")
    s.add_argument("--headless", action="store_true")
    s.add_argument("--out", default="listings.json")
    s.add_argument("--scored", default="scored_listings.json")
    s.set_defaults(func=cmd_pipeline)

    s = sub.add_parser("outreach", help="send cold iMessages to queued agents")
    s.add_argument("--max", type=int, default=None)
    s.add_argument("--send", action="store_true", help="actually send (default dry run)")
    s.add_argument("--force", action="store_true", help="ignore business-hours window")
    s.set_defaults(func=cmd_outreach)

    s = sub.add_parser("replies", help="poll Messages for replies + classify")
    s.add_argument("--hours", type=int, default=None)
    s.set_defaults(func=cmd_replies)

    s = sub.add_parser("status", help="print queue summary")
    s.set_defaults(func=cmd_status)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
