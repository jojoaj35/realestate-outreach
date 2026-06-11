"""Playwright helpers for Instagram discovery and DMs.

Uses a persistent Chrome profile (``IG_BROWSER_PROFILE``) so you log in once
manually, then sessions persist across runs — same idea as the realtor scraper.
"""
from __future__ import annotations

import random
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import ROOT, settings

LOGIN_HINTS = ["log in", "sign up", "create an account", "log into instagram"]
CHALLENGE_HINTS = ["verify", "suspicious", "try again later", "checkpoint"]


def profile_dir() -> Path:
    p = Path(settings.ig_browser_profile)
    if not p.is_absolute():
        p = ROOT / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def polite_sleep(min_s: float = 2.0, max_s: float = 5.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


@contextmanager
def instagram_context(headless: bool = False):
    """Launch persistent Chrome for Instagram."""
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir()),
            headless=headless,
            channel="chrome",
            args=["--disable-blink-features=AutomationControlled"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        try:
            yield ctx
        finally:
            ctx.close()


def _page_text(page: Page) -> str:
    try:
        return page.inner_text("body").lower()[:8000]
    except Exception:
        return ""


def ensure_logged_in(page: Page, progress=None) -> bool:
    """Return True if logged in; pause for manual login if not."""
    page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=60000)
    polite_sleep(2, 4)

    def _msg(m: str):
        if progress:
            progress({"message": m})

    body = _page_text(page)
    # Logged-in home shows nav icons; login page shows sign-up prompts.
    if page.locator('svg[aria-label="Home"], svg[aria-label="Home page"]').count() > 0:
        return True
    if page.locator('a[href*="/direct/inbox/"]').count() > 0:
        return True
    if any(h in body for h in LOGIN_HINTS) and page.locator('input[name="username"]').count() > 0:
        _msg("Not logged in — log into Instagram in the browser window, then press Enter here…")
        try:
            input("  → Press Enter after you've logged in… ")
        except EOFError:
            return False
        page.reload(wait_until="domcontentloaded")
        polite_sleep(2, 3)
        return page.locator('svg[aria-label="Home"], svg[aria-label="Home page"]').count() > 0
    # Assume logged in if no obvious login form.
    return True


def check_already_following(page: Page, handle: str) -> bool:
    """Return True if the logged-in account already follows ``handle``."""
    handle = handle.lstrip("@").lower()
    if not handle:
        return False
    script = """
    async (username) => {
      const csrftoken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      const headers = {
        'X-IG-App-ID': '936619743392459',
        'X-CSRFToken': csrftoken,
        'X-Requested-With': 'XMLHttpRequest',
      };
      try {
        const profileResp = await fetch(
          '/api/v1/users/web_profile_info/?username=' + encodeURIComponent(username),
          { headers, credentials: 'include' }
        );
        if (!profileResp.ok) return false;
        const profileData = await profileResp.json();
        const userId = profileData?.data?.user?.id;
        if (!userId) return false;

        const friendshipResp = await fetch(
          '/api/v1/friendships/show/' + userId + '/',
          { headers, credentials: 'include' }
        );
        if (!friendshipResp.ok) return false;
        const friendship = await friendshipResp.json();
        return !!(friendship.following || friendship.outgoing_request);
      } catch (e) {
        return false;
      }
    }
    """
    try:
        return bool(page.evaluate(script, handle))
    except Exception:
        return False


def search_users(page: Page, query: str, limit: int = 20) -> list[dict]:
    """Search Instagram for users matching ``query`` via the internal API."""
    script = """
    async ({ query, limit }) => {
      const url = '/web/search/topsearch/?query=' + encodeURIComponent(query);
      const r = await fetch(url, { credentials: 'include' });
      if (!r.ok) return [];
      const data = await r.json();
      return (data.users || []).slice(0, limit).map(u => ({
        ig_handle: u.user?.username || '',
        display_name: u.user?.full_name || '',
        profile_url: u.user?.username ? ('https://www.instagram.com/' + u.user.username + '/') : '',
        source: 'search:' + query,
      }));
    }
    """
    try:
        return page.evaluate(script, {"query": query, "limit": limit}) or []
    except Exception:
        return []


