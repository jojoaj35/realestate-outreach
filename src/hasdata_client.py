"""Wrapper around HasData's Zillow Listing + Property APIs.

HasData (formerly Scrape-It.Cloud) runs the live Zillow scrape on their own
infrastructure — rotating residential proxies, PerimeterX/Imperva/CAPTCHA
bypass — and returns structured JSON. That's why this works from a datacenter
(Render) where running our own Playwright against Zillow would be blocked.

Get a key (1,000 free credits, no card) at https://hasdata.com — put it in
``.env`` as ``HASDATA_API_KEY``.
"""
from __future__ import annotations

import requests

from config import settings

BASE_URL = "https://api.hasdata.com"
LISTING_URL = f"{BASE_URL}/scrape/zillow/listing"
PROPERTY_URL = f"{BASE_URL}/scrape/zillow/property"


class HasDataError(RuntimeError):
    pass


def hasdata_available() -> bool:
    return bool((settings.hasdata_api_key or "").strip())


def _key() -> str:
    key = (settings.hasdata_api_key or "").strip()
    if not key:
        raise HasDataError("HASDATA_API_KEY is not set in .env (get one at https://hasdata.com)")
    return key


def _check(result: dict, status_code: int) -> None:
    if isinstance(result, dict) and result.get("status") == "error" and result.get("message"):
        raise HasDataError(str(result["message"]))
    if status_code == 401:
        raise HasDataError("Invalid HasData API key")
    if status_code == 403:
        raise HasDataError("Not enough HasData API credits for this request")
    if status_code == 429:
        raise HasDataError("HasData concurrency/rate limit reached")
    if isinstance(result, dict) and result.get("errors"):
        raise HasDataError(f"HasData validation error: {result['errors']}")
    if status_code >= 400:
        raise HasDataError(f"HasData request failed ({status_code})")


def search(params: dict, timeout: float = 90.0) -> dict:
    """Zillow Listing API. ``params`` must include ``keyword`` and ``type``."""
    headers = {"x-api-key": _key(), "Content-Type": "application/json"}
    resp = requests.get(LISTING_URL, headers=headers, params=params, timeout=timeout)
    try:
        result = resp.json()
    except ValueError:
        raise HasDataError(f"Non-JSON response ({resp.status_code}): {resp.text[:200]}")
    _check(result, resp.status_code)
    return result


def property_detail(url: str, extract_emails: bool = False, timeout: float = 90.0) -> dict:
    """Zillow Property API — full detail (agent contact, photos) for one listing URL."""
    headers = {"x-api-key": _key(), "Content-Type": "application/json"}
    params: dict = {"url": url}
    if extract_emails:
        params["extractAgentEmails"] = "true"
    resp = requests.get(PROPERTY_URL, headers=headers, params=params, timeout=timeout)
    try:
        result = resp.json()
    except ValueError:
        raise HasDataError(f"Non-JSON response ({resp.status_code}): {resp.text[:200]}")
    _check(result, resp.status_code)
    return result
