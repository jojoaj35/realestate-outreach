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
import photo_quality
import photos
from config import settings
from score import classify

SAMPLE_SIZE = 25         # score most of the gallery, not just the hero (up to this many)
MIN_GOOD_PHOTO_COUNT = 8  # listings with fewer photos get penalized

# Scoring weights — recalibrated to measure PHOTOGRAPHIC CRAFT, not house quality.
#
# The previous design let the CLIP pro/amateur k-NN dominate (W_PRO_STYLE=0.80).
# That classifier separates the labeled sets well *only because the labels are
# content-confounded* (the "pro" folder is nice/expensive homes, the "amateur"
# folder is modest ones). On content-agnostic photo-craft signals the two sets
# are indistinguishable (see scripts/diagnose_model.py), proving CLIP keyed on
# how nice the *house* looks, not on how well the photo was *shot*. So the
# headline score is now the objective craft score from ``photo_quality`` and
# CLIP is removed from it (kept only for transparency in ``pro_style_score`` and
# for aerial/drone detection, which is a different, non-confounded prompt).
W_CRAFT = 1.0            # objective photographic craft — the entire headline
W_CLIP_STYLE = 0.0       # CLIP k-NN: content/price-confounded, removed from score
# Aggregate per-photo craft with a PLAIN MEAN (no trimming). The old code trimmed
# the worst photos, which for outreach is backwards: a listing is a good target
# precisely *because* it has amateur photos, so we must not hide them.
CRAFT_TRIM_FRAC = 0.0
PRO_TRIM_FRAC = 0.1      # (legacy name kept for diagnose_model import compat)


def _gallery_craft_score(values: list[float]) -> float:
    """Blend typical quality with the worst shots in the gallery.

    Amateurs often have a decent exterior hero but dark/crooked/tight interior
    shots. Weight the bottom of the distribution heavily so a few bright
    exteriors cannot rescue an otherwise phone-quality gallery.
    """
    if not values:
        return 0.0
    if len(values) < 4:
        return float(np.mean(values))
    mean = float(np.mean(values))
    p25 = float(np.percentile(values, 25))
    p10 = float(np.percentile(values, 10))
    return 0.25 * mean + 0.35 * p25 + 0.40 * p10


def _apply_gallery_penalties(
    craft: float,
    craft_finals: list[float],
    bright_scores: list[float],
    framing_scores: list[float],
) -> float:
    """Cap listings where many room shots are weak despite a bright hero."""
    n = len(craft_finals)
    if n < 4:
        return craft

    p20 = float(np.percentile(craft_finals, 20))
    weak_frac = sum(c < 0.78 for c in craft_finals) / n
    if weak_frac >= 0.40:
        craft = min(craft, 0.12 + 0.45 * p20)
    elif weak_frac >= 0.25:
        craft = min(craft, 0.20 + 0.48 * p20)

    if bright_scores and sum(b < 0.5 for b in bright_scores) / len(bright_scores) >= 0.55:
        bright_avg = float(np.mean(bright_scores))
        craft = min(craft, 0.28 + 0.55 * bright_avg)

    if framing_scores and sum(f < 0.5 for f in framing_scores) / len(framing_scores) >= 0.20:
        craft = min(craft, craft * 0.85)

    return craft


def _trimmed_mean(values: list[float], trim_frac: float = PRO_TRIM_FRAC) -> float:
    """Mean after dropping the lowest ``trim_frac`` of values.

    A genuinely professional listing often includes a few weak detail shots
    (dark closets, garages, tight bathrooms). A plain mean lets those drag the
    whole listing down; trimming the worst few keeps the score representative of
    the listing's typical quality while still reacting to mostly-bad galleries.
    """
    if not values:
        return 0.0
    if len(values) < 5:
        return float(np.mean(values))
    ordered = sorted(values)
    drop = int(len(ordered) * trim_frac)
    kept = ordered[drop:] if drop else ordered
    return float(np.mean(kept))


