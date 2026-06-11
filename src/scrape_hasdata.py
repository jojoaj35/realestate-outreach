"""Scrape live Zillow listings via the HasData API (works from Render/datacenter).

HasData does the actual Zillow scraping on their infrastructure and returns
JSON; we map that into the shared ``Listing`` schema so the rest of the
pipeline (scoring, queue, outreach) is source-agnostic.

Two phases, mirroring scrape_unlockmls:
  1. Listing API: a search by "City, ST" returns many listings + hero photo
     (cheap — 5 credits/request, paginated).
  2. Property API (enrich): per-listing detail for agent contact + full photos
     (5 credits each; skip with enrich=False to save credits).

Usage:
    python src/scrape_hasdata.py --city Austin --state TX --max 40 --out listings.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape_realtor import Listing
from hasdata_client import HasDataError, hasdata_available, property_detail, search

_MONEY_RE = re.compile(r"[\d.]+")
_PHONE_DIGITS_RE = re.compile(r"\d")
_ZPID_RE = re.compile(r"/(\d+)_zpid")


def _first(d: dict, keys: list[str], default: Any = None) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def _money_to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v)
    m = _MONEY_RE.search(str(v).replace(",", ""))
    if not m:
        return None
    try:
        return int(float(m.group(0)))
    except ValueError:
        return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").split()[0])
    except (ValueError, IndexError):
        return None


def _zillow_abs_url(u: str) -> str:
    if not u:
        return ""
    if u.startswith("http"):
        return u
    return "https://www.zillow.com" + ("" if u.startswith("/") else "/") + u


def _listing_id(row: dict, url: str) -> str:
    zpid = _first(row, ["zpid", "id", "zpId"])
    if zpid:
        return str(zpid)
    m = _ZPID_RE.search(url or "")
    return m.group(1) if m else (url or "")


def find_listings_array(data: Any) -> list[dict]:
    """Walk the response for the largest list of listing-shaped dicts.

    Defensive against schema drift: HasData has used ``listings``,
    ``properties``, ``realEstateListings`` etc. as the array key.
    """
    candidates: list[list[dict]] = []

    def is_listing(d: Any) -> bool:
        return isinstance(d, dict) and any(
            k in d for k in ("zpid", "detailUrl", "zpId", "hdpUrl")
        )

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            if obj and sum(1 for x in obj if is_listing(x)) >= max(1, len(obj) // 2):
                candidates.append([x for x in obj if isinstance(x, dict)])
            for x in obj:
                walk(x)
        elif isinstance(obj, dict):
            for v in obj.values():
                walk(v)

    walk(data)
    return max(candidates, key=len) if candidates else []


def _collect_photo_urls(obj: Any, out: list[str], seen: set[str]) -> None:
    """Recursively gather image URLs from a listing/property structure."""
    if isinstance(obj, dict):
        for key in ("url", "imgSrc", "image", "src", "jpegUrl", "jpeg"):
            v = obj.get(key)
            if isinstance(v, str) and v.startswith("http") and any(
                ext in v.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")
            ):
                if v not in seen:
                    seen.add(v)
                    out.append(v)
        for v in obj.values():
            _collect_photo_urls(v, out, seen)
    elif isinstance(obj, list):
        for v in obj:
            _collect_photo_urls(v, out, seen)


def _find_agent_contact(obj: Any) -> dict:
    """Walk a property detail for agent name / phone / email / broker."""
    found = {"agent_name": "", "agent_phone": "", "agent_email": "", "broker_name": ""}

    def visit(d: Any) -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                kl = k.lower()
                if isinstance(v, str) and v.strip():
                    if not found["agent_name"] and kl in ("agentname", "listingagent", "name") and "broker" not in kl:
                        if "agent" in kl or kl == "name":
                            found["agent_name"] = v.strip()
                    if not found["agent_phone"] and "phone" in kl:
                        digits = "".join(_PHONE_DIGITS_RE.findall(v))
                        if len(digits) >= 10:
                            found["agent_phone"] = v.strip()
                    if not found["agent_email"] and "email" in kl and "@" in v:
                        found["agent_email"] = v.strip()
                    if not found["broker_name"] and ("brokername" in kl or "brokeragename" in kl):
                        found["broker_name"] = v.strip()
                elif isinstance(v, (dict, list)):
                    visit(v)
        elif isinstance(d, list):
            for x in d:
                visit(x)

    visit(obj)
    return found


def _parse_address(row: dict) -> tuple[str, str, str, str]:
    full = _first(row, ["address", "streetAddress", "fullAddress"], "")
    street = _first(row, ["addressStreet", "street"], "")
    city = _first(row, ["addressCity", "city"], "")
    state = _first(row, ["addressState", "state"], "")
    zip_code = str(_first(row, ["addressZipcode", "zipcode", "zip", "postalCode"], "") or "")
    if isinstance(full, str) and full and not street:
        street = full.split(",")[0].strip()
    return (street or (full if isinstance(full, str) else "")), city or "", state or "", zip_code


def parse_listing_row(row: dict) -> Listing | None:
    url = _zillow_abs_url(str(_first(row, ["detailUrl", "hdpUrl", "url"], "")))
    lid = _listing_id(row, url)
    if not lid:
        return None

    street, city, state, zip_code = _parse_address(row)
    price = _money_to_int(_first(row, ["unformattedPrice", "price", "listPrice"]))
    beds = _to_float(_first(row, ["beds", "bedrooms"]))
    baths = _to_float(_first(row, ["baths", "bathrooms"]))
    sqft = _money_to_int(_first(row, ["area", "livingArea", "livingAreaValue", "sqft"]))

    photos: list[str] = []
    seen: set[str] = set()
    hero = _first(row, ["imgSrc", "image"])
    if isinstance(hero, str) and hero.startswith("http"):
        photos.append(hero)
        seen.add(hero)
    _collect_photo_urls(row.get("carouselPhotos") or row.get("photos") or [], photos, seen)

    return Listing(
        listing_id=str(lid),
        url=url,
        address=street,
        city=city,
        state=state,
        zip_code=zip_code,
        list_price=price,
        beds=beds,
        baths=baths,
        sqft=sqft,
        photo_count=len(photos),
        photo_urls=photos[:40],
        broker_name=str(_first(row, ["brokerName", "brokerageName"], "") or ""),
        raw_source="hasdata",
    )


def enrich_listing(listing: Listing, extract_emails: bool = False) -> None:
    """Fetch the Zillow Property detail and fill agent contact + full photos."""
    if not listing.url:
        return
    try:
        detail = property_detail(listing.url, extract_emails=extract_emails)
    except HasDataError as e:
        print(f"    enrich error: {e}", flush=True)
        return

    prop = detail.get("property") if isinstance(detail, dict) else None
    target = prop or detail

    contact = _find_agent_contact(target)
    if contact["agent_name"]:
        listing.agent_name = contact["agent_name"]
    if contact["agent_phone"]:
        listing.agent_phone = contact["agent_phone"]
    if contact["agent_email"]:
        listing.agent_email = contact["agent_email"]
    if contact["broker_name"] and not listing.broker_name:
        listing.broker_name = contact["broker_name"]

    photos: list[str] = list(listing.photo_urls)
    seen: set[str] = set(photos)
    _collect_photo_urls(target, photos, seen)
    if len(photos) > listing.photo_count:
        listing.photo_urls = photos[:40]
        listing.photo_count = len(listing.photo_urls)

    listing.raw_source = "hasdata+detail"


def scrape(
    city: str = "Austin",
    state: str = "TX",
    max_results: int = 40,
    listing_type: str = "forSale",
    enrich: bool = True,
    extract_emails: bool = False,
    only_city: bool = True,
) -> list[Listing]:
    if not hasdata_available():
        raise HasDataError("Set HASDATA_API_KEY in .env (1,000 free credits at https://hasdata.com)")

    keyword = f"{city}, {state}".strip().strip(",")
    listings: list[Listing] = []
    seen: set[str] = set()
    page = 1
    max_pages = 20

    print(f"Scraping Zillow '{keyword}' ({listing_type}) via HasData…", flush=True)
    while len(listings) < max_results and page <= max_pages:
        params = {"keyword": keyword, "type": listing_type, "page": page}
        try:
            resp = search(params)
        except HasDataError as e:
            print(f"  search error (page {page}): {e}", flush=True)
            break

        rows = find_listings_array(resp)
        if not rows:
            break
        print(f"  page {page}: {len(rows)} listings", flush=True)

        new_this_page = 0
        for row in rows:
            listing = parse_listing_row(row)
            if not listing or listing.listing_id in seen:
                continue
            if only_city and listing.city and listing.city.strip().lower() != city.strip().lower():
                continue
            seen.add(listing.listing_id)
            listings.append(listing)
            new_this_page += 1
            if len(listings) >= max_results:
                break

        if new_this_page == 0:
            break
        page += 1
        time.sleep(0.5)

    if enrich:
        print(f"\nEnriching {len(listings)} listings with agent contact + photos…", flush=True)
        for i, listing in enumerate(listings, 1):
            enrich_listing(listing, extract_emails=extract_emails)
            tag = listing.agent_phone or "no phone"
            print(f"  [{i}/{len(listings)}] {(listing.address or listing.url)[:40]:40s} -> "
                  f"{listing.agent_name or '?'} ({tag})", flush=True)
            time.sleep(0.4)

    return listings[:max_results]


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape live Zillow listings via HasData.")
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--state", default="TX")
    ap.add_argument("--max", type=int, default=40)
    ap.add_argument("--type", default="forSale", choices=["forSale", "forRent", "sold"])
    ap.add_argument("--no-enrich", action="store_true", help="skip per-listing detail (saves credits)")
    ap.add_argument("--emails", action="store_true", help="also extract agent emails (+5 credits/row)")
    ap.add_argument("--all-cities", action="store_true", help="keep results outside the exact city")
    ap.add_argument("--out", default="listings.json")
    ap.add_argument("--debug-raw", action="store_true", help="dump first raw API response to hasdata_raw.json")
    args = ap.parse_args()

    if args.debug_raw:
        resp = search({"keyword": f"{args.city}, {args.state}", "type": args.type, "page": 1})
        Path("hasdata_raw.json").write_text(json.dumps(resp, indent=2)[:500_000])
        print("Wrote hasdata_raw.json (first response).")
        return

    listings = scrape(
        city=args.city, state=args.state, max_results=args.max,
        listing_type=args.type, enrich=not args.no_enrich,
        extract_emails=args.emails, only_city=not args.all_cities,
    )
    Path(args.out).write_text(json.dumps([asdict(l) for l in listings], indent=2))
    with_phone = sum(1 for l in listings if l.agent_phone)
    print(f"\nSaved {len(listings)} listings -> {args.out} ({with_phone} with agent phone)")


if __name__ == "__main__":
    main()
