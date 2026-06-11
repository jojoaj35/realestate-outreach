"""Discover realtor Instagram profiles for a target city.

Default mode (``IG_LLM_MODE=agent``): OpenAI tool-calling agent plans searches,
inspects profiles, and queues realtors (requires ``OPENAI_API_KEY``).

Legacy modes (``borderline`` / ``clip_only``): CLIP ranking pipeline without agent loop.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from ig_browser import (
    collect_hashtag_users,
    ensure_logged_in,
    instagram_context,
    polite_sleep,
    scrape_profile,
    search_users,
)
from ig_corpus import get_ig_corpus
from ig_llm import hybrid_should_queue, llm_available
from ig_rank import score_profile
from ig_sources import discovery_hashtags, google_instagram_handles, search_queries
from ig_store import get_ig_store, normalize_handle
from store import get_store


def discover(
    city: str,
    max_results: int = 50,
    enrich_from_queue: bool = False,
    headless: bool = False,
    require_city: bool = True,
    realtor_score_threshold: float | None = None,
    progress=None,
) -> dict:
    """Find realtor profiles and upsert into ig_queue.csv."""
    store = get_ig_store()
    corpus = get_ig_corpus()
    prev_realtor_threshold = settings.ig_realtor_score_threshold
    if realtor_score_threshold is not None:
        settings.ig_realtor_score_threshold = realtor_score_threshold

    try:
        if settings.ig_llm_mode == "agent":
            if not settings.openai_api_key:
                raise RuntimeError(
                    "AI agent mode requires OPENAI_API_KEY in .env. "
                    "Get one at https://platform.openai.com/api-keys"
                )
            from ig_agent import run_discovery_agent
            return run_discovery_agent(
                city=city,
                max_results=max_results,
                headless=headless,
                require_city=require_city,
                progress=progress,
            )
        return _discover_impl(
            city=city,
            max_results=max_results,
            enrich_from_queue=enrich_from_queue,
            headless=headless,
            require_city=require_city,
            progress=progress,
            store=store,
            corpus=corpus,
        )
    finally:
        if realtor_score_threshold is not None:
            settings.ig_realtor_score_threshold = prev_realtor_threshold


def _discover_impl(
    *,
    city: str,
    max_results: int,
    enrich_from_queue: bool,
    headless: bool,
    require_city: bool,
    progress,
    store,
    corpus,
) -> dict:
    """Inner discover loop (separated for threshold restore via try/finally)."""
    seen: set[str] = {normalize_handle(r["ig_handle"]) for r in store.all()}
    seen.discard("")
    candidates: dict[str, dict] = {}
    stats = {
        "searched": 0,
        "profiles_checked": 0,
        "matched": 0,
        "skipped_following": 0,
        "skipped_low_score": 0,
        "skipped_semantic_dup": 0,
        "skipped_dnc": 0,
        "skipped_llm": 0,
        "llm_adjudicated": 0,
        "added": 0,
        "updated": 0,
    }

    def _progress(msg: str, **extra):
        if progress:
            progress({"message": msg, **extra})
        print(msg, flush=True)

    with instagram_context(headless=headless) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not ensure_logged_in(page, progress=progress):
            raise RuntimeError("Instagram login required — log in via the browser window.")

        if llm_available():
            _progress(f"LLM rerank enabled ({settings.ig_llm_mode}, {settings.ig_llm_model})")

        # --- expanded IG search ---
        for query in search_queries(city):
            if len(candidates) >= max_results * 3:
                break
            _progress(f"Searching Instagram: {query!r}")
            users = search_users(page, query, limit=25)
            stats["searched"] += len(users)
            for u in users:
                h = normalize_handle(u.get("ig_handle", ""))
                if h and h not in seen and h not in candidates:
                    candidates[h] = {**u, "city": city, "source": u.get("source") or f"search:{query}"}
            polite_sleep(1, 2)

        # --- hashtags ---
        for tag in discovery_hashtags(city):
            if len(candidates) >= max_results * 3:
                break
            _progress(f"Scanning hashtag #{tag}")
            try:
                handles = collect_hashtag_users(page, tag, scrolls=3)
            except Exception as e:
                _progress(f"  hashtag #{tag} failed: {e}")
                continue
            for h in handles:
                if h not in seen and h not in candidates:
                    candidates[h] = {
                        "ig_handle": h,
                        "city": city,
                        "source": f"hashtag:{tag}",
                    }
            polite_sleep(2, 4)

        # --- Google site:instagram.com fallback ---
        if settings.ig_google_search_enabled:
            _progress("Google site:instagram.com search (fallback)")
            try:
                for h in google_instagram_handles(page, city):
                    if h not in seen and h not in candidates:
                        candidates[h] = {
                            "ig_handle": h,
                            "city": city,
                            "source": "google:site",
                        }
            except Exception as e:
                _progress(f"  Google search failed: {e}")

        # --- enrich from listing queue ---
        if enrich_from_queue:
            listing_store = get_store()
            agents: set[str] = set()
            for row in listing_store.all():
                name = (row.get("agent_name") or "").strip()
                if name and len(name) > 3:
                    agents.add(name)
            for name in sorted(agents)[:30]:
                q = f"{name} realtor"
                _progress(f"Cross-search from queue: {name!r}")
                for u in search_users(page, q, limit=5):
                    h = normalize_handle(u.get("ig_handle", ""))
                    if h and h not in seen and h not in candidates:
                        candidates[h] = {
                            **u,
                            "city": city,
                            "source": f"queue:{name}",
                        }
                polite_sleep(1, 2)

        # --- visit profiles, rank, and filter ---
        matched: list[dict] = []
        for i, (handle, stub) in enumerate(list(candidates.items())):
            if len(matched) >= max_results:
                break
            stats["profiles_checked"] += 1
            _progress(
                f"Checking @{handle} ({i + 1}/{len(candidates)})",
                done=i,
                total=len(candidates),
            )

            if store.is_dnc(handle):
                stats["skipped_dnc"] += 1
                _progress(f"Skipping @{handle} — DNC")
                polite_sleep(0.5, 1)
                continue

            profile = scrape_profile(page, handle)
            if not profile:
                polite_sleep(1, 2)
                continue
            profile["city"] = city
            profile["source"] = stub.get("source", profile.get("source", ""))
            if stub.get("display_name") and not profile.get("display_name"):
                profile["display_name"] = stub["display_name"]

            if profile.get("already_following"):
                stats["skipped_following"] += 1
                _progress(f"Skipping @{handle} — already following")
                rank_result = score_profile(profile, city)
                corpus.record(
                    handle,
                    rank_result.get("profile_doc", ""),
                    rank_result,
                    rank_result.get("rank_reasons") or [],
                    queued=False,
                    already_following=True,
                    exclude_anchor=True,
                )
                polite_sleep(1, 2)
                continue

            rank_result = score_profile(profile, city)
            is_dup, dup_handle = corpus.is_semantic_duplicate(
                rank_result.get("embedding"),
                store=store,
            )
            if is_dup:
                stats["skipped_semantic_dup"] += 1
                reasons = list(rank_result.get("rank_reasons") or [])
                reasons.append(f"skipped: semantic dup of @{dup_handle}")
                rank_result["rank_reasons"] = reasons
                profile["rank_reasons"] = json.dumps(reasons)
                corpus.record(
                    handle,
                    rank_result.get("profile_doc", ""),
                    rank_result,
                    reasons,
                    queued=False,
                    exclude_anchor=True,
                )
                _progress(f"Skipping @{handle} — similar to excluded @{dup_handle}")
                polite_sleep(1, 2)
                continue

            queue_ok, rank_result, tier = hybrid_should_queue(
                profile, city, require_city=require_city, rank_result=rank_result,
            )
            if tier in ("llm", "llm_overridden"):
                stats["llm_adjudicated"] += 1
            reasons = rank_result.get("rank_reasons") or []
            if isinstance(reasons, str):
                try:
                    reasons = json.loads(reasons)
                except json.JSONDecodeError:
                    reasons = [reasons]

            corpus.record(
                handle,
                rank_result.get("profile_doc", ""),
                rank_result,
                reasons,
                queued=queue_ok,
            )

            if queue_ok:
                profile["status"] = "queued"
                try:
                    from booked.score import get_scorer
                    scorer = get_scorer()
                    if scorer is not None:
                        profile["book_score"] = scorer.score(profile)
                except Exception as exc:  # noqa: BLE001
                    _progress(f"  book_score skipped for @{handle}: {exc}")
                matched.append(profile)
                stats["matched"] += 1
            else:
                if tier in ("llm",) and any("LLM rejected" in str(r) for r in reasons):
                    stats["skipped_llm"] += 1
                else:
                    stats["skipped_low_score"] += 1
            polite_sleep(1.5, 3)

    if matched:
        added, updated = store.upsert_profiles(matched)
        stats["added"] = added
        stats["updated"] = updated

    _progress(
        f"Done — checked {stats['profiles_checked']} profiles, "
        f"matched {stats['matched']}, skipped {stats['skipped_following']} already following, "
        f"skipped {stats['skipped_low_score']} low score, "
        f"skipped {stats['skipped_llm']} LLM rejected, "
        f"LLM reviewed {stats['llm_adjudicated']}, "
        f"skipped {stats['skipped_semantic_dup']} semantic dup, "
        f"added {stats['added']}, updated {stats['updated']}"
    )
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover realtor Instagram profiles for a city.")
    ap.add_argument("--city", default="San Antonio", help="target city/metro")
    ap.add_argument("--max", type=int, default=50, help="max profiles to queue")
    ap.add_argument("--enrich-from-queue", action="store_true",
                    help="also search IG for agents already in the listing queue")
    ap.add_argument("--no-require-city", action="store_true",
                    help="accept realtors without city mention in bio")
    ap.add_argument("--headless", action="store_true")
    args = ap.parse_args()
    discover(
        city=args.city,
        max_results=args.max,
        enrich_from_queue=args.enrich_from_queue,
        headless=args.headless,
        require_city=not args.no_require_city,
    )


if __name__ == "__main__":
    main()
