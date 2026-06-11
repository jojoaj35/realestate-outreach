"""Enrich Instagram contacts with profile attributes for the propensity model.

Scrapes follower/following/post counts, business flag, category, and bio for the
*engaged* Instagram subset (contacts who replied or booked) — not all ~3,700
threads — using the project's logged-in persistent Chrome profile
(``IG_BROWSER_PROFILE``). Data comes from Instagram's authenticated
``web_profile_info`` JSON endpoint, fetched in the page context so the session
cookies + app id are applied.

Output: ``data/booked/ig_profiles.json`` (``{handle: {profile fields}}``),
resumable across runs. Re-run ``features`` + ``train_booked`` afterward to fold
the new signal into the model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .paths import BOOKED_DIR, FEATURES_CSV

IG_PROFILES_JSON = BOOKED_DIR / "ig_profiles.json"
_APP_ID = "936619743392459"
_PROFILE_API = "https://www.instagram.com/api/v1/users/web_profile_info/?username="

_FETCH_JS = """
async (handle) => {
  const r = await fetch(
    'https://www.instagram.com/api/v1/users/web_profile_info/?username=' + handle,
    { headers: { 'x-ig-app-id': '%s' }, credentials: 'include' });
  if (!r.ok) return { error: r.status };
  const j = await r.json();
  const u = (j.data && j.data.user) || null;
  if (!u) return { error: 'no_user' };
  return {
    follower_count: u.edge_followed_by ? u.edge_followed_by.count : 0,
    following_count: u.edge_follow ? u.edge_follow.count : 0,
    post_count: u.edge_owner_to_timeline_media ? u.edge_owner_to_timeline_media.count : 0,
    is_business: (u.is_business_account || u.is_professional_account) ? 1 : 0,
    is_verified: u.is_verified ? 1 : 0,
    category: u.category_name || u.business_category_name || '',
    bio: u.biography || '',
    full_name: u.full_name || '',
  };
}
""" % _APP_ID


def _targets(max_n: int | None) -> list[str]:
    df = pd.read_csv(FEATURES_CSV)
    ig = df[(df.is_ig == 1) & ((df.replied == 1) | (df.label == 1))]
    handles = [h for h in ig["handle"].dropna().astype(str) if h]
    # de-dup, keep order
    seen, out = set(), []
    for h in handles:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:max_n] if max_n else out


def _load_existing() -> dict:
    if IG_PROFILES_JSON.exists():
        return json.loads(IG_PROFILES_JSON.read_text())
    return {}


def enrich(max_n: int | None = None, headless: bool = True) -> dict:
    from ig_browser import ensure_logged_in, instagram_context, polite_sleep

    targets = _targets(max_n)
    profiles = _load_existing()
    todo = [h for h in targets if h not in profiles]
    print(f"[enrich] {len(targets)} engaged IG handles; {len(todo)} to fetch "
          f"({len(profiles)} cached)")
    if not todo:
        return profiles

    fetched = 0
    with instagram_context(headless=headless) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not ensure_logged_in(page):
            print("[enrich] not logged into Instagram — aborting; model runs without enrichment")
            return profiles
        for i, handle in enumerate(todo, 1):
            try:
                result = page.evaluate(_FETCH_JS, handle)
            except Exception as exc:  # noqa: BLE001
                result = {"error": str(exc)[:80]}
            if result and not result.get("error"):
                profiles[handle] = result
                fetched += 1
            else:
                profiles[handle] = {"error": (result or {}).get("error", "unknown")}
            if i % 10 == 0 or i == len(todo):
                IG_PROFILES_JSON.write_text(json.dumps(profiles, indent=2))
                print(f"  {i}/{len(todo)} fetched={fetched}")
            polite_sleep(3, 7)

    IG_PROFILES_JSON.write_text(json.dumps(profiles, indent=2))
    ok = sum(1 for v in profiles.values() if not v.get("error"))
    print(f"[enrich] done: {ok}/{len(profiles)} profiles with data -> {IG_PROFILES_JSON}")
    return profiles


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=None)
    ap.add_argument("--show", action="store_true", help="run with a visible browser window")
    args = ap.parse_args()
    enrich(max_n=args.max, headless=not args.show)
