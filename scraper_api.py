"""Mac-side scraper API — lets the Render-hosted dashboard use Scrapling.

Zillow blocks scraping from datacenter IPs (like Render's), so the stealth
browser has to run here on the Mac. This small FastAPI service wraps
``scrape_zillow`` and exposes it as job-based HTTP endpoints that the Render
app calls (see ``src/scrape_remote.py``).

Run it on the Mac:
    bash scripts/run_scraper_api.sh          # starts on http://127.0.0.1:8765

Then expose it with ngrok so Render can reach it:
    ngrok http --domain=<your-static-domain> 8765

Auth: every request must carry the shared secret in the ``X-Api-Key`` header.
The key is read from ``SCRAPER_API_KEY`` in .env and must match the same env
var on Render. Jobs run one at a time (a single stealth browser session).
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402

import scrape_zillow  # noqa: E402

API_KEY = os.getenv("SCRAPER_API_KEY", "")

app = FastAPI(title="Zillow scraper API (Scrapling on the Mac)")

JOBS: dict[str, dict] = {}
# One stealth browser at a time; concurrent jobs queue behind this lock.
_BROWSER_LOCK = threading.Lock()


def _check_key(x_api_key: str | None) -> None:
    if not API_KEY:
        raise HTTPException(500, "SCRAPER_API_KEY is not set in .env on the Mac")
    if x_api_key != API_KEY:
        raise HTTPException(401, "bad or missing X-Api-Key header")


class DiscoverReq(BaseModel):
    city: str = "Austin"
    state: str = "TX"
    url: str | None = None  # optional Zillow search/detail link (overrides city)
    max_urls: int = 40


class EnrichReq(BaseModel):
    listings: list[dict]
    city: str = ""
    state: str = "TX"


def _start_job(fn) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"status": "queued"}

    def run():
        with _BROWSER_LOCK:
            JOBS[job_id] = {"status": "running"}
            try:
                JOBS[job_id] = {"status": "done", "result": fn()}
            except Exception as e:  # noqa: BLE001 — report any scraper failure
                JOBS[job_id] = {"status": "error", "error": f"{type(e).__name__}: {e}",
                                "trace": traceback.format_exc()[-2000:]}

    threading.Thread(target=run, daemon=True).start()
    return job_id


@app.get("/health")
def health():
    return {"ok": True, "service": "scraper-api"}


@app.post("/jobs/discover")
def discover(req: DiscoverReq, x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)

    def work():
        if req.url:
            listings = scrape_zillow.discover_listings_from_url(req.url, max_urls=req.max_urls)
        else:
            listings = scrape_zillow.discover_listings(req.city, req.state, max_urls=req.max_urls)
        return [asdict(l) for l in listings]

    return {"job_id": _start_job(work)}


@app.post("/jobs/enrich")
def enrich(req: EnrichReq, x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)

    def work():
        return scrape_zillow.enrich_agents(req.listings, req.city, req.state)

    return {"job_id": _start_job(work)}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, x_api_key: str | None = Header(default=None)):
    _check_key(x_api_key)
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job
