"""Scrape realtor.com search results using a real Chrome browser (Playwright).

Usage:
    python src/scrape_realtor.py "https://www.realtor.com/realestateandhomes-search/San-Antonio_TX" \
        --pages 3 --out listings.json

Why a real browser:
- realtor.com blocks library-based HTTP clients via TLS/header fingerprinting.
- A real Chrome window passes all the fingerprint checks for free.
- If a CAPTCHA / "Press & Hold" challenge appears, the browser pauses and you
  solve it manually. Session cookies persist across runs in ./browser_profile/
  so you usually only need to do this once.

Strategy:
- Parse the <script id="__NEXT_DATA__"> JSON for both search and detail pages.
- Pagination: /pg-N suffix.
- Polite delay between pages.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Page

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / "browser_profile"

# Signals that we're staring at a bot challenge instead of a real page.
CHALLENGE_HINTS = [
    "press and hold",
    "verify you are human",
    "are you a robot",
    "perimeterx",
    "px-captcha",
    "access to this page has been denied",
    "checking your browser",
    "cf-challenge",
]


@dataclass
class Listing:
    listing_id: str
    url: str
    address: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    list_price: int | None = None
    beds: float | None = None
    baths: float | None = None
    sqft: int | None = None
    photo_count: int = 0
    photo_urls: list[str] = field(default_factory=list)
    agent_name: str = ""
    agent_phone: str = ""
    agent_email: str = ""
    broker_name: str = ""
    has_virtual_tour: bool = False
    virtual_tour_url: str = ""
    raw_source: str = ""


def polite_sleep(min_s: float = 2.0, max_s: float = 5.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def looks_like_challenge(html: str) -> bool:
    lower = html.lower()[:50000]
    return any(h in lower for h in CHALLENGE_HINTS)


def wait_for_human(page: Page, reason: str) -> None:
    print("\n" + "=" * 60)
    print(f"  HUMAN ACTION NEEDED: {reason}")
    print("  Solve the challenge in the Chrome window, then press Enter here.")
    print("=" * 60)
    input("  [press Enter when the real page is loaded] ")
    # Re-give the page a moment to settle after user clicks
    page.wait_for_load_state("domcontentloaded")


def fetch_html(page: Page, url: str, retry_on_challenge: bool = True) -> str:
    print(f"  GET {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    except PWTimeout:
        print("  (navigation timeout — continuing anyway)")
    # Let any client-side JS / Next.js hydrate
    try:
        page.wait_for_selector("script#__NEXT_DATA__", timeout=15_000)
    except PWTimeout:
        pass

    html = page.content()
    if looks_like_challenge(html):
        if retry_on_challenge:
            wait_for_human(page, "bot challenge detected")
            html = page.content()
        else:
            return ""
    return html


def extract_next_data(html: str) -> dict[str, Any] | None:
    # Quick regex pull avoids loading the whole BS4 tree just for one script tag.
    m = re.search(
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_search_results(data: dict) -> list[dict]:
    """Walk Next.js data tree for the results array. Defensive to schema drift."""
    candidates = []

    def walk(obj):
        if isinstance(obj, dict):
            if "results" in obj and isinstance(obj["results"], list):
                if obj["results"] and isinstance(obj["results"][0], dict) and (
                    "property_id" in obj["results"][0] or "listing_id" in obj["results"][0]
                ):
                    candidates.append(obj["results"])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    return max(candidates, key=len) if candidates else []


def parse_listing_summary(r: dict) -> Listing:
    pid = str(r.get("property_id") or r.get("listing_id") or "")
    loc = r.get("location") or {}
    addr = loc.get("address") or {}
    desc = r.get("description") or {}
    photos = r.get("photos") or []
    permalink = r.get("permalink") or ""
    url = f"https://www.realtor.com/realestateandhomes-detail/{permalink}" if permalink else ""

    return Listing(
        listing_id=pid,
        url=url,
        address=addr.get("line", "") or "",
        city=addr.get("city", "") or "",
        state=addr.get("state_code", "") or "",
        zip_code=addr.get("postal_code", "") or "",
        list_price=r.get("list_price"),
        beds=desc.get("beds"),
        baths=desc.get("baths_consolidated") or desc.get("baths"),
        sqft=desc.get("sqft"),
        photo_count=len(photos),
        photo_urls=[p.get("href") for p in photos if p.get("href")][:40],
        raw_source="search",
    )


def enrich_from_detail(listing: Listing, page: Page) -> None:
    if not listing.url:
        return
    html = fetch_html(page, listing.url)
    data = extract_next_data(html)
    if not data:
        return

    def find_agents(obj):
        out = []
        if isinstance(obj, dict):
            for key in ("advertisers", "agents"):
                v = obj.get(key)
                if isinstance(v, list):
                    out.extend(v)
            for v in obj.values():
                out.extend(find_agents(v))
        elif isinstance(obj, list):
            for v in obj:
                out.extend(find_agents(v))
        return out

    agents = find_agents(data)
    if agents:
        a = agents[0]
        listing.agent_name = a.get("name") or a.get("nickname") or ""
        phones = a.get("phones") or []
        if phones and isinstance(phones[0], dict):
            listing.agent_phone = phones[0].get("number", "") or ""
        elif isinstance(a.get("phone"), str):
            listing.agent_phone = a["phone"]
        listing.agent_email = a.get("email", "") or ""
        broker = a.get("broker") or {}
        if isinstance(broker, dict):
            listing.broker_name = broker.get("name", "") or ""

    def find_photos(obj):
        if isinstance(obj, dict):
            if "photos" in obj and isinstance(obj["photos"], list) and obj["photos"]:
                if isinstance(obj["photos"][0], dict) and "href" in obj["photos"][0]:
                    return obj["photos"]
            for v in obj.values():
                r = find_photos(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = find_photos(v)
                if r:
                    return r
        return None

    photos = find_photos(data)
    if photos and len(photos) > listing.photo_count:
        listing.photo_urls = [p.get("href") for p in photos if p.get("href")][:40]
        listing.photo_count = len(photos)

    listing.raw_source = "search+detail"


def paginate_url(base_url: str, page: int) -> str:
    if page == 1:
        return base_url
    parsed = urlparse(base_url)
    path = parsed.path.rstrip("/")
    path = re.sub(r"/pg-\d+$", "", path)
    return parsed._replace(path=f"{path}/pg-{page}").geturl()


def make_context(p, headless: bool):
    PROFILE_DIR.mkdir(exist_ok=True)
    return p.chromium.launch_persistent_context(
        user_data_dir=str(PROFILE_DIR),
        headless=headless,
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/Chicago",
        args=[
            "--disable-blink-features=AutomationControlled",
        ],
    )


def scrape(search_url: str, pages: int, enrich: bool, headless: bool) -> list[Listing]:
    all_listings: list[Listing] = []
    seen: set[str] = set()

    with sync_playwright() as p:
        ctx = make_context(p, headless=headless)
        page = ctx.new_page()
        # Strip the obvious "navigator.webdriver" tell
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )

        for pg in range(1, pages + 1):
            url = paginate_url(search_url, pg)
            print(f"\n[page {pg}]")
            html = fetch_html(page, url)
            if not html:
                print("  empty page — stopping")
                break
            data = extract_next_data(html)
            if not data:
                print("  no __NEXT_DATA__ found, saving debug_last_page.html")
                Path(ROOT / "debug_last_page.html").write_text(html[:300_000])
                break
            results = parse_search_results(data)
            print(f"  {len(results)} results")
            if not results:
                break
            for r in results:
                listing = parse_listing_summary(r)
                if not listing.listing_id or listing.listing_id in seen:
                    continue
                seen.add(listing.listing_id)
                all_listings.append(listing)
            polite_sleep()

        if enrich:
            print(f"\nEnriching {len(all_listings)} listings with agent contact...")
            for i, l in enumerate(all_listings, 1):
                print(f"  [{i}/{len(all_listings)}] {l.address or l.listing_id}")
                enrich_from_detail(l, page)
                polite_sleep()

        ctx.close()

    return all_listings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", help="realtor.com search URL with your filters baked in")
    ap.add_argument("--pages", type=int, default=1, help="how many result pages to walk")
    ap.add_argument("--no-enrich", action="store_true", help="skip per-listing detail fetch")
    ap.add_argument("--headless", action="store_true",
                    help="hide the browser (only works once cookies are warmed up)")
    ap.add_argument("--out", default="listings.json", help="output JSON path")
    args = ap.parse_args()

    listings = scrape(args.url, pages=args.pages, enrich=not args.no_enrich,
                      headless=args.headless)
    out_path = Path(args.out)
    out_path.write_text(json.dumps([asdict(l) for l in listings], indent=2))

    with_agent = sum(1 for l in listings if l.agent_phone or l.agent_email)
    print(f"\nSaved {len(listings)} listings -> {out_path}")
    print(f"  with agent contact: {with_agent}")
    print(f"  total photos: {sum(l.photo_count for l in listings)}")


if __name__ == "__main__":
    main()
