"""AI discovery agent for Instagram realtor outreach.

Uses OpenAI tool-calling: the model plans searches, inspects profiles, and decides
who to queue. CLIP scores are hints only — GPT makes the final call.

Requires ``OPENAI_API_KEY`` in .env and ``IG_LLM_MODE=agent`` (default).
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
from ig_rank import build_profile_doc, score_profile
from ig_sources import discovery_hashtags, google_instagram_handles, load_city_config, search_queries
from ig_store import get_ig_store, normalize_handle

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc, assignment]

MAX_AGENT_TURNS = 50


def agent_available() -> bool:
    return bool(
        settings.openai_api_key
        and OpenAI is not None
        and settings.ig_llm_mode == "agent"
        and settings.ig_llm_enabled
    )


def _client() -> OpenAI:
    if not settings.openai_api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is required for AI agent mode. Add it to your .env file."
        )
    if OpenAI is None:
        raise RuntimeError("Install openai: pip install openai")
    return OpenAI(api_key=settings.openai_api_key)


TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "instagram_search",
            "description": "Search Instagram for user accounts matching a query string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "IG search query, e.g. 'Stone Oak realtor'"},
                    "limit": {"type": "integer", "description": "Max results (default 15)", "default": 15},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_hashtag",
            "description": "Collect usernames from an Instagram hashtag explore page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "hashtag": {"type": "string", "description": "Hashtag without #"},
                    "scrolls": {"type": "integer", "default": 3},
                },
                "required": ["hashtag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "google_find_handles",
            "description": "Google site:instagram.com search for realtor handles in the target city.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "inspect_profile",
            "description": (
                "Visit an Instagram profile and return bio, posts, CLIP hint scores, "
                "and skip flags (already following, DNC)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "description": "Instagram username without @"},
                },
                "required": ["handle"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "queue_realtor",
            "description": "Add a previously inspected profile to the outreach queue.",
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string"},
                    "reason": {"type": "string", "description": "Why this is a good outreach target"},
                    "confidence": {"type": "number", "description": "0-1 confidence score"},
                },
                "required": ["handle", "reason", "confidence"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_profile",
            "description": "Mark a profile as not a fit (must inspect first unless already known).",
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["handle", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_progress",
            "description": "Current discovery stats: queued count, inspected, skipped, etc.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_discovery",
            "description": "End the discovery run when goal is met or no more leads likely.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Brief summary of what was found"},
                },
                "required": ["summary"],
            },
        },
    },
]


@dataclass
class AgentContext:
    city: str
    max_results: int
    require_city: bool
    page: Any
    store: Any
    corpus: Any
    seen: set[str] = field(default_factory=set)
    inspected: dict[str, dict] = field(default_factory=dict)
    matched: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    finished: bool = False
    finish_summary: str = ""

    def __post_init__(self):
        if not self.stats:
            self.stats = {
                "searched": 0,
                "profiles_checked": 0,
                "matched": 0,
                "skipped_following": 0,
                "skipped_dnc": 0,
                "skipped_llm": 0,
                "skipped_semantic_dup": 0,
                "skipped_low_score": 0,
                "llm_adjudicated": 0,
                "agent_turns": 0,
                "added": 0,
                "updated": 0,
            }


def _system_prompt(ctx: AgentContext) -> str:
    cfg = load_city_config(ctx.city)
    return f"""You are an AI discovery agent for a real estate photographer doing cold Instagram DMs.

GOAL: Find and queue up to {ctx.max_results} residential REAL ESTATE AGENTS (Realtors who sell homes)
in {ctx.city} for outreach. You have {ctx.max_results - len(ctx.matched)} slots remaining.

TARGET: Licensed listing agents, buyer's agents, team leads at brokerages (KW, Compass, etc.)
SKIP: Mortgage lenders, loan officers, real estate photographers/videographers, stagers,
title/insurance, home builders, meme/inspiration pages, accounts already on DNC or already followed.

City context: metro aliases {cfg.get('metro_aliases', [])}, neighborhoods {cfg.get('neighborhoods', [])[:6]}.

WORKFLOW:
1. Search with varied queries/hashtags/google until you have candidate handles.
2. inspect_profile before queue_realtor or skip_profile.
3. queue_realtor only for confident residential agents serving {ctx.city}.
4. Call finish_discovery when queued >= goal or searches are exhausted.

Be efficient: batch searches, inspect promising handles, don't re-inspect the same handle."""


