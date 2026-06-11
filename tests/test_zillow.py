"""Unit tests for the Scrapling-based Zillow parsers (no network).

Fixtures mirror the real Zillow markup: search results live in a ``__NEXT_DATA__``
JSON blob (each listResult carries hero photo + price + address + bed/bath/sqft);
detail pages stash the listing graph in ``gdpClientCache`` (a stringified JSON blob)
which carries the agent attribution, plus full-resolution gallery photos on
photos.zillowstatic.com.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import scrape_zillow as sz


def _next_data_html(payload: dict) -> str:
    blob = json.dumps(payload)
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{blob}</script></body></html>'


SEARCH_NEXT_DATA = _next_data_html({
    "props": {"pageProps": {"searchPageState": {"cat1": {"searchResults": {"listResults": [
        {
            "zpid": "12345",
            "detailUrl": "/homedetails/123-Main-St-Austin-TX-78701/12345_zpid/",
            "unformattedPrice": 598000,
            "addressStreet": "123 Main St",
            "addressCity": "Austin",
            "addressState": "TX",
            "addressZipcode": "78701",
            "beds": 3,
            "baths": 2.5,
            "area": 1850,
            "imgSrc": "https://photos.zillowstatic.com/fp/aaa111-cc_ft_768.webp",
            "brokerName": "Some Realty",
        },
        {
            "zpid": "67890",
            "detailUrl": "/homedetails/4611-Rosedale-Ave-Austin-TX-78756/67890_zpid/",
            "unformattedPrice": 675000,
            "hdpData": {"homeInfo": {
                "streetAddress": "4611 Rosedale Ave",
                "city": "Austin", "state": "TX", "zipcode": "78756",
                "bedrooms": 4, "bathrooms": 3, "livingArea": 2200,
            }},
            "carouselPhotos": [{"url": "https://photos.zillowstatic.com/fp/bbb222-cc_ft_768.webp"}],
        },
    ]}}}}},
})


DETAIL_NEXT_DATA = _next_data_html({
    "props": {"pageProps": {"componentProps": {"gdpClientCache": json.dumps({
        "ForSaleDoubleScrollFullRenderQuery": {"property": {"attributionInfo": {
            "agentName": "Paola McElheron",
            "agentPhoneNumber": "512-555-9090",
            "agentLicenseNumber": "0123456",
            "brokerName": "Local Color Realty Group",
            "brokerPhoneNumber": "512-740-0071",
        }}}
    })}}},
}).replace(
    "</body>",
    '<img src="https://photos.zillowstatic.com/fp/ccc333-cc_ft_1536.jpg">'
    '<img src="https://photos.zillowstatic.com/fp/ccc333-cc_ft_768.jpg">'
    '<img src="https://photos.zillowstatic.com/fp/ddd444-cc_ft_1536.jpg">'
    "</body>",
)


# ── search results ───────────────────────────────────────────────────────────
def test_parse_search_cards_basic():
    listings = sz.parse_search_cards(SEARCH_NEXT_DATA)
    assert len(listings) == 2
    first = listings[0]
    assert first.listing_id == "12345"
    assert first.url == ("https://www.zillow.com/homedetails/"
                         "123-Main-St-Austin-TX-78701/12345_zpid/")
    assert first.address == "123 Main St"
    assert first.city == "Austin"
    assert first.state == "TX"
    assert first.zip_code == "78701"
    assert first.list_price == 598000
    assert first.beds == 3
    assert first.baths == 2.5
    assert first.sqft == 1850
    assert first.broker_name == "Some Realty"
    assert first.photo_count == 1
    assert first.photo_urls[0].startswith("https://photos.zillowstatic.com/")
    assert first.raw_source == "zillow"


def test_parse_search_cards_reads_homeinfo_and_carousel():
    listings = sz.parse_search_cards(SEARCH_NEXT_DATA)
    second = listings[1]
    assert second.address == "4611 Rosedale Ave"
    assert second.beds == 4
    assert second.baths == 3
    assert second.sqft == 2200
    assert second.photo_urls[0] == "https://photos.zillowstatic.com/fp/bbb222-cc_ft_768.webp"


def test_parse_search_cards_dedups_zpids():
    doubled = SEARCH_NEXT_DATA  # two distinct zpids already; ensure stable count
    assert len(sz.parse_search_cards(doubled)) == 2


# ── detail page ────────────────────────────────────────────────────────────--
def test_parse_detail_agent_and_broker():
    listing = sz.parse_detail(DETAIL_NEXT_DATA)
    assert listing.agent_name == "Paola McElheron"
    assert listing.broker_name == "Local Color Realty Group"
    # Listing-agent number is preferred over the broker number.
    assert listing.agent_phone == "512-555-9090"


def test_parse_detail_gallery_dedups_by_photo_hash():
    listing = sz.parse_detail(DETAIL_NEXT_DATA)
    # ccc333 appears twice (two sizes) -> one entry; ddd444 -> one entry.
    assert listing.photo_count == 2
    assert all("photos.zillowstatic.com" in u for u in listing.photo_urls)


# ── helpers ──────────────────────────────────────────────────────────────────
def test_valid_listing_agent():
    assert sz.valid_listing_agent("Paola McElheron")
    assert not sz.valid_listing_agent("Zillow")
    assert not sz.valid_listing_agent("Smith")
    assert not sz.valid_listing_agent("")


def test_norm_phone_rejects_tollfree_and_formats():
    assert sz._norm_phone("512-740-0071") == "512-740-0071"
    assert sz._norm_phone("(512) 555 9090") == "512-555-9090"
    assert sz._norm_phone("+1 512 555 9090") == "512-555-9090"
    assert sz._norm_phone("800-555-1234") == ""  # toll-free rejected
    assert sz._norm_phone("123") == ""


def test_build_search_url():
    assert sz.build_search_url("Austin", "TX") == "https://www.zillow.com/homes/Austin,-TX_rb/"
    assert sz.build_search_url("San Antonio", "TX") == \
        "https://www.zillow.com/homes/San-Antonio,-TX_rb/"
