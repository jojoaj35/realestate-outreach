"""Scrape Zillow listings + listing-agent contact with Scrapling.

This ports the standalone ``~/Desktop/zillow-scraper`` technique into the outreach
workflow as the sole listing source feeding ``scan.py`` and the web dashboard.

Zillow runs aggressive anti-bot detection (PerimeterX + reCAPTCHA), so — like the
standalone scraper — we drive Scrapling's stealth Playwright fetcher
(``StealthySession``) with humanized interactions instead of plain HTTP. Listings
are pulled out of Zillow's embedded ``__NEXT_DATA__`` JSON blob (far more reliable
than DOM scraping, which lazy-loads cards), with an inline-JSON / DOM fallback for
older layouts. The listing-agent name + phone never appear on the search cards, so
Phase 2 opens each kept listing's detail page and walks the ``gdpClientCache`` graph
for the agent attribution — exactly the approach the standalone scraper uses.

Two-phase, mirroring ``scan.py``:

  Phase 1  :func:`discover_listings`  — one search-page fetch yields the
            ``listResults`` cards, each already carrying a hero photo + price +
            address + beds/baths. No per-listing fetch needed, so hero CLIP
            scoring stays cheap.

  Phase 2  :func:`enrich_agents`      — for the kept listings only, open each
            detail page to read the listing-agent name/phone/brokerage and the
            full photo gallery.

Usage:
    python src/scrape_zillow.py --city Austin --state TX --max 25 --out listings.json
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
from urllib.parse import quote, urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape_realtor import Listing

# Scrapling is imported lazily inside fetch helpers so importing this module
# (e.g. for the pure parsers / unit tests) never requires the browser deps.

ZILLOW_BASE = "https://www.zillow.com"

# Agent-attribution keys Zillow uses on detail-page JSON.
AGENT_KEYS = {"agentName", "agentPhoneNumber", "brokerName", "brokerPhoneNumber"}

# __NEXT_DATA__ / inline JSON script blobs (extracted with stdlib regex so the
# pure parsers stay dependency-free — only the network layer needs Scrapling).
_NEXT_DATA_RE = re.compile(
    r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.S)

# Full-resolution gallery photos live on photos.zillowstatic.com.
_PHOTO_RE = re.compile(
    r"https://photos\.zillowstatic\.com/fp/[A-Za-z0-9]+-[a-z_]+\d+\.(?:jpg|jpeg|webp|png)",
    re.I,
)
_PHOTO_HASH_RE = re.compile(r"/fp/([A-Za-z0-9]+)-")
_TOLLFREE_PREFIXES = {"800", "888", "877", "866", "855", "844", "833"}


# ── small helpers (no network — unit-tested) ─────────────────────────────────
def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _safe_get(d: Any, *keys, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _agent_name_tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^A-Za-z]+", name or "") if len(t) > 1]


def valid_listing_agent(agent_name: str) -> bool:
    """A real person's name (>= 2 word tokens), not a placeholder like 'Zillow'."""
    name = (agent_name or "").strip()
    if not name or name.lower() in {"zillow", "listing agent", "premier agent"}:
        return False
    return len(_agent_name_tokens(name)) >= 2


def _norm_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10 or digits[:3] in _TOLLFREE_PREFIXES:
        return ""
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def build_search_url(city: str, state: str = "TX") -> str:
    """Build a Zillow city search URL, e.g. .../homes/Austin,-TX_rb/."""
    city_slug = (city or "").strip().replace(" ", "-")
    return f"{ZILLOW_BASE}/homes/{quote(city_slug)},-{state.strip()}_rb/"


# ── JSON / DOM parsing from already-fetched HTML ─────────────────────────────
def _extract_next_data(html: str) -> dict | None:
    """Pull and parse the ``<script id="__NEXT_DATA__">`` JSON blob."""
    m = _NEXT_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None


def parse_search_results(html: str) -> list[dict]:
    """Return the raw Zillow ``listResults`` entries from a search page.

    Zillow embeds search results in ``__NEXT_DATA__``; we fall back to any inline
    ``application/json`` script (older layouts) and finally to nothing.
    """
    data = _extract_next_data(html)
    results: list[dict] = []
    if data is not None:
        results = _safe_get(
            data, "props", "pageProps", "searchPageState", "cat1",
            "searchResults", "listResults", default=None,
        ) or _safe_get(
            data, "props", "pageProps", "componentProps", "searchPageState",
            "cat1", "searchResults", "listResults", default=None,
        ) or []
    if not results:
        results = _parse_inline_json(html)
    return [r for r in results if isinstance(r, dict)]


def _parse_inline_json(html: str) -> list[dict]:
    """Some Zillow layouts stash listings in a hidden application/json script."""
    for raw in _JSON_SCRIPT_RE.findall(html or ""):
        if "listResults" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        stack: list[Any] = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if isinstance(node.get("listResults"), list):
                    return node["listResults"]
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return []