def _fetch_link_in_bio(page: Page, url: str) -> dict:
    """Lightweight second hop: page title from external bio link."""
    if not url or "instagram.com" in url.lower():
        return {"link_in_bio_url": url, "link_in_bio_title": ""}
    try:
        title = page.evaluate(
            """
            async (linkUrl) => {
              try {
                const r = await fetch(linkUrl, { credentials: 'omit', redirect: 'follow' });
                const html = await r.text();
                const m = html.match(/<title[^>]*>([^<]+)<\\/title>/i);
                return m ? m[1].trim() : '';
              } catch (e) {
                return '';
              }
            }
            """,
            url,
        ) or ""
        return {"link_in_bio_url": url, "link_in_bio_title": title[:200]}
    except Exception:
        return {"link_in_bio_url": url, "link_in_bio_title": ""}


def _profile_api_data(page: Page, handle: str) -> dict:
    """Fetch web_profile_info for captions, bio link, and display name."""
    script = """
    async (username) => {
      const csrftoken = document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '';
      const headers = {
        'X-IG-App-ID': '936619743392459',
        'X-CSRFToken': csrftoken,
        'X-Requested-With': 'XMLHttpRequest',
      };
      try {
        const r = await fetch(
          '/api/v1/users/web_profile_info/?username=' + encodeURIComponent(username),
          { headers, credentials: 'include' }
        );
        if (!r.ok) return {};
        const data = await r.json();
        const user = data?.data?.user || {};
        const edges = user.edge_owner_to_timeline_media?.edges || [];
        const captions = edges.slice(0, 6).map(e => {
          const cap = e?.node?.edge_media_to_caption?.edges?.[0]?.node?.text;
          return cap || '';
        }).filter(Boolean);
        const bioLinks = user.bio_links || [];
        const external = user.external_url || bioLinks[0]?.url || '';
        return {
          display_name: user.full_name || '',
          bio: user.biography || '',
          external_url: external || '',
          recent_post_captions: captions,
        };
      } catch (e) {
        return {};
      }
    }
    """
    try:
        return page.evaluate(script, handle) or {}
    except Exception:
        return {}


def scrape_profile(page: Page, handle: str) -> dict | None:
    """Visit a profile page and extract bio, captions, and link-in-bio metadata."""
    handle = handle.lstrip("@").lower()
    if not handle:
        return None
    url = f"https://www.instagram.com/{handle}/"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        polite_sleep(1.5, 3)
    except Exception:
        return None

    body = _page_text(page)
    if "sorry, this page isn't available" in body or "page not found" in body:
        return None

    bio = ""
    followers = ""
    display_name = handle
    recent_post_captions: list[str] = []
    link_in_bio_url = ""
    link_in_bio_title = ""

    api_data = _profile_api_data(page, handle)
    if api_data.get("display_name"):
        display_name = api_data["display_name"]
    if api_data.get("bio"):
        bio = api_data["bio"]
    recent_post_captions = list(api_data.get("recent_post_captions") or [])
    link_in_bio_url = api_data.get("external_url") or ""

    # Meta tags are the most stable source.
    try:
        og_desc = page.locator('meta[property="og:description"]').get_attribute("content") or ""
        # Format: "1,234 Followers, 567 Following, 89 Posts - See Instagram photos..."
        m = re.match(r"([\d,.KkMm]+)\s+Followers?", og_desc)
        if m:
            followers = m.group(1)
        og_title = page.locator('meta[property="og:title"]').get_attribute("content") or ""
        if og_title and "(" in og_title and display_name == handle:
            display_name = og_title.split("(")[0].strip()
    except Exception:
        pass

    # Bio from header section (fallback if API missed it).
    if not bio:
        for sel in ('header section div.-vDIg span', 'header section span[dir="auto"]',
                    'div[data-testid="user-bio"]'):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    text = loc.inner_text().strip()
                    if text and len(text) > 3 and text.lower() != handle:
                        bio = text
                        break
            except Exception:
                continue

    if not bio:
        try:
            bio = page.evaluate("""
              () => {
                const meta = document.querySelector('meta[name="description"]');
                if (!meta) return '';
                const c = meta.content || '';
                const dash = c.indexOf(' - ');
                return dash > 0 ? c.slice(dash + 3).split(' from ')[0].trim() : '';
              }
            """) or ""
        except Exception:
            pass

    if not recent_post_captions:
        try:
            recent_post_captions = page.evaluate("""
              () => {
                const alts = [...document.querySelectorAll('article img[alt]')]
                  .map(img => img.getAttribute('alt') || '')
                  .filter(a => a.length > 8 && !a.toLowerCase().includes('profile picture'));
                return alts.slice(0, 6);
              }
            """) or []
        except Exception:
            pass

    if link_in_bio_url:
        link_meta = _fetch_link_in_bio(page, link_in_bio_url)
        link_in_bio_title = link_meta.get("link_in_bio_title", "")

    already_following = check_already_following(page, handle)

    return {
        "ig_handle": handle,
        "display_name": display_name,
        "bio": bio,
        "follower_count": followers,
        "profile_url": url,
        "already_following": already_following,
        "recent_post_captions": recent_post_captions,
        "link_in_bio_url": link_in_bio_url,
        "link_in_bio_title": link_in_bio_title,
    }