def score_listing_record(listing: dict, sample_size: int = SAMPLE_SIZE) -> dict:
    """Return a copy of ``listing`` with photo-quality + drone fields added.

    Key fields added:
      - ``clip_score``  pro-photo probability (1.0 = looks professional/edited)
      - ``score``       overall blended quality (lower = better outreach target)
      - ``has_drone``   True if any sampled photo looks like an aerial/drone shot
    """
    out = dict(listing)
    urls = listing.get("photo_urls") or []
    photo_count = int(listing.get("photo_count") or len(urls))

    local_paths = photos.download_many(urls, limit=sample_size)
    if not local_paths:
        out["score"] = 0.0
        out["clip_score"] = 0.0
        out["vertical_score"] = 0.0
        out["pro_style_score"] = 0.0
        out["has_drone"] = False
        out["score_reasons"] = "no downloadable photos"
        out["scored_photos"] = 0
        return out

    craft_finals: list[float] = []   # objective per-photo craft score (headline)
    photo_finals: list[float] = []   # craft, optionally nudged by CLIP style
    bright_scores: list[float] = []  # per-photo brightness (dark/dull detector)
    framing_scores: list[float] = []  # per-photo room framing (wall-heavy vs room)
    pro_probs: list[float] = []      # CLIP style only (kept for transparency)
    vert_scores: list[float] = []    # vertical straightness only
    vert_devs: list[float] = []
    max_aerial = 0.0
    n_drone = n_blurry = n_blown = n_dark = n_cast = n_portrait = n_tilted = 0

    for path in local_paths:
        try:
            q = photo_quality.analyze(path)
        except ValueError:
            continue
        c = classify(path)
        pro = c["pro_prob"]
        pro_probs.append(pro)

        craft_finals.append(q.craft_score)
        bright_scores.append(q.brightness_score)
        framing_scores.append(q.framing_score)
        # CLIP is removed from the headline (W_CLIP_STYLE=0) because it keys on
        # house content, not photo craft; the blend keeps it tunable.
        photo_finals.append(W_CRAFT * q.craft_score + W_CLIP_STYLE * pro)

        vert_scores.append(q.straightness_score)
        if q.vertical_dev_deg > 0:
            vert_devs.append(q.vertical_dev_deg)
            if q.vertical_dev_deg >= heuristics.VERT_BAD_DEG:
                n_tilted += 1

        if c["aerial_prob"] >= settings.aerial_threshold:
            n_drone += 1
        max_aerial = max(max_aerial, c["aerial_prob"])

        if q.sharpness_score < 0.5:
            n_blurry += 1
        if q.window_pull_score < 0.5:
            n_blown += 1
        if q.brightness_score < 0.5:
            n_dark += 1
        if q.white_balance_score < 0.5 and q.cast_chroma > 0:
            n_cast += 1
        if q.is_portrait:
            n_portrait += 1

    if not photo_finals:
        out["score"] = 0.0
        out["clip_score"] = 0.0
        out["vertical_score"] = 0.0
        out["pro_style_score"] = 0.0
        out["has_drone"] = False
        out["score_reasons"] = "photos unreadable"
        out["scored_photos"] = 0
        return out

    craft = _apply_gallery_penalties(
        _gallery_craft_score(craft_finals), craft_finals, bright_scores, framing_scores,
    )
    score = _apply_gallery_penalties(
        _gallery_craft_score(photo_finals), craft_finals, bright_scores, framing_scores,
    )
    pro_style = _trimmed_mean(pro_probs) if pro_probs else 0.0
    vertical = float(np.mean(vert_scores)) if vert_scores else 0.5
    # Headline "photo-craft score": objective, content-agnostic. This is the
    # number the UI shows (lower = worse photography = better outreach target).
    clip_score = craft
    has_drone = n_drone > 0
    mean_dev = float(np.mean(vert_devs)) if vert_devs else 0.0
    out["clip_score"] = round(clip_score, 4)   # 1.0 = professionally shot/edited
    out["craft_score"] = round(craft, 4)
    out["pro_style_score"] = round(pro_style, 4)
    out["vertical_score"] = round(vertical, 4)
    out["vertical_dev_deg"] = round(mean_dev, 2)
    out["has_drone"] = has_drone
    out["has_virtual_tour"] = bool(listing.get("has_virtual_tour"))
    out["aerial_prob"] = round(max_aerial, 4)
    n = len(photo_finals)
    reasons: list[str] = []

    if has_drone:
        reasons.append(f"{n_drone} drone/aerial photo(s) detected")
    if n_blown:
        reasons.append(f"{n_blown}/{n} with blown-out windows (no window pull)")
    if n_dark:
        reasons.append(f"{n_dark}/{n} dark/dull (under-exposed)")
    if n_cast:
        reasons.append(f"{n_cast}/{n} with a color cast (white balance off)")
    if vert_devs and vertical < 0.4:
        reasons.append(f"crooked verticals (~{mean_dev:.1f}° off, camera tilted)")
    elif vert_devs and vertical >= 0.85:
        reasons.append(f"straight verticals (~{mean_dev:.1f}° off, shot level)")
    if n_blurry:
        reasons.append(f"{n_blurry}/{n} soft / motion-blurred")
    if n_portrait:
        reasons.append(f"{n_portrait} portrait-orientation phone shots")
    n_bad_frame = sum(1 for f in framing_scores if f < 0.5)
    if n_bad_frame:
        reasons.append(f"{n_bad_frame}/{n} poor room framing (too much wall)")
    if craft < 0.6:
        reasons.append("low photographic craft (amateur capture)")

    # Only judge photo count when we actually know it. A count of 0/1 usually
    # means we only had the hero photo (data limitation), not a real flaw.
    if photo_count >= 2 and photo_count < MIN_GOOD_PHOTO_COUNT:
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
