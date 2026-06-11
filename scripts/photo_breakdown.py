"""Per-photo craft breakdown for one listing URL."""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photos  # noqa: E402
import photo_quality  # noqa: E402
import scrape_zillow  # noqa: E402


def main() -> None:
    url = sys.argv[1].split("?")[0]
    with scrape_zillow._new_session() as session:
        listings = scrape_zillow.discover_listings_from_url(url, session=session)
        rec = asdict(listings[0])
        scrape_zillow.enrich_agents([rec], rec["city"], rec["state"], session=session)
    paths = photos.download_many(rec["photo_urls"], limit=25)
    print(f"{rec.get('address')} — {len(paths)} photos\n")
    print(f"{'#':>3} {'craft':>6} {'bright':>6} {'window':>6} {'frame':>6} {'vert':>6}  flags")
    for i, p in enumerate(paths):
        q = photo_quality.analyze(p)
        flags = "; ".join(q.flags[:2]) if q.flags else "-"
        print(f"{i:3d} {q.craft_score:6.3f} {q.brightness_score:6.3f} {q.window_pull_score:6.3f} "
              f"{q.framing_score:6.3f} {q.straightness_score:6.3f}  {flags[:55]}")


if __name__ == "__main__":
    main()
