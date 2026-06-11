"""Score the hand-labeled validation galleries and show the craft breakdown.

Reads a manifest produced by ``fetch_galleries.py`` and, for each listing,
aggregates the per-photo ``photo_quality`` sub-scores (trimmed mean, same as the
listing scorer) so we can verify the 5 BAD listings score low and the 1 GOOD one
scores high — and see *which* signal is responsible when tuning weights.

Run:  ./venv/bin/python scripts/calibrate_craft.py [manifest.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photo_quality as pq  # noqa: E402
import photos  # noqa: E402
from score_listings import MIN_GOOD_PHOTO_COUNT, SAMPLE_SIZE, _gallery_craft_score  # noqa: E402

SUBS = ["window_pull_score", "brightness_score", "dynamic_range_score",
        "white_balance_score", "straightness_score", "sharpness_score",
        "framing_score", "shadow_score", "orientation_score"]
SHORT = {"window_pull_score": "win", "brightness_score": "brt",
         "dynamic_range_score": "dr", "white_balance_score": "wb",
         "straightness_score": "str", "sharpness_score": "shp",
         "framing_score": "frm", "shadow_score": "shd", "orientation_score": "ori"}


def score_listing(rec: dict) -> dict:
    # Re-download from photo_urls so URL upgrades (full-size, not thumbnails)
    # apply; the cache makes repeat runs instant.
    urls = rec.get("photo_urls") or []
    paths = photos.download_many(urls, limit=SAMPLE_SIZE) if urls else (rec.get("local_paths") or [])
    qs = []
    for p in paths:
        try:
            qs.append(pq.analyze(p))
        except ValueError:
            continue
    if not qs:
        return {}
    crafts = [q.craft_score for q in qs]
    craft = _gallery_craft_score(crafts)
    photo_count = int(rec.get("photo_count") or len(paths))
    score = craft * (0.75 if 2 <= photo_count < MIN_GOOD_PHOTO_COUNT else 1.0)
    subs = {s: float(np.mean([getattr(q, s) for q in qs])) for s in SUBS}
    # most common flags across the gallery
    from collections import Counter
    flags = Counter(f.split(" (")[0] for q in qs for f in q.flags)
    return {"craft": craft, "score": score, "n": len(qs),
            "photo_count": photo_count, "subs": subs,
            "top_flags": [f"{k} x{v}" for k, v in flags.most_common(4)]}


def main() -> None:
    manifest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "labeled_galleries.json"
    data = json.loads(manifest.read_text())
    hdr = f"{'label':<7}{'craft':>6}{'score':>7} " + " ".join(f"{SHORT[s]:>5}" for s in SUBS)
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for key, rec in data.items():
        r = score_listing(rec)
        if not r:
            print(f"{rec.get('label','?'):<7}  (no photos)  {rec.get('address','')}")
            continue
        rows.append((rec.get("label", "?"), rec.get("address", key), r))
        subs = r["subs"]
        print(f"{rec.get('label','?'):<7}{r['craft']:>6.2f}{r['score']:>7.2f} "
              + " ".join(f"{subs[s]:>5.2f}" for s in SUBS)
              + f"  {rec.get('address','')[:30]}")
        print(f"         flags: {', '.join(r['top_flags'])}")
    bad = [r for lbl, _, r in rows if lbl == "target"]
    good = [r for lbl, _, r in rows if lbl == "good"]
    if bad and good:
        print("-" * len(hdr))
        print(f"BAD (target) craft: min {min(b['craft'] for b in bad):.2f} "
              f"max {max(b['craft'] for b in bad):.2f}")
        print(f"GOOD craft        : {good[0]['craft']:.2f}")
        margin = good[0]['craft'] - max(b['craft'] for b in bad)
        print(f"separation margin (good - worst-bad) = {margin:+.2f}  "
              f"({'PASS' if margin > 0 else 'FAIL'})")


if __name__ == "__main__":
    main()