def _inspect(ctx: AgentContext, handle: str) -> dict:
    handle = normalize_handle(handle)
    if not handle:
        return {"error": "invalid handle"}

    if ctx.store.is_dnc(handle):
        ctx.stats["skipped_dnc"] += 1
        return {"handle": handle, "skip": True, "reason": "on do-not-contact list"}

    if handle in ctx.inspected:
        return ctx.inspected[handle]

    ctx.stats["profiles_checked"] += 1
    profile = scrape_profile(ctx.page, handle)
    if not profile:
        return {"handle": handle, "error": "profile not found or private"}

    profile["city"] = ctx.city
    profile["source"] = profile.get("source") or "agent:inspect"

    if profile.get("already_following"):
        ctx.stats["skipped_following"] += 1
        rank = score_profile(profile, ctx.city)
        ctx.corpus.record(
            handle, rank.get("profile_doc", ""), rank, ["skipped: following"],
            queued=False, already_following=True, exclude_anchor=True,
        )
        out = {
            "handle": handle,
            "skip": True,
            "reason": "already following",
            "display_name": profile.get("display_name"),
            "bio": profile.get("bio", "")[:300],
        }
        ctx.inspected[handle] = out
        return out

    rank = score_profile(profile, ctx.city)
    is_dup, dup_of = ctx.corpus.is_semantic_duplicate(rank.get("embedding"), store=ctx.store)
    if is_dup:
        ctx.stats["skipped_semantic_dup"] += 1
        ctx.corpus.record(handle, rank.get("profile_doc", ""), rank,
                          [f"semantic dup of @{dup_of}"], queued=False, exclude_anchor=True)
        out = {"handle": handle, "skip": True, "reason": f"similar to excluded @{dup_of}"}
        ctx.inspected[handle] = out
        return out

    out = {
        "handle": handle,
        "display_name": profile.get("display_name"),
        "bio": profile.get("bio", "")[:500],
        "follower_count": profile.get("follower_count"),
        "recent_post_captions": (profile.get("recent_post_captions") or [])[:6],
        "link_in_bio": profile.get("link_in_bio_url") or profile.get("external_url"),
        "clip_hints": {
            "realtor_score": rank.get("match_score"),
            "city_score": rank.get("city_score"),
            "exclude_score": rank.get("exclude_score"),
        },
        "profile_doc": rank.get("profile_doc"),
        "skip": False,
        "_profile": profile,
        "_rank": rank,
    }
    ctx.inspected[handle] = out
    polite_sleep(1, 2)
    return {k: v for k, v in out.items() if not k.startswith("_")}


def _queue(ctx: AgentContext, handle: str, reason: str, confidence: float) -> dict:
    handle = normalize_handle(handle)
    if len(ctx.matched) >= ctx.max_results:
        return {"ok": False, "error": f"goal already met ({ctx.max_results} queued)"}

    info = ctx.inspected.get(handle)
    if not info or info.get("skip"):
        return {"ok": False, "error": "inspect_profile first and ensure not skipped"}

    profile = info.get("_profile")
    rank = info.get("_rank")
    if not profile or not rank:
        return {"ok": False, "error": "missing profile data — inspect again"}

    profile["status"] = "queued"
    profile["match_score"] = round(float(confidence), 4)
    profile["city_score"] = rank.get("city_score", "")
    reasons = [f"agent {confidence:.2f}: {reason}", f"clip realtor {rank.get('match_score')}"]
    profile["rank_reasons"] = json.dumps(reasons)

    ctx.corpus.record(handle, rank.get("profile_doc", ""), rank, reasons, queued=True)
    ctx.matched.append(profile)
    ctx.stats["matched"] += 1
    ctx.stats["llm_adjudicated"] += 1
    ctx.seen.add(handle)
    return {"ok": True, "handle": handle, "queued_total": len(ctx.matched), "goal": ctx.max_results}


def _skip(ctx: AgentContext, handle: str, reason: str) -> dict:
    handle = normalize_handle(handle)
    info = ctx.inspected.get(handle)
    if info and info.get("_rank"):
        ctx.corpus.record(
            handle,
            info["_rank"].get("profile_doc", ""),
            info["_rank"],
            [f"agent skip: {reason}"],
            queued=False,
        )
    ctx.stats["skipped_llm"] += 1
    ctx.seen.add(handle)
    return {"ok": True, "handle": handle, "reason": reason}