def collect_hashtag_users(page: Page, hashtag: str, scrolls: int = 4) -> list[str]:
    """Collect usernames from a hashtag explore page."""
    tag = hashtag.lstrip("#").lower()
    page.goto(f"https://www.instagram.com/explore/tags/{tag}/",
              wait_until="domcontentloaded", timeout=45000)
    polite_sleep(2, 4)

    handles: set[str] = set()
    for _ in range(scrolls):
        try:
            links = page.evaluate("""
              () => [...document.querySelectorAll('a[href^="/"]')]
                .map(a => a.getAttribute('href'))
                .filter(h => h && /^\\/[A-Za-z0-9._]+\\/$/.test(h))
                .map(h => h.slice(1, -1).toLowerCase())
            """) or []
            for h in links:
                if h not in {"explore", "p", "reel", "stories", "accounts", "direct", tag}:
                    handles.add(h)
        except Exception:
            pass
        page.mouse.wheel(0, 1200)
        polite_sleep(1.5, 2.5)

    return sorted(handles)


def copy_to_clipboard(text: str) -> bool:
    """Copy text to macOS clipboard (assisted DM mode)."""
    import subprocess
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
        return True
    except Exception:
        return False


def send_dm_automated(page: Page, handle: str, message: str) -> tuple[bool, str]:
    """Open profile, click Message, type and send. Returns (ok, detail)."""
    handle = handle.lstrip("@").lower()
    url = f"https://www.instagram.com/{handle}/"
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        polite_sleep(2, 3)
    except Exception as e:
        return False, f"profile load failed: {e}"

    body = _page_text(page)
    if any(h in body for h in CHALLENGE_HINTS):
        return False, "Instagram challenge/rate limit detected — try later or use assisted mode"

    # Message button variants.
    clicked = False
    for label in ("Message", "Send message"):
        btn = page.get_by_role("button", name=re.compile(label, re.I))
        if btn.count():
            try:
                btn.first.click(timeout=5000)
                clicked = True
                break
            except Exception:
                pass
    if not clicked:
        link = page.locator('a[href*="/direct/t/"]').first
        if link.count():
            try:
                link.click(timeout=5000)
                clicked = True
            except Exception:
                pass
    if not clicked:
        return False, "Message button not found (may need to follow them first)"

    polite_sleep(1.5, 2.5)

    # Message input — contenteditable div in DM thread.
    input_sel = 'div[role="textbox"][contenteditable="true"], div[contenteditable="true"][aria-label*="Message"]'
    box = page.locator(input_sel).last
    if not box.count():
        return False, "DM input box not found"
    try:
        box.click(timeout=5000)
        box.fill(message)
        polite_sleep(0.5, 1)
        page.keyboard.press("Enter")
        polite_sleep(1, 2)
    except Exception as e:
        return False, f"typing failed: {e}"

    return True, "sent via browser"
