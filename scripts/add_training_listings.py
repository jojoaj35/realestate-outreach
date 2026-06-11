"""Download Zillow listing galleries into a training_data label folder.

Use this to teach the photo classifier from real listings — e.g. labeling
twilight/dusk exteriors as professional so the scanner stops flagging them.

After adding listings, retrain with:  python src/train_clip.py

Usage:
    python scripts/add_training_listings.py good  URL [URL ...]
    python scripts/add_training_listings.py target URL [URL ...]

Each listing's photos are saved under
    training_data/<label>/<slug>_<zpid>/NN.jpg
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photos  # noqa: E402
import scrape_zillow  # noqa: E402

TRAIN = ROOT / "training_data"
_SLUG_RE = re.compile(r"/homedetails/([^/]+)/(\d+)_zpid", re.I)


def _slug_for(url: str, listing) -> str:
    m = _SLUG_RE.search(url)
    if m:
        return f"{m.group(1)}_{m.group(2)}"
    return listing.listing_id or "listing"


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("usage: add_training_listings.py <good|target> URL [URL ...]")
    label = sys.argv[1].strip().lower()
    if label not in {"good", "target"}:
        raise SystemExit("label must be 'good' or 'target'")

    # Clean pasted URLs (strip stray spaces, trailing punctuation, query strings).
    urls = []
    for raw in sys.argv[2:]:
        u = raw.strip().strip(",").replace(" ", "")
        if u:
            urls.append(u.split("?")[0])

    out_root = TRAIN / label
    total = 0
    with scrape_zillow._new_session() as session:
        for url in urls:
            print(f"\n== {url} ==", flush=True)
            listings = scrape_zillow.discover_listings_from_url(url, session=session)
            if not listings or not listings[0].photo_urls:
                print("  no photos parsed — skipping", flush=True)
                continue
            listing = listings[0]
            dest = out_root / _slug_for(url, listing)
            dest.mkdir(parents=True, exist_ok=True)
            saved = 0
            for purl in listing.photo_urls:
                p = photos.download(purl)
                if not p:
                    continue
                (dest / f"{saved:02d}.jpg").write_bytes(Path(p).read_bytes())
                saved += 1
            total += saved
            print(f"  saved {saved} photos -> {dest}", flush=True)

    print(f"\nDone — saved {total} photos into training_data/{label}/.")
    print("Now retrain:  python src/train_clip.py")


if __name__ == "__main__":
    main()
