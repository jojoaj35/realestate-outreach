#!/usr/bin/env python3
"""Real Estate Photography Outreach — single entry point.

Stages:
    scrape    realtor.com search URL -> listings.json
    score     listings.json -> scored_listings.json (photo quality)
    enqueue   scored_listings.json -> queue store (Sheets or local CSV)
    pipeline  scrape + score + enqueue in one shot
    outreach  send cold iMessages to queued agents (DRY RUN unless --send)
    dnc       add a phone number to the do-not-contact list (honor opt-outs)
    status    print a queue summary
    ig-discover  find realtor Instagram profiles for a city
    ig-status    print Instagram queue summary
    ig-dm        send Instagram DMs (assisted by default; DRY RUN unless --send)
    ig-dnc       add an Instagram handle to the do-not-contact list

Examples:
    python run.py scan --city Austin --state TX --count 40 --keep 15           # Zillow via Scrapling
    python run.py scan --url "https://www.zillow.com/homes/Austin,-TX_rb/"      # scan a Zillow link
    python run.py scrape --source zillow --city Austin --state TX --max 25     # Zillow via Scrapling
    python run.py scrape --source hasdata --city Austin --state TX --max 40    # live Zillow via HasData
    python run.py outreach --max 5          # dry run
    python run.py outreach --max 5 --send   # actually send
    python run.py dnc +12105550123 "asked to stop"
    python run.py status
    python run.py ig-discover --city "San Antonio" --max 30
    python run.py ig-dm --max 5 --send
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


def _scrape(args):
    """Run the chosen scraper, return a list of listing dicts."""
    from dataclasses import asdict
    if getattr(args, "source", "realtor") == "zillow":
        import scrape_zillow
        listings = scrape_zillow.scrape(
            city=args.city,
            state=getattr(args, "state", "TX"),
            max_results=args.max,
            enrich=not args.no_enrich,
        )
    elif getattr(args, "source", "realtor") == "hasdata":
        import scrape_hasdata
        listings = scrape_hasdata.scrape(
            city=args.city,
            state=getattr(args, "state", "TX"),
            max_results=args.max,
            enrich=not args.no_enrich,
            only_city=not args.all_cities,
        )
    else:
        import scrape_realtor
        if not args.url:
            raise SystemExit("realtor source needs a search URL (positional arg).")
        listings = scrape_realtor.scrape(
            args.url, pages=args.pages, enrich=not args.no_enrich, headless=args.headless
        )
    return [asdict(l) for l in listings]


def cmd_scrape(args) -> None:
    raw = _scrape(args)
    Path(args.out).write_text(json.dumps(raw, indent=2))
    with_phone = sum(1 for r in raw if r.get("agent_phone"))
    print(f"Saved {len(raw)} listings -> {args.out} ({with_phone} with agent phone)")


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
    import score_listings
    from store import get_store

    print("== scrape ==")
    raw = _scrape(args)
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


def cmd_dnc(args) -> None:
    from store import get_store
    store = get_store()
    store.add_dnc(args.phone, reason=args.reason)
    print(f"Added to do-not-contact: {args.phone}"
          + (f" ({args.reason})" if args.reason else ""))


def cmd_scan(args) -> None:
    import scan
    scan.scan(city=args.city, count=args.count, keep=args.keep,
              max_pro_score=args.max_pro_score, state=args.state,
              url=(args.url or None))
    print("\nReview: python run.py status   |   export: python run.py export --top "
          f"{args.keep} --out targets.xls")


def cmd_export(args) -> None:
    import export_xls
    rows = export_xls.select_rows(args.top)
    path = export_xls.export(rows, Path(args.out))
    print(f"Wrote {len(rows)} agents -> {path}")


def cmd_web(args) -> None:
    sys.path.insert(0, str(ROOT / "src" / "web"))
    import app as web_app
    web_app.main(host=args.host, port=args.port)


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


def cmd_ig_discover(args) -> None:
    import ig_discover
    ig_discover.discover(
        city=args.city,
        max_results=args.max,
        enrich_from_queue=args.enrich_from_queue,
        headless=args.headless,
        require_city=not args.no_require_city,
    )


def cmd_ig_status(args) -> None:
    from ig_store import get_ig_store
    rows = get_ig_store().all()
    by_status = Counter(r.get("status", "?") for r in rows)
    print(f"Instagram queue: {len(rows)} profiles")
    for status, n in sorted(by_status.items()):
        print(f"  {status:10s} {n}")
    queued = [r for r in rows if r.get("status") == "queued"]
    if queued:
        print("\nNext up:")
        for r in queued[:10]:
            print(f"  @{r.get('ig_handle','?'):20s}  {r.get('display_name','')[:30]}")


def cmd_ig_dm(args) -> None:
    import ig_dm
    ig_dm.run(
        max_sends=args.max,
        dry_run=not args.send,
        force_hours=args.force,
        automated=True if args.automated else None,
    )


def cmd_ig_dnc(args) -> None:
    from ig_store import get_ig_store
    store = get_ig_store()
    store.add_dnc(args.handle, reason=args.reason)
    print(f"Added to IG do-not-contact: @{args.handle.lstrip('@')}"
          + (f" ({args.reason})" if args.reason else ""))


def cmd_booked_ingest(args) -> None:
    from booked import dm_ingest, imsg_ingest, payments_ingest
    payments_ingest.ingest()
    imsg_ingest.ingest()
    dm_ingest.ingest()


def cmd_booked_match(args) -> None:
    from booked import identity_match
    identity_match.match()


def cmd_booked_label(args) -> None:
    from booked import label_booked
    label_booked.label()


def cmd_booked_features(args) -> None:
    from booked import features
    features.build()


def cmd_booked_train(args) -> None:
    from booked import train_booked
    train_booked.train()


def cmd_booked_enrich(args) -> None:
    from booked import ig_enrich
    ig_enrich.enrich(max_n=args.max, headless=not args.show)


def cmd_booked_score_ig(args) -> None:
    from booked import score
    n = score.score_ig_queue()
    if n:
        print(f"\nScored {n} Instagram profiles. The DM queue and dashboard now "
              f"prioritize highest booking-propensity first.")


def cmd_booked_pipeline(args) -> None:
    """Full chain: ingest -> match -> label -> features -> train."""
    from booked import (dm_ingest, features, identity_match, imsg_ingest,
                        label_booked, payments_ingest, train_booked)
    print("== ingest ==")
    payments_ingest.ingest()
    imsg_ingest.ingest()
    dm_ingest.ingest()
    print("\n== match ==")
    identity_match.match()
    print("\n== label ==")
    label_booked.label()
    print("\n== features ==")
    features.build()
    print("\n== train ==")
    train_booked.train()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape", help="scrape listings -> listings.json")
    s.add_argument("url", nargs="?", default=None, help="realtor.com search URL (realtor source)")
    s.add_argument("--source", choices=["realtor", "zillow", "hasdata"], default="zillow")
    s.add_argument("--city", default="Austin", help="city filter (zillow / hasdata)")
    s.add_argument("--state", default="TX", help="state (zillow / hasdata source)")
    s.add_argument("--max", type=int, default=80, help="max listings (zillow / hasdata)")
    s.add_argument("--all-cities", action="store_true", help="don't restrict to --city (hasdata)")
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
    s.add_argument("url", nargs="?", default=None, help="realtor.com search URL (realtor source)")
    s.add_argument("--source", choices=["realtor", "zillow", "hasdata"], default="zillow")
    s.add_argument("--city", default="Austin", help="city filter (zillow / hasdata)")
    s.add_argument("--state", default="TX", help="state (zillow / hasdata source)")
    s.add_argument("--max", type=int, default=80, help="max listings (zillow / hasdata)")
    s.add_argument("--all-cities", action="store_true", help="don't restrict to --city (hasdata)")
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

    s = sub.add_parser("dnc", help="add a phone number to the do-not-contact list")
    s.add_argument("phone")
    s.add_argument("reason", nargs="?", default="manual opt-out")
    s.set_defaults(func=cmd_dnc)

    s = sub.add_parser("scan", help="find Zillow listings with NON-professional photos")
    s.add_argument("--city", default="Austin")
    s.add_argument("--state", default="TX")
    s.add_argument("--url", default="", help="scan a Zillow search/listing link directly")
    s.add_argument("--count", type=int, default=200, help="how many listings to scan")
    s.add_argument("--keep", type=int, default=15, help="how many worst to enrich + queue")
    s.add_argument("--max-pro-score", type=float, default=None,
                   help="only keep listings with hero CLIP <= this (e.g. 0.5)")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("export", help="export agents to an .xls spreadsheet")
    s.add_argument("--top", type=int, default=None, help="only the N weakest-photo listings")
    s.add_argument("--out", default="agents.xls")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("web", help="launch the local web dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5000)
    s.set_defaults(func=cmd_web)

    s = sub.add_parser("status", help="print queue summary")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("ig-discover", help="find realtor Instagram profiles for a city")
    s.add_argument("--city", default="San Antonio")
    s.add_argument("--max", type=int, default=50)
    s.add_argument("--enrich-from-queue", action="store_true",
                   help="also search IG for agents in the listing queue")
    s.add_argument("--no-require-city", action="store_true",
                   help="accept profiles without city in bio")
    s.add_argument("--headless", action="store_true")
    s.set_defaults(func=cmd_ig_discover)

    s = sub.add_parser("ig-status", help="print Instagram queue summary")
    s.set_defaults(func=cmd_ig_status)

    s = sub.add_parser("ig-dm", help="send Instagram DMs to queued profiles")
    s.add_argument("--max", type=int, default=None)
    s.add_argument("--send", action="store_true", help="actually send (default: dry run)")
    s.add_argument("--force", action="store_true", help="ignore business-hours window")
    s.add_argument("--automated", action="store_true",
                   help="Playwright auto-send (overrides IG_SEND_MODE=assisted)")
    s.set_defaults(func=cmd_ig_dm)

    s = sub.add_parser("ig-dnc", help="add an Instagram handle to do-not-contact")
    s.add_argument("handle", help="@handle or handle")
    s.add_argument("reason", nargs="?", default="manual opt-out")
    s.set_defaults(func=cmd_ig_dnc)

    # ── Booked-agent propensity model ──────────────────────────────────────
    s = sub.add_parser("booked-pipeline",
                       help="full booked-agent chain: ingest+match+label+features+train")
    s.set_defaults(func=cmd_booked_pipeline)

    s = sub.add_parser("booked-ingest",
                       help="parse payments + iMessage + Instagram DMs")
    s.set_defaults(func=cmd_booked_ingest)

    s = sub.add_parser("booked-match", help="resolve payments<->iMessage<->Instagram identities")
    s.set_defaults(func=cmd_booked_match)

    s = sub.add_parser("booked-label", help="assign booked labels + write booked_contacts.csv")
    s.set_defaults(func=cmd_booked_label)

    s = sub.add_parser("booked-features", help="build the per-contact feature table")
    s.set_defaults(func=cmd_booked_features)

    s = sub.add_parser("booked-train", help="train the booking-propensity model + report")
    s.set_defaults(func=cmd_booked_train)

    s = sub.add_parser("booked-enrich",
                       help="scrape Instagram profile stats for engaged contacts")
    s.add_argument("--max", type=int, default=None, help="cap number of handles to fetch")
    s.add_argument("--show", action="store_true", help="run with a visible browser window")
    s.set_defaults(func=cmd_booked_enrich)

    s = sub.add_parser("booked-score-ig",
                       help="score the Instagram outreach queue with the booking model")
    s.set_defaults(func=cmd_booked_score_ig)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
