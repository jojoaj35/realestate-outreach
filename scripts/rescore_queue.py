"""Re-score every listing in the queue with the new photo-craft scoring.

Backs up ``data/queue.csv`` first, then for each row re-scores the WHOLE gallery
(from the ``fetch_galleries.py`` manifest) with ``score_listings`` and rewrites
the craft fields. Outreach ``status`` is PRESERVED for every existing row (same
contract as ``store.upsert_listings``) — re-scoring never sends anything and
never auto-queues. Prints a before/after table sorted by the new craft score.

Run:
    ./venv/bin/python scripts/fetch_galleries.py --manifest data/queue_galleries.json --from-queue
    ./venv/bin/python scripts/rescore_queue.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import score_listings  # noqa: E402
from config import DATA_DIR  # noqa: E402
from store import FIELDNAMES  # noqa: E402

_ZPID = re.compile(r"/(\d+)_zpid/")
QUEUE = DATA_DIR / "queue.csv"
MANIFEST = DATA_DIR / "queue_galleries.json"

# Re-scored fields (status and outreach progress are deliberately untouched).
UPDATE_FIELDS = ("score", "clip_score", "pro_style_score", "vertical_score",
                 "has_drone", "score_reasons")


def _zpid(url: str) -> str:
    m = _ZPID.search(url or "")
    return m.group(1) if m else ""


def main() -> None:
    rows = list(csv.DictReader(QUEUE.open(newline="")))
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    by_zpid = {k: v for k, v in manifest.items()}

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = DATA_DIR / f"queue_backup_{ts}.csv"
    backup.write_text(QUEUE.read_text())
    print(f"backed up queue.csv -> {backup.name}\n")

    table = []
    for r in rows:
        zpid = r.get("listing_id") or _zpid(r.get("url", ""))
        rec = by_zpid.get(zpid)
        old_clip = r.get("clip_score", "")
        if not rec or not rec.get("photo_urls"):
            table.append((r.get("address", ""), old_clip, "—", r.get("status", ""),
                          "no gallery fetched"))
            continue
        listing = {
            "listing_id": zpid,
            "photo_urls": rec["photo_urls"],
            "photo_count": rec.get("photo_count") or len(rec["photo_urls"]),
            "has_virtual_tour": r.get("has_virtual_tour") == "yes",
        }
        scored = score_listings.score_listing_record(listing)
        for k in UPDATE_FIELDS:
            if k == "has_drone":
                r[k] = "yes" if scored.get("has_drone") else ""
            elif scored.get(k) != "" and scored.get(k) is not None:
                r[k] = scored.get(k)
        r["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        table.append((r.get("address", ""), old_clip, scored.get("clip_score"),
                      r.get("status", ""), scored.get("score_reasons", "")))

    with QUEUE.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})

    def _newkey(t):
        try:
            return float(t[2])
        except (TypeError, ValueError):
            return 99.0
    table.sort(key=_newkey)
    print(f"{'address':<26}{'old_clip':>9}{'new_craft':>10}  {'status':<8} reasons")
    print("-" * 100)
    for addr, old, new, status, reasons in table:
        old_s = f"{float(old):.3f}" if _is_num(old) else str(old)
        new_s = f"{float(new):.3f}" if _is_num(new) else str(new)
        print(f"{addr[:25]:<26}{old_s:>9}{new_s:>10}  {status:<8} {reasons[:48]}")


def _is_num(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


if __name__ == "__main__":
    main()
