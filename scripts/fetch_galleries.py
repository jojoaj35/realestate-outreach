"""Fetch full photo galleries for a set of Zillow listings and cache them.

Re-scoring needs the whole gallery (not just the hero), but the queue only
stores the hero URL. This re-fetches each listing's detail page via the Zillow
stealth browser (~35s/page), records every photo URL, and downloads the bytes
into the shared photo cache. Results are written to a JSON manifest so re-runs
are instant and resumable.

Usage:
    # explicit url,label pairs (label optional)
    python scripts/fetch_galleries.py --manifest data/labeled_galleries.json \
        good=https://www.zillow.com/homedetails/.../X_zpid/ \
        target=https://www.zillow.com/homedetails/.../Y_zpid/

    # every listing currently in the queue (label taken from status)
    python scripts/fetch_galleries.py --manifest data/queue_galleries.json --from-queue
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photos  # noqa: E402
import scrape_zillow  # noqa: E402
from config import DATA_DIR  # noqa: E402

_ZPID = re.compile(r"/(\d+)_zpid/")


def _zpid(url: str) -> str:
    m = _ZPID.search(url)
    return m.group(1) if m else url


def fetch_one(url: str, session, download: bool = True, sample: int = 12) -> dict | None:
    res = scrape_zillow.discover_listings_from_url(url, max_urls=1, session=session)
    if not res:
        return None
    l = res[0]
    urls = list(l.photo_urls or [])
    rec = {
        "listing_id": str(l.listing_id or _zpid(url)),
        "url": url,
        "address": l.address,
        "list_price": l.list_price,
        "photo_count": l.photo_count or len(urls),
        "photo_urls": urls,
    }
    if download and urls:
        rec["local_paths"] = [str(p) for p in photos.download_many(urls, limit=sample)]
    return rec


def load_manifest(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch + cache Zillow galleries.")
    ap.add_argument("pairs", nargs="*", help="label=url or just url")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--from-queue", action="store_true")
    ap.add_argument("--no-download", action="store_true")
    ap.add_argument("--sample", type=int, default=12)
    args = ap.parse_args()

    targets: list[tuple[str, str]] = []  # (label, url)
    for p in args.pairs:
        if "=" in p:
            label, url = p.split("=", 1)
        else:
            label, url = "", p
        targets.append((label, url))

    if args.from_queue:
        with (DATA_DIR / "queue.csv").open(newline="") as f:
            for r in csv.DictReader(f):
                if r.get("url"):
                    targets.append((r.get("status", ""), r["url"]))

    manifest = load_manifest(Path(args.manifest))
    out_path = Path(args.manifest)
    with scrape_zillow._new_session() as session:
        for i, (label, url) in enumerate(targets, 1):
            key = _zpid(url)
            if key in manifest and manifest[key].get("photo_urls"):
                print(f"[{i}/{len(targets)}] cached {key}", flush=True)
                continue
            print(f"[{i}/{len(targets)}] fetching {url}", flush=True)
            try:
                rec = fetch_one(url, session, download=not args.no_download, sample=args.sample)
            except Exception as e:  # noqa: BLE001
                print(f"   ERROR: {e}", flush=True)
                rec = None
            if rec:
                rec["label"] = label
                manifest[key] = rec
                save_manifest(out_path, manifest)  # checkpoint after each
                print(f"   ok: {rec['photo_count']} photos, "
                      f"{len(rec.get('local_paths', []))} downloaded", flush=True)
            else:
                print("   no result", flush=True)
    print(f"\nManifest -> {out_path} ({len(manifest)} listings)")


if __name__ == "__main__":
    main()
