"""Find Zillow listings with NON-PROFESSIONAL photos (the real outreach opportunity).

Most listings use a hired photographer; we want the DIY/phone-photo ones. The
signal for that is CLIP similarity to your professional reference set: low
similarity = amateur-looking photos.

Two-phase for speed (all via Scrapling's stealth browser against Zillow):
  1. Discover many listings (hero photo only, fast) and craft-score each hero.
  2. Keep the least-professional N, then enrich only those (listing-agent contact
     + full photo set) and re-score up to 25 gallery photos (not just the hero).
     The listing score blends the typical shot with the worst quarter so dark/
     crooked interior photos pull the score down even when the exterior hero is OK.

Result: those N get queued for outreach and can be exported to .xls.

Usage:
    python src/scan.py --city Austin --state TX --count 200 --keep 15
    python src/scan.py --url "https://www.zillow.com/homes/Austin,-TX_rb/" --keep 15
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import photo_quality
import photos
import score_listings
import scrape_zillow
from config import settings
from store import get_store

# Phase 1 (hero) is only a cheap pre-filter; enrich this many times ``keep``
# candidates so the full-gallery score in phase 2 — not the hero — drives the
# keep/queue decision. A listing with a nice hero but bad interiors still gets
# its whole gallery scored.
HERO_PREFILTER_MULTIPLE = 2.0


def _hero_clip(listing: dict) -> float | None:
    """Photographic-craft score of the hero shot (the agent's chosen-best photo).

    Uses the same objective craft score as the full listing score
    (``score_listings``) so the candidates we keep in phase 1 are ranked by the
    same content-agnostic signal we judge them on in phase 2.
    """
    urls = listing.get("photo_urls") or []
    if not urls:
        return None
    p = photos.download(urls[0])
    if not p:
        return None
    try:
        return photo_quality.craft_score(p)
    except ValueError:
        return None


def scan(city: str = "Austin", count: int = 200, keep: int = 15,
         max_pro_score: float | None = None, state: str = "TX",
         url: str | None = None, progress=None, **_ignored) -> list[dict]:
    """Two-phase Zillow scan that queues the least-professional listings.

    Phase 1 (cheap PRE-FILTER only): discover listings off a Zillow city search
    or a pasted ``url``. Each search card carries a hero photo, so we craft-score
    the hero just to decide which detail pages are worth opening. The hero is
    NOT the decision — it only widens/narrows the enrichment pool.

    Phase 2 (the DECISION): open each pre-filtered listing's detail page for the
    listing-agent contact AND the full photo gallery, then score the WHOLE
    gallery (``score_listings``). The queue/keep decision is made on that
    full-gallery craft score, so a listing with a flattering hero but dark,
    dull, or crooked *interior* photos is still caught. Because the hero is the
    agent's chosen-best shot, ranking by hero alone would hide exactly those, so
    phase 1 deliberately enriches a wider pool (``HERO_PREFILTER_MULTIPLE`` ×
    ``keep``) and lets the full-gallery score sort it out.
    """
    def report(**kw):
        if progress:
            progress(kw)

    url = (url or "").strip()
    if url:
        print(f"== phase 1: scanning Zillow link (photos only) ==\n  {url}")
        report(phase="scanning", message="Scanning Zillow link…")
        listings = scrape_zillow.discover_listings_from_url(url, max_urls=max(count, 25))
    else:
        print(f"== phase 1: discovering {count} {city} Zillow listings (photos only) ==")
        report(phase="scanning", message=f"Discovering {city} Zillow listings via Scrapling…")
        listings = scrape_zillow.discover_listings(city, state, max_urls=max(count, 25))

    print(f"  found {len(listings)} listings", flush=True)
    if not listings:
        report(phase="done", added=0, updated=0, message="No Zillow listings found.")
        return []

    raw: list[dict] = [asdict(l) for l in listings[:count]]

    scored = []
    tour_skipped = 0
    for i, d in enumerate(raw, 1):
        if settings.exclude_virtual_tour_listings and d.get("has_virtual_tour"):
            tour_skipped += 1
            continue
        c = _hero_clip(d)
        if c is None:
            continue
        d["hero_clip"] = round(c, 4)
        scored.append(d)
        if i % 5 == 0 or i == len(raw):
            report(phase="scoring", done=i, total=len(raw),
                   message=f"Ranking photos {i}/{len(raw)}…")

    scored.sort(key=lambda d: d["hero_clip"])
    # Enrich a WIDER pool than we intend to queue: the hero is only a pre-filter,
    # and the real keep/queue decision happens on the full gallery in phase 2.
    # This prevents excluding a listing just because its hero looked fine.
    enrich_n = max(keep, int(round(keep * HERO_PREFILTER_MULTIPLE)))
    if max_pro_score is not None:
        candidates = [d for d in scored if d["hero_clip"] <= max_pro_score][:enrich_n]
    else:
        candidates = scored[:enrich_n]

    if scored:
        print(f"  hero CLIP range: {scored[0]['hero_clip']:.2f} (worst) .. "
              f"{scored[-1]['hero_clip']:.2f} (best)")
    print(f"== phase 2: enriching {len(candidates)} kept listings (agent contact) ==")
    report(phase="enriching", done=0, total=len(candidates),
           message=f"Opening detail pages for {len(candidates)} kept listings…")

    # One stealth browser session reads agent contact + full gallery for all kept.
    scrape_zillow.enrich_agents(candidates, city, state)

    enriched = []
    drone_skipped = 0
    no_agent_skipped = 0
    for i, d in enumerate(candidates, 1):
        if not scrape_zillow.valid_listing_agent(d.get("agent_name", "")):
            no_agent_skipped += 1
            print(f"  [{i}/{len(candidates)}] NO LISTING AGENT — skip "
                  f"{d.get('address','')[:32]}", flush=True)
            continue
        full = score_listings.score_listing_record(d)
        report(phase="enriching", done=i, total=len(candidates),
               message=f"Enriched {i}/{len(candidates)}…")
        if settings.exclude_drone_listings and full.get("has_drone"):
            drone_skipped += 1
            print(f"  [{i}/{len(candidates)}] DRONE — skip {full.get('address','')[:32]}", flush=True)
            continue
        enriched.append(full)
        print(f"  [{i}/{len(candidates)}] pro={full.get('clip_score')}  "
              f"{full.get('address','')[:32]:32s} -> {full.get('agent_name') or '?'} "
              f"({full.get('agent_phone') or 'no phone'})", flush=True)

    enriched.sort(key=lambda d: d.get("clip_score", 1.0))
    store = get_store()
    added, updated = store.upsert_listings(enriched)
    contactable = sum(1 for d in enriched if d.get("agent_phone") and d.get("agent_name"))
    no_phone = len(enriched) - contactable
    msg = f"Done — {added} new bad-photo listings stored ({contactable} with listing-agent phone)."
    if no_phone:
        msg += f" ({no_phone} skipped — no verified listing-agent contact)"
    if no_agent_skipped:
        msg += f" ({no_agent_skipped} skipped — no listing agent on Zillow)"
    if drone_skipped:
        msg += f" ({drone_skipped} skipped for drone photos)"
    print(f"\nStored {added} new ({updated} updated). {contactable} listing-agent contactable, "
          f"{no_phone} without phone, {tour_skipped} virtual-tour, {drone_skipped} drone, "
          f"{no_agent_skipped} no listing agent.")
    report(phase="done", added=added, updated=updated,
           tour_skipped=tour_skipped, drone_skipped=drone_skipped, message=msg)
    return enriched


def main() -> None:
    ap = argparse.ArgumentParser(description="Find non-professional-photo Zillow listings.")
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--state", default="TX")
    ap.add_argument("--url", default="", help="scan a Zillow search/listing link directly")
    ap.add_argument("--count", type=int, default=200, help="how many listings to scan")
    ap.add_argument("--keep", type=int, default=15,
                    help="target queue size; phase 1 enriches HERO_PREFILTER_MULTIPLE× "
                         "this many, and the full-gallery score decides what is queued")
    ap.add_argument("--max-pro-score", type=float, default=None,
                    help="only keep listings with hero CLIP <= this (e.g. 0.5)")
    args = ap.parse_args()
    scan(args.city, count=args.count, keep=args.keep, max_pro_score=args.max_pro_score,
         state=args.state, url=args.url or None)


if __name__ == "__main__":
    main()