def listing_from_raw(raw: dict) -> Listing | None:
    """Normalize one Zillow ``listResults`` entry into a :class:`Listing`."""
    hdp = raw.get("hdpData") or {}
    home = hdp.get("homeInfo") or {}

    detail_url = raw.get("detailUrl") or ""
    if detail_url and not detail_url.startswith("http"):
        detail_url = urljoin(ZILLOW_BASE, detail_url)

    zpid = raw.get("zpid") or home.get("zpid")
    if not detail_url and not zpid:
        return None
    if not detail_url and zpid:
        detail_url = f"{ZILLOW_BASE}/homedetails/{zpid}_zpid/"

    # Hero photo: prefer the carousel's first shot, fall back to imgSrc.
    hero = ""
    carousel = raw.get("carouselPhotos") or []
    if carousel and isinstance(carousel[0], dict):
        hero = carousel[0].get("url") or ""
    hero = hero or raw.get("imgSrc") or ""
    photo_urls = [hero] if hero.startswith("http") else []

    price = raw.get("unformattedPrice") or home.get("price")
    list_price = int(price) if isinstance(price, (int, float)) else None

    beds = raw.get("beds") if raw.get("beds") is not None else home.get("bedrooms")
    baths = raw.get("baths") if raw.get("baths") is not None else home.get("bathrooms")
    sqft = raw.get("area") or home.get("livingArea")

    listing = Listing(
        listing_id=str(zpid or ""),
        url=detail_url,
        address=_clean(raw.get("addressStreet") or home.get("streetAddress")
                       or raw.get("address")),
        city=_clean(raw.get("addressCity") or home.get("city")),
        state=_clean(raw.get("addressState") or home.get("state")),
        zip_code=_clean(raw.get("addressZipcode") or home.get("zipcode")),
        list_price=list_price,
        beds=float(beds) if isinstance(beds, (int, float)) else None,
        baths=float(baths) if isinstance(baths, (int, float)) else None,
        sqft=int(sqft) if isinstance(sqft, (int, float)) else None,
        photo_urls=photo_urls,
        photo_count=len(photo_urls),
        broker_name=_clean(raw.get("brokerName")),
        raw_source="zillow",
    )
    return listing


def parse_search_cards(html: str) -> list[Listing]:
    """Parse a Zillow search page into :class:`Listing` objects (hero photo only)."""
    out: list[Listing] = []
    seen: set[str] = set()
    for raw in parse_search_results(html):
        listing = listing_from_raw(raw)
        if listing is None:
            continue
        key = listing.listing_id or listing.url
        if key in seen:
            continue
        seen.add(key)
        out.append(listing)
    return out


def _walk_for_attribution(node: Any) -> dict | None:
    """DFS the JSON graph for a dict carrying agent/broker attribution keys."""
    if isinstance(node, dict):
        if AGENT_KEYS & set(node.keys()):
            return node
        for v in node.values():
            found = _walk_for_attribution(v)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _walk_for_attribution(item)
            if found:
                return found
    return None


def parse_detail(html: str, listing: Listing | None = None) -> Listing:
    """Parse listing-agent contact + full photo gallery off a detail page.

    Zillow keeps the listing graph in ``__NEXT_DATA__`` under
    ``props.pageProps.componentProps.gdpClientCache`` (itself a stringified JSON
    blob), so we re-parse that and walk it for the agent attribution.
    """
    if listing is None:
        listing = Listing(listing_id="", url="", raw_source="zillow")

    data = _extract_next_data(html)
    if data is not None:
        cache = _safe_get(data, "props", "pageProps", "componentProps", "gdpClientCache")
        graph: Any = data
        if isinstance(cache, str):
            try:
                graph = json.loads(cache)
            except json.JSONDecodeError:
                graph = data
        attr = _walk_for_attribution(graph) or {}

        name = _clean(attr.get("agentName"))
        if valid_listing_agent(name):
            listing.agent_name = name
        phone = _norm_phone(attr.get("agentPhoneNumber") or "")
        if not phone:
            phone = _norm_phone(attr.get("brokerPhoneNumber") or "")
        if phone:
            listing.agent_phone = phone
        broker = _clean(attr.get("brokerName"))
        if broker:
            listing.broker_name = broker

    # Full gallery: dedup by photo hash (Zillow serves several sizes per photo).
    gallery: list[str] = []
    seen: set[str] = set()
    for u in _PHOTO_RE.findall(html):
        m = _PHOTO_HASH_RE.search(u)
        key = m.group(1) if m else u
        if key in seen:
            continue
        seen.add(key)
        gallery.append(u)
    if gallery:
        listing.photo_urls = gallery
        listing.photo_count = len(gallery)
    return listing


# ── network layer (Scrapling StealthySession) ────────────────────────────────
def _new_session():
    """Stealth Playwright session tuned for Zillow's PerimeterX bot wall."""
    from scrapling.fetchers import StealthySession
    return StealthySession(
        headless=True,
        humanize=True,
        block_images=True,
        block_webrtc=True,
        solve_cloudflare=False,
    )


