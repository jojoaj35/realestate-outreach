"""Score whole listings (not just single files).

Bridges the scraper output (remote photo URLs) and the photo scorer:
downloads a sample of each listing's photos, scores them, and produces a
single listing-level score + human-readable reasons.

Note on resolution: scraped images are display-size variants, so the
``low resolution`` heuristic is unreliable here and is intentionally ignored.
We lean on CLIP similarity to your pro set, blur, exposure, and photo count.

Usage:
    python src/score_listings.py listings.json --out scored_listings.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import heuristics
import photos
from config import settings
from score import clip_similarity

SAMPLE_SIZE = 6          # photos to download + score per listing
MIN_GOOD_PHOTO_COUNT = 8  # listings with fewer photos get penalized


def _normalize_sim(sim: float) -> float:
    return max(0.0, min(1.0, (sim - 0.4) / 0.5))


def score_listing_record(listing: dict, sample_size: int = SAMPLE_SIZE) -> dict:
    """Return a copy of ``listing`` with ``score`` and ``score_reasons`` added."""
    out = dict(listing)
    urls = listing.get("photo_urls") or []
    photo_count = int(listing.get("photo_count") or len(urls))

    local_paths = photos.download_many(urls, limit=sample_size)
    if not local_paths:
        out["score"] = 0.0
        out["score_reasons"] = "no downloadable photos"
        out["scored_photos"] = 0
        return out

    photo_finals: list[float] = []
    sims: list[float] = []
    n_blurry = n_bad_exposure = n_portrait = 0

    for path in local_paths:
        try:
            h = heuristics.score_image(path)
        except ValueError:
            continue
        sim_norm = _normalize_sim(clip_similarity(path))
        sims.append(sim_norm)

        # Recompute the heuristic score ignoring the (untrustworthy) low-res flag.
        flags = [h.is_blurry, h.is_over_or_under_exposed, h.is_portrait]
        heur3 = 1.0 - (sum(flags) / len(flags))
        photo_finals.append(0.4 * heur3 + 0.6 * sim_norm)

        n_blurry += int(h.is_blurry)
        n_bad_exposure += int(h.is_over_or_under_exposed)
        n_portrait += int(h.is_portrait)

    if not photo_finals:
        out["score"] = 0.0
        out["score_reasons"] = "photos unreadable"
        out["scored_photos"] = 0
        return out

    score = float(np.mean(photo_finals))
    reasons: list[str] = []

    if n_blurry:
        reasons.append(f"{n_blurry}/{len(photo_finals)} sampled photos blurry")
    if n_bad_exposure:
        reasons.append(f"{n_bad_exposure}/{len(photo_finals)} poorly exposed")
    if n_portrait:
        reasons.append(f"{n_portrait} portrait-orientation shots")
    if sims and float(np.mean(sims)) < 0.5:
        reasons.append("photos don't match a professional style")
    if photo_count < MIN_GOOD_PHOTO_COUNT:
        reasons.append(f"only {photo_count} photos on the listing")
        score *= 0.75

    out["score"] = round(score, 4)
    out["score_reasons"] = "; ".join(reasons) if reasons else "no major issues found"
    out["scored_photos"] = len(photo_finals)
    return out


def score_file(in_path: Path, out_path: Path) -> list[dict]:
    listings = json.loads(in_path.read_text())
    scored = []
    for i, listing in enumerate(listings, 1):
        addr = listing.get("address") or listing.get("listing_id") or "?"
        print(f"[{i}/{len(listings)}] scoring {addr} ...", flush=True)
        scored.append(score_listing_record(listing))

    scored.sort(key=lambda r: r.get("score", 1.0))
    out_path.write_text(json.dumps(scored, indent=2))

    below = [r for r in scored if r.get("score", 1.0) <= settings.outreach_score_threshold]
    print(f"\nScored {len(scored)} listings -> {out_path}")
    print(f"  {len(below)} at/below outreach threshold ({settings.outreach_score_threshold})")
    return scored


def main() -> None:
    ap = argparse.ArgumentParser(description="Score scraped listings by photo quality.")
    ap.add_argument("listings", help="listings.json from scrape_realtor.py")
    ap.add_argument("--out", default="scored_listings.json")
    args = ap.parse_args()
    score_file(Path(args.listings), Path(args.out))


if __name__ == "__main__":
    main()
