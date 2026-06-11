"""Score hand-picked example listings for photo-craft validation.

Usage:
    ./venv/bin/python scripts/score_examples.py URL [URL ...]
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photo_quality  # noqa: E402
import scrape_zillow  # noqa: E402
from score_listings import score_listing_record  # noqa: E402


def score_url(url: str) -> None:
    print(f"\n{'='*72}\n{url}\n{'='*72}", flush=True)
    with scrape_zillow._new_session() as session:
        listings = scrape_zillow.discover_listings_from_url(url, session=session)
        if not listings:
            print("  no listing parsed")
            return
        listing = listings[0]
        rec = asdict(listing)
        scrape_zillow.enrich_agents([rec], listing.city, listing.state, session=session)
        scored = score_listing_record(rec)
        n = scored.get("scored_photos", 0)
        price = scored.get("list_price")
        price_s = f"${price:,}" if price else "?"
        print(f"  {scored.get('address')}  ({price_s})")
        print(f"  photos scored: {n}/{scored.get('photo_count')}")
        print(f"  craft_score: {scored.get('craft_score')}  (clip_score field: {scored.get('clip_score')})")
        print(f"  vertical: {scored.get('vertical_score')}  pro_style(clip): {scored.get('pro_style_score')}")
        print(f"  reasons: {scored.get('score_reasons')}")


def main() -> None:
    urls = [u.strip().split("?")[0] for u in sys.argv[1:] if u.strip()]
    if not urls:
        raise SystemExit("usage: score_examples.py URL [URL ...]")
    for url in urls:
        score_url(url)


if __name__ == "__main__":
    main()
