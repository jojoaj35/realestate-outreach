"""Free/local retrieval sources for IG realtor discovery.

Rule-based query and hashtag expansion from ``data/city_discovery.json``,
plus optional Google ``site:instagram.com`` handle extraction via Playwright.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path

from playwright.sync_api import Page

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import DATA_DIR, settings
from ig_browser import polite_sleep
from ig_store import normalize_handle

CITY_CONFIG_PATH = DATA_DIR / "city_discovery.json"
_HANDLE_RE = re.compile(r"instagram\.com/([A-Za-z0-9._]+)", re.I)
_SKIP_HANDLES = {"p", "reel", "stories", "explore", "accounts", "direct", "tv", "about"}


def _city_slug(city: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", city.lower())


def _keyword_list(raw: str) -> list[str]:
    return [k.strip().lower() for k in raw.split(",") if k.strip()]


def load_city_config(city: str) -> dict:
    """Return discovery config for ``city``; fall back to generic slug-based tags."""
    city = (city or "").strip()
    data: dict = {}
    if CITY_CONFIG_PATH.exists():
        try:
            data = json.loads(CITY_CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}

    if city in data:
        cfg = dict(data[city])
    else:
        # Case-insensitive lookup
        cfg = None
        city_lower = city.lower()
        for key, val in data.items():
            if key.lower() == city_lower:
                cfg = dict(val)
                break
        if cfg is None:
            slug = _city_slug(city)
            cfg = {
                "state": "",
                "metro_aliases": [city.lower(), slug] if slug else [city.lower()],
                "neighborhoods": [],
                "hashtags": [
                    f"{slug}realtor",
                    f"{slug}realestate",
                    f"{slug}homes",
                ] if slug else [],
                "brokerages": [],
            }

    cfg.setdefault("state", "")
    cfg.setdefault("metro_aliases", [city.lower()])
    cfg.setdefault("neighborhoods", [])
    cfg.setdefault("hashtags", [])
    cfg.setdefault("brokerages", [])
    return cfg


def discovery_hashtags(city: str) -> list[str]:
    """Configured + city-config hashtags, deduped."""
    cfg = load_city_config(city)
    configured = _keyword_list(settings.ig_discovery_hashtags)
    slug = _city_slug(city)
    auto = [f"{slug}realtor", f"{slug}realestate", f"{slug}homes"] if slug else []
    seen: set[str] = set()
    out: list[str] = []
    for t in configured + cfg.get("hashtags", []) + auto:
        t = t.lstrip("#").lower()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def search_queries(city: str) -> list[str]:
    """Generate 15–30 IG search strings from city config."""
    cfg = load_city_config(city)
    state = (cfg.get("state") or "").strip()
    metro = cfg.get("metro_aliases") or [city.lower()]
    neighborhoods = cfg.get("neighborhoods") or []
    brokerages = cfg.get("brokerages") or []

    queries: list[str] = [
        f"realtor {city}",
        f"{city} real estate agent",
        f"{city} realtor",
        f"real estate {city}",
        f"homes {city}",
        f"listing agent {city}",
    ]
    if state:
        queries.extend([
            f"realtor {city} {state}",
            f"real estate agent {city} {state}",
        ])

    for alias in metro[:4]:
        if alias.lower() != city.lower():
            queries.extend([
                f"realtor {alias}",
                f"{alias} real estate agent",
                f"{alias} homes agent",
            ])

    for hood in neighborhoods[:8]:
        queries.extend([
            f"{hood} realtor",
            f"{hood} real estate agent",
        ])

    for brokerage in brokerages[:6]:
        queries.extend([
            f"{brokerage} {city}",
            f"{brokerage} realtor {city}",
        ])

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        q = " ".join(q.split())
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)

    # Optional GPT query expansion when OPENAI_API_KEY is set.
    try:
        from ig_llm import expand_search_queries, llm_available
        if llm_available():
            for q in expand_search_queries(city, out):
                key = q.lower()
                if key not in seen:
                    seen.add(key)
                    out.append(q)
    except ImportError:
        pass

    return out[:35]


def google_search_queries(city: str) -> list[str]:
    """Google ``site:instagram.com`` queries for handle extraction."""
    cfg = load_city_config(city)
    state = (cfg.get("state") or "TX").strip()
    queries = [
        f'site:instagram.com realtor "{city}"',
        f'site:instagram.com "real estate agent" "{city}" {state}',
        f'site:instagram.com "{city}" realtor homes',
    ]
    for alias in (cfg.get("metro_aliases") or [])[:2]:
        if alias.lower() != city.lower():
            queries.append(f'site:instagram.com realtor "{alias}"')
    return queries


def _extract_handles_from_html(html: str) -> list[str]:
    handles: set[str] = set()
    for m in _HANDLE_RE.finditer(html):
        h = normalize_handle(m.group(1))
        if h and h not in _SKIP_HANDLES:
            handles.add(h)
    return sorted(handles)


def google_instagram_handles(page: Page, city: str, max_per_query: int = 15) -> list[str]:
    """Fallback retrieval: parse Google result links for Instagram handles."""
    if not settings.ig_google_search_enabled:
        return []

    handles: set[str] = set()
    for query in google_search_queries(city):
        url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            polite_sleep(2, 4)
        except Exception:
            continue

        found: set[str] = set()
        try:
            links = page.evaluate("""
              () => [...document.querySelectorAll('a[href]')]
                .map(a => a.href)
                .filter(Boolean)
            """) or []
            for link in links:
                for h in _extract_handles_from_html(link):
                    found.add(h)
        except Exception:
            pass

        try:
            body_html = page.content()
            for h in _extract_handles_from_html(body_html):
                found.add(h)
        except Exception:
            pass

        for h in sorted(found)[:max_per_query]:
            handles.add(h)
        polite_sleep(2, 3)

    return sorted(handles)