def _fetch(session, url: str, *, wait: int = 2000, attempts: int = 2,
           min_len: int = 5000):
    """Fetch a URL through a stealth session, retrying transient/blocked pages."""
    last = None
    for attempt in range(attempts):
        try:
            page = session.fetch(url, network_idle=True, google_search=False, wait=wait)
        except Exception as e:  # noqa: BLE001 — browser errors vary; retry then surface
            last = e
            time.sleep(1.5 * (attempt + 1))
            continue
        if getattr(page, "status", 0) == 200 and len(page.html_content) >= min_len:
            return page
        last = RuntimeError(f"status {getattr(page, 'status', '?')} for {url}")
        time.sleep(1.5 * (attempt + 1))
    if last:
        print(f"  fetch failed: {last}", flush=True)
    return None


def discover_listings_from_url(url: str, max_urls: int = 40,
                               session=None) -> list[Listing]:
    """Phase 1 from a pasted Zillow link.

    Accepts either a Zillow *search* URL (``/homes/...`` — yields the result
    cards) or a single *detail* URL (``/homedetails/...`` — yields one listing).
    """
    url = (url or "").strip()
    if not url:
        return []
    own = session is None
    session = session or _new_session()
    try:
        if own:
            session.__enter__()
        page = _fetch(session, url)
        if not page:
            print(f"  could not fetch {url}", flush=True)
            return []
        if "/homedetails/" in url:
            base = Listing(listing_id="", url=url.split("?")[0], raw_source="zillow")
            listing = parse_detail(page.html_content, base)
            return [listing]
        cards = parse_search_cards(page.html_content)
        print(f"  link yielded {len(cards)} listings", flush=True)
        return cards[:max_urls]
    finally:
        if own:
            session.__exit__(None, None, None)


def discover_listings(city: str, state: str = "TX", max_urls: int = 40,
                      session=None) -> list[Listing]:
    """Phase 1: harvest search-card listings (hero photo + price + address)."""
    own = session is None
    session = session or _new_session()
    try:
        if own:
            session.__enter__()
        out: list[Listing] = []
        seen: set[str] = set()
        pages_needed = max(1, (max_urls + 39) // 40)
        for page_no in range(1, pages_needed + 1):
            url = build_search_url(city, state)
            if page_no > 1:
                url = f"{url}{page_no}_p/"
            page = _fetch(session, url)
            if not page:
                break
            cards = parse_search_cards(page.html_content)
            new = [c for c in cards if (c.listing_id or c.url) not in seen]
            for c in new:
                seen.add(c.listing_id or c.url)
            out.extend(new)
            print(f"  page {page_no}: +{len(new)} listings (total {len(out)})", flush=True)
            if len(out) >= max_urls or not new:
                break
            time.sleep(0.8)
        return out[:max_urls]
    finally:
        if own:
            session.__exit__(None, None, None)


def enrich_agents(listings: list[dict], city: str = "", state: str = "TX",
                  session=None) -> list[dict]:
    """Phase 2: open each kept listing's detail page for agent + full gallery.

    Mutates each dict in place (``agent_name``/``agent_phone``/``broker_name`` and
    the full ``photo_urls``/``photo_count``) and returns the same list.
    """
    own = session is None
    session = session or _new_session()
    try:
        if own:
            session.__enter__()
        for i, d in enumerate(listings, 1):
            url = d.get("url") or ""
            if not url:
                continue
            page = _fetch(session, url, wait=1500)
            if not page:
                print(f"  [{i}/{len(listings)}] detail fetch failed: {url}", flush=True)
                continue
            base = Listing(listing_id=str(d.get("listing_id") or ""), url=url,
                          raw_source="zillow")
            parsed = parse_detail(page.html_content, base)
            d["agent_name"] = parsed.agent_name
            if parsed.agent_phone:
                d["agent_phone"] = parsed.agent_phone
            if parsed.broker_name:
                d["broker_name"] = parsed.broker_name
            if parsed.photo_urls:
                d["photo_urls"] = parsed.photo_urls
                d["photo_count"] = parsed.photo_count
            time.sleep(0.6)
        return listings
    finally:
        if own:
            session.__exit__(None, None, None)


def scrape(city: str = "Austin", state: str = "TX", max_results: int = 25,
           enrich: bool = True) -> list[Listing]:
    """Standalone convenience: discover + (optionally) enrich agent contact."""
    with _new_session() as session:
        listings = discover_listings(city, state, max_urls=max_results, session=session)
        if enrich and listings:
            dicts = [asdict(l) for l in listings]
            enrich_agents(dicts, city, state, session=session)
            listings = [Listing(**{k: v for k, v in d.items()
                                   if k in Listing.__dataclass_fields__}) for d in dicts]
    return listings


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Zillow listings via Scrapling.")
    ap.add_argument("--city", default="Austin")
    ap.add_argument("--state", default="TX")
    ap.add_argument("--max", type=int, default=25)
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip opening detail pages for agent contact")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    listings = scrape(args.city, args.state, args.max, enrich=not args.no_enrich)
    rows = [asdict(l) for l in listings]
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))
        print(f"wrote {len(rows)} listings -> {args.out}")
    else:
        print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
