"""Thin wrapper around Tavily Search + Extract APIs."""
from __future__ import annotations

import requests

from config import settings

SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"


class TavilyError(RuntimeError):
    pass


def tavily_available() -> bool:
    return bool((settings.tavily_api_key or "").strip())


def _key() -> str:
    key = (settings.tavily_api_key or "").strip()
    if not key:
        raise TavilyError("TAVILY_API_KEY is not set in .env")
    return key


def search(
    query: str,
    *,
    max_results: int = 10,
    include_domains: list[str] | None = None,
    search_depth: str = "basic",
    topic: str = "general",
    timeout: float = 60.0,
) -> dict:
    body: dict = {
        "api_key": _key(),
        "query": query,
        "max_results": max_results,
        "search_depth": search_depth,
        "topic": topic,
    }
    if include_domains:
        body["include_domains"] = include_domains
    resp = requests.post(SEARCH_URL, json=body, timeout=timeout)
    if resp.status_code != 200:
        raise TavilyError(f"Tavily search failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def extract(
    urls: str | list[str],
    *,
    extract_depth: str = "advanced",
    include_images: bool = True,
    timeout: float = 120.0,
) -> dict:
    url_list = [urls] if isinstance(urls, str) else list(urls)
    if not url_list:
        return {"results": [], "failed_results": []}
    body = {
        "api_key": _key(),
        "urls": url_list[:20],
        "extract_depth": extract_depth,
        "include_images": include_images,
    }
    resp = requests.post(EXTRACT_URL, json=body, timeout=timeout)
    if resp.status_code != 200:
        raise TavilyError(f"Tavily extract failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()


def extract_with_retry(
    urls: str | list[str],
    *,
    extract_depth: str = "advanced",
    include_images: bool = True,
    attempts: int = 3,
    backoff: float = 2.0,
    timeout: float = 120.0,
) -> dict:
    """Extract with retry of any failed URLs (Redfin extraction is flaky).

    Re-requests only the URLs that failed on each attempt, merging successes.
    """
    import time

    url_list = [urls] if isinstance(urls, str) else list(urls)
    pending = list(dict.fromkeys(url_list))
    results: list[dict] = []
    last_failed: list = []

    for attempt in range(attempts):
        if not pending:
            break
        try:
            resp = extract(pending, extract_depth=extract_depth,
                           include_images=include_images, timeout=timeout)
        except TavilyError:
            if attempt == attempts - 1:
                raise
            time.sleep(backoff * (attempt + 1))
            continue

        got = resp.get("results") or []
        results.extend(got)
        done = {(r.get("url") or "").rstrip("/") for r in got}
        last_failed = resp.get("failed_results") or []
        pending = [u for u in pending if u.rstrip("/") not in done]
        if pending and attempt < attempts - 1:
            time.sleep(backoff * (attempt + 1))

    return {"results": results, "failed_results": last_failed}