def execute_tool(ctx: AgentContext, name: str, args: dict) -> str:
    """Run one agent tool; return JSON string for the model."""
    try:
        if name == "instagram_search":
            q = args.get("query", "")
            limit = int(args.get("limit") or 15)
            users = search_users(ctx.page, q, limit=limit)
            ctx.stats["searched"] += len(users)
            handles = []
            for u in users:
                h = normalize_handle(u.get("ig_handle", ""))
                if h and h not in ctx.seen:
                    handles.append(h)
            polite_sleep(1, 2)
            return json.dumps({"query": q, "handles": handles[:limit], "count": len(handles)})

        if name == "scan_hashtag":
            tag = (args.get("hashtag") or "").lstrip("#")
            scrolls = int(args.get("scrolls") or 3)
            handles = collect_hashtag_users(ctx.page, tag, scrolls=scrolls)
            new = [h for h in handles if h not in ctx.seen]
            polite_sleep(2, 3)
            return json.dumps({"hashtag": tag, "handles": new[:30], "count": len(new)})

        if name == "google_find_handles":
            if not settings.ig_google_search_enabled:
                return json.dumps({"handles": [], "note": "google search disabled in config"})
            handles = google_instagram_handles(ctx.page, ctx.city)
            new = [h for h in handles if h not in ctx.seen]
            return json.dumps({"handles": new, "count": len(new)})

        if name == "inspect_profile":
            return json.dumps(_inspect(ctx, args.get("handle", "")))

        if name == "queue_realtor":
            return json.dumps(_queue(
                ctx,
                args.get("handle", ""),
                args.get("reason", ""),
                float(args.get("confidence") or 0.5),
            ))

        if name == "skip_profile":
            return json.dumps(_skip(ctx, args.get("handle", ""), args.get("reason", "")))

        if name == "get_progress":
            return json.dumps({
                **ctx.stats,
                "queued": len(ctx.matched),
                "goal": ctx.max_results,
                "inspected_count": len(ctx.inspected),
            })

        if name == "finish_discovery":
            ctx.finished = True
            ctx.finish_summary = args.get("summary", "")
            return json.dumps({"ok": True, "summary": ctx.finish_summary})

        return json.dumps({"error": f"unknown tool {name}"})
    except Exception as e:
        return json.dumps({"error": str(e)})


def run_discovery_agent(
    city: str,
    max_results: int = 30,
    headless: bool = False,
    require_city: bool = True,
    progress=None,
) -> dict:
    """Run GPT tool-calling agent to discover and queue realtor IG profiles."""
    def _progress(msg: str, **extra):
        if progress:
            progress({"message": msg, **extra})
        print(msg, flush=True)

    store = get_ig_store()
    corpus = get_ig_corpus()
    client = _client()

    with instagram_context(headless=headless) as browser_ctx:
        page = browser_ctx.pages[0] if browser_ctx.pages else browser_ctx.new_page()
        if not ensure_logged_in(page, progress=progress):
            raise RuntimeError("Instagram login required — log in via the browser window.")

        ctx = AgentContext(
            city=city,
            max_results=max_results,
            require_city=require_city,
            page=page,
            store=store,
            corpus=corpus,
            seen={normalize_handle(r["ig_handle"]) for r in store.all()},
        )
        ctx.seen.discard("")

        base_queries = search_queries(city)[:8]
        base_tags = discovery_hashtags(city)[:6]
        user_start = json.dumps({
            "city": city,
            "goal": max_results,
            "suggested_start_queries": base_queries,
            "suggested_hashtags": base_tags,
            "already_in_queue": len(store.get_by_status("queued")),
        })

        messages: list[dict] = [
            {"role": "system", "content": _system_prompt(ctx)},
            {"role": "user", "content": (
                f"Begin discovery for {city}. Queue up to {max_results} residential listing agents.\n"
                f"Context: {user_start}\n"
                "Start by searching, then inspect and queue the best matches."
            )},
        ]

        _progress(f"AI agent started ({settings.ig_llm_model}) — goal: {max_results} realtors in {city}")

        for turn in range(MAX_AGENT_TURNS):
            if ctx.finished or len(ctx.matched) >= max_results:
                break

            ctx.stats["agent_turns"] = turn + 1
            resp = client.chat.completions.create(
                model=settings.ig_llm_model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
            )
            msg = resp.choices[0].message
            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                if msg.content:
                    _progress(f"Agent: {msg.content[:200]}")
                continue

            for tc in msg.tool_calls:
                fn = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                _progress(f"Agent → {fn}({json.dumps(args)[:80]})")
                result = execute_tool(ctx, fn, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
                if ctx.finished or len(ctx.matched) >= max_results:
                    break

        if ctx.matched:
            added, updated = store.upsert_profiles(ctx.matched)
            ctx.stats["added"] = added
            ctx.stats["updated"] = updated

        summary = ctx.finish_summary or f"Queued {len(ctx.matched)} profiles"
        _progress(
            f"Agent done — {summary}. "
            f"Queued {ctx.stats['matched']}, inspected {ctx.stats['profiles_checked']}, "
            f"{ctx.stats['agent_turns']} turns, added {ctx.stats['added']}"
        )
        return ctx.stats
