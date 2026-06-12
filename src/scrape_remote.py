"""Client for the Mac-side scraper API (``scraper_api.py``).

When the app runs somewhere Zillow blocks (e.g. Render), set:
    SCRAPER_API_URL  — the public ngrok URL of the Mac scraper API
    SCRAPER_API_KEY  — shared secret, must match the Mac's .env

``scan.py`` prefers this backend whenever SCRAPER_API_URL is set, so the
stealth-browser scraping still happens on the Mac while everything else
(scoring, queue, dashboard) runs on the server.

Jobs are polled rather than held open so long scrapes survive tunnels and
proxies with short request timeouts.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import fields as dc_fields
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scrape_realtor import Listing  # noqa: E402

POLL_SECONDS = 4.0


def _base_url() -> str:
    return (os.getenv("SCRAPER_API_URL") or "").strip().rstrip("/")


def available() -> bool:
    return bool(_base_url())


def _headers() -> dict:
    return {"X-Api-Key": os.getenv("SCRAPER_API_KEY", "")}


def _run_job(path: str, payload: dict, timeout: float = 1500.0):
    """Create a job on the Mac scraper API and poll until it finishes."""
    base = _base_url()
    resp = requests.post(f"{base}{path}", json=payload, headers=_headers(), timeout=60)
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = requests.get(f"{base}/jobs/{job_id}", headers=_headers(), timeout=60).json()
        state = status.get("status")
        if state == "done":
            return status["result"]
        if state == "error":
            raise RuntimeError(f"scraper API job failed: {status.get('error')}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"scraper API job {job_id} did not finish within {timeout:.0f}s")


def _to_listing(d: dict) -> Listing:
    keys = {f.name for f in dc_fields(Listing)}
    return Listing(**{k: v for k, v in d.items() if k in keys})


def discover_listings(city: str, state: str = "TX", max_urls: int = 40) -> list[Listing]:
    result = _run_job("/jobs/discover",
                      {"city": city, "state": state, "max_urls": max_urls})
    return [_to_listing(d) for d in result]


def discover_listings_from_url(url: str, max_urls: int = 40) -> list[Listing]:
    result = _run_job("/jobs/discover", {"url": url, "max_urls": max_urls})
    return [_to_listing(d) for d in result]


def enrich_agents(listings: list[dict], city: str = "", state: str = "TX") -> list[dict]:
    """Same contract as ``scrape_zillow.enrich_agents`` — mutates in place."""
    result = _run_job("/jobs/enrich",
                      {"listings": listings, "city": city, "state": state})
    for d, enriched in zip(listings, result):
        d.update(enriched)
    return listings
