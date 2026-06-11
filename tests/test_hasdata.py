"""Unit tests for HasData (Zillow) response parsing into the Listing schema.

These use a realistic mock response shape so the mapping is verified without
spending API credits. If a live response differs, run
``python src/scrape_hasdata.py --debug-raw`` and adjust find_listings_array /
parse_listing_row accordingly.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import scrape_hasdata as hd

MOCK_SEARCH = {
    "requestMetadata": {"id": "abc", "status": "ok"},
    "listings": [
        {
            "zpid": "12345",
            "detailUrl": "/homedetails/123-Main-St-Austin-TX-78745/12345_zpid/",
            "statusType": "FOR_SALE",
            "price": "$425,000",
            "unformattedPrice": 425000,
            "addressStreet": "123 Main St",
            "addressCity": "Austin",
            "addressState": "TX",
            "addressZipcode": "78745",
            "beds": 3,
            "baths": 2,
            "area": 1850,
            "imgSrc": "https://photos.zillowstatic.com/fp/abc-cc_ft_768.jpg",
            "carouselPhotos": [
                {"url": "https://photos.zillowstatic.com/fp/one.jpg"},
                {"url": "https://photos.zillowstatic.com/fp/two.jpg"},
            ],
            "brokerName": "Acme Realty",
        },
        {
            "zpid": "67890",
            "detailUrl": "https://www.zillow.com/homedetails/9-Oak-Austin-TX-78704/67890_zpid/",
            "unformattedPrice": 599000,
            "address": "9 Oak Dr, Austin, TX 78704",
            "beds": 4,
            "baths": 3,
            "imgSrc": "https://photos.zillowstatic.com/fp/oak.jpg",
        },
    ],
}

MOCK_PROPERTY = {
    "requestMetadata": {"status": "ok"},
    "property": {
        "zpid": "12345",
        "address": {"streetAddress": "123 Main St", "city": "Austin", "state": "TX"},
        "listingAgent": {
            "agentName": "Jane Agent",
            "agentPhoneNumber": "(512) 555-0199",
            "agentEmail": "jane@acme.com",
        },
        "brokerName": "Acme Realty",
        "photos": [
            {"url": "https://photos.zillowstatic.com/fp/detail-1.jpg"},
            {"url": "https://photos.zillowstatic.com/fp/detail-2.jpg"},
            {"url": "https://photos.zillowstatic.com/fp/detail-3.jpg"},
        ],
    },
}


def test_find_listings_array_locates_rows():
    rows = hd.find_listings_array(MOCK_SEARCH)
    assert len(rows) == 2
    assert rows[0]["zpid"] == "12345"


def test_parse_listing_row_structured():
    rows = hd.find_listings_array(MOCK_SEARCH)
    listing = hd.parse_listing_row(rows[0])
    assert listing.listing_id == "12345"
    assert listing.url.startswith("https://www.zillow.com/homedetails/")
    assert listing.list_price == 425000
    assert listing.beds == 3
    assert listing.baths == 2
    assert listing.sqft == 1850
    assert listing.city == "Austin"
    assert listing.state == "TX"
    assert listing.zip_code == "78745"
    assert listing.broker_name == "Acme Realty"
    assert listing.photo_count >= 3  # hero + 2 carousel


def test_parse_listing_row_relative_url_made_absolute():
    rows = hd.find_listings_array(MOCK_SEARCH)
    listing = hd.parse_listing_row(rows[0])
    assert listing.url == "https://www.zillow.com/homedetails/123-Main-St-Austin-TX-78745/12345_zpid/"


def test_listing_id_falls_back_to_zpid_in_url():
    row = {"detailUrl": "https://www.zillow.com/homedetails/x/98765_zpid/"}
    listing = hd.parse_listing_row(row)
    assert listing.listing_id == "98765"


def test_money_to_int_handles_strings():
    assert hd._money_to_int("$1,200,000") == 1200000
    assert hd._money_to_int(599000) == 599000
    assert hd._money_to_int(None) is None


def test_enrich_fills_agent_contact(monkeypatch):
    rows = hd.find_listings_array(MOCK_SEARCH)
    listing = hd.parse_listing_row(rows[0])
    monkeypatch.setattr(hd, "property_detail", lambda url, extract_emails=False: MOCK_PROPERTY)
    hd.enrich_listing(listing)
    assert listing.agent_name == "Jane Agent"
    assert "555" in listing.agent_phone
    assert listing.agent_email == "jane@acme.com"
    assert listing.photo_count >= 3
