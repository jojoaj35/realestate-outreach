"""Download listing photo URLs to local files so the scorer can read them.

Realtor.com serves images from rdcpix.com, usually as small display variants
(e.g. ``...-w480_h360.webp``). We upgrade those to a large variant so the
quality heuristics see something close to the real photo, then cache the bytes
under ``data/photo_cache/`` keyed by URL hash.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import requests

from config import DATA_DIR

CACHE_DIR = DATA_DIR / "photo_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_SIZE_TOKEN = re.compile(r"-w\d+_h\d+(_x\d+)?")
# Zillow detail pages expose carousel *thumbnails* (``-cc_ft_192.webp``); the
# same image is served full-size at ``-cc_ft_1536.jpg``. Upgrading these is what
# lets the photo-quality scorer see real pixels instead of 192px thumbs.
_ZILLOW_SIZE_TOKEN = re.compile(r"-cc_ft_\d+\.(?:jpg|jpeg|webp|png)", re.I)
_ZILLOW_SKIP = ("zillow_web_logo",)  # carousel watermark, not a listing photo
_MLSGRID_S3 = re.compile(
    r"^https://s3\.amazonaws\.com/mlsgrid/images/([^/]+)/([^/?#]+)(?:\.[a-z]+)?",
    re.I,
)
_UNLOCKMLS_CF = "https://d36ebcehl5r1pw.cloudfront.net/images"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
}


def normalize_photo_url(url: str) -> str:
    """Rewrite known CDN variants to a fetchable URL.

    Unlock MLS detail pages expose ``s3.amazonaws.com/mlsgrid`` links that return
    403; the same image is served from their CloudFront distribution.
    """
    m = _MLSGRID_S3.match(url.strip())
    if m:
        act_id, image_id = m.group(1), m.group(2)
        if not Path(image_id).suffix:
            ext = Path(url.split("?")[0]).suffix.lower() or ".jpeg"
            image_id = f"{image_id}{ext}"
        return f"{_UNLOCKMLS_CF}/{act_id}/{image_id}"
    return url


def _request_headers(url: str) -> dict:
    headers = dict(_HEADERS)
    if "cdn-redfin.com" in url or "rdcpix.com" in url:
        headers["Referer"] = "https://www.redfin.com/"
    elif "cloudfront.net" in url or "unlockmls.com" in url or "mlsgrid" in url:
        headers["Referer"] = "https://www.search.unlockmls.com/"
    return headers


def upgrade_rdcpix_url(url: str, width: int = 2048, height: int = 1536) -> str:
    """Bump an rdcpix display URL up to a large variant. No-op for other hosts."""
    if "rdcpix.com" not in url:
        return url
    if _SIZE_TOKEN.search(url):
        return _SIZE_TOKEN.sub(f"-w{width}_h{height}", url)
    return url


def upgrade_zillow_url(url: str, width: int = 1536) -> str:
    """Bump a zillowstatic thumbnail (``-cc_ft_192.webp``) to a full variant."""
    if "zillowstatic.com" not in url:
        return url
    return _ZILLOW_SIZE_TOKEN.sub(f"-cc_ft_{width}.jpg", url)


def _cache_path(url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    ext = Path(url.split("?")[0]).suffix.lower() or ".jpg"
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    return CACHE_DIR / f"{digest}{ext}"


def download(url: str, upgrade: bool = True, timeout: int = 20) -> Path | None:
    """Download one photo (cached). Returns local path, or None on failure."""
    if not url or any(tok in url for tok in _ZILLOW_SKIP):
        return None
    fetch_url = normalize_photo_url(url)
    if upgrade:
        fetch_url = upgrade_rdcpix_url(fetch_url)
        fetch_url = upgrade_zillow_url(fetch_url)
    dest = _cache_path(fetch_url)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        resp = requests.get(fetch_url, headers=_request_headers(fetch_url), timeout=timeout)
        resp.raise_for_status()
        if not resp.content:
            return None
        dest.write_bytes(resp.content)
        return dest
    except requests.RequestException:
        # Fall back to the original (un-upgraded) URL once.
        if upgrade and fetch_url != url:
            return download(url, upgrade=False, timeout=timeout)
        return None


def download_many(urls: list[str], limit: int | None = None) -> list[Path]:
    """Download a list of photo URLs, returning the local paths that succeeded."""
    selected = urls[:limit] if limit else urls
    out: list[Path] = []
    for u in selected:
        p = download(u)
        if p is not None:
            out.append(p)
    return out
