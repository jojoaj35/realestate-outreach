"""Legacy OpenAI LLM layer — used when ``IG_LLM_MODE=borderline`` or ``all``.

For the default AI agent pipeline see ``ig_agent.py`` (``IG_LLM_MODE=agent``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from ig_rank import build_profile_doc, should_queue

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc, assignment]


def llm_available() -> bool:
    return bool(
        settings.ig_llm_enabled
        and settings.openai_api_key
        and OpenAI is not None
        and settings.ig_llm_mode not in ("off", "clip_only")
    )


def _client() -> OpenAI | None:
    if not llm_available():
        return None
    return OpenAI(api_key=settings.openai_api_key)


def _clip_tier(rank_result: dict, require_city: bool) -> str:
    """Return ``reject``, ``accept``, or ``borderline`` from CLIP scores."""
    match = float(rank_result.get("match_score") or 0)
    city = float(rank_result.get("city_score") or 0)
    exclude = float(rank_result.get("exclude_score") or 0)

    if exclude >= settings.ig_llm_auto_reject_exclude:
        return "reject"
    if match < settings.ig_llm_auto_reject_match:
        return "reject"

    if settings.ig_llm_mode == "all":
        return "borderline"

    city_ok = (not require_city) or city >= settings.ig_llm_auto_accept_city
    if (
        match >= settings.ig_llm_auto_accept_match
        and city_ok
        and exclude < settings.ig_llm_auto_accept_exclude
    ):
        return "accept"
    return "borderline"


def _system_prompt(city: str, require_city: bool) -> str:
    city_rule = (
        f"Must actively serve the {city} metro (bio, posts, or link mention city, "
        f"neighborhoods, or local metro tags like SATX/ATX)."
        if require_city
        else "City mention is a plus but not required."
    )
    return f"""You classify Instagram profiles for outreach from a real estate photographer.

Target city: {city}
{city_rule}

QUEUE (queue=true) when the account is a residential listing agent or Realtor® who sells homes.
DO NOT queue: mortgage lenders, loan officers, photographers, videographers, stagers, title/insurance,
home builders marketing only their own developments, or generic real estate meme/inspiration pages.

Use the profile text and CLIP scores as hints. Be conservative on exclusions (photographers/lenders).
Respond with JSON only: {{"profiles":[{{"ig_handle":"...","queue":true/false,"is_realtor":bool,
"serves_target_city":bool,"exclude_reason":"","confidence":0-1,"reason":"short"}}]}}"""


def classify_profiles(
    items: list[dict],
    city: str,
    require_city: bool = True,
) -> dict[str, dict]:
    """Batch-classify profiles. Each item needs ig_handle + profile_doc (or profile dict)."""
    client = _client()
    if not client or not items:
        return {}

    payload = []
    for it in items:
        handle = it.get("ig_handle") or ""
        doc = it.get("profile_doc") or build_profile_doc(it, city)
        payload.append({
            "ig_handle": handle,
            "profile": doc,
            "clip": {
                "realtor": it.get("match_score"),
                "city": it.get("city_score"),
                "exclude": it.get("exclude_score"),
            },
        })

    user = json.dumps({"target_city": city, "profiles": payload}, ensure_ascii=False)

    try:
        resp = client.chat.completions.create(
            model=settings.ig_llm_model,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _system_prompt(city, require_city)},
                {"role": "user", "content": user},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
    except Exception as e:
        return {it.get("ig_handle", ""): {"queue": False, "reason": f"LLM error: {e}", "llm_error": True}
                for it in items}

    out: dict[str, dict] = {}
    for row in data.get("profiles") or []:
        h = (row.get("ig_handle") or "").lower().lstrip("@")
        if h:
            out[h] = row
    return out


def expand_search_queries(city: str, base_queries: list[str], max_extra: int = 12) -> list[str]:
    """Ask GPT for extra IG search strings for a city (deduped, no API if unavailable)."""
    client = _client()
    if not client or not settings.ig_llm_expand_queries:
        return []

    prompt = f"""Target city: {city}
Existing queries: {json.dumps(base_queries[:12])}

Suggest {max_extra} additional Instagram user-search queries to find LOCAL residential
real estate agents (not photographers or lenders). Use neighborhood names, metro nicknames,
and brokerage names common in the area. Return JSON: {{"queries":["..."]}} only."""

    try:
        resp = client.chat.completions.create(
            model=settings.ig_llm_model,
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You help with real estate lead discovery. JSON only."},
                {"role": "user", "content": prompt},
            ],
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        extra = [str(q).strip() for q in data.get("queries") or [] if str(q).strip()]
        return extra[:max_extra]
    except Exception:
        return []


def hybrid_should_queue(
    profile: dict,
    city: str,
    require_city: bool = True,
    rank_result: dict | None = None,
) -> tuple[bool, dict, str | None]:
    """CLIP + optional LLM decision. Returns (queue, rank_result, tier)."""
    from ig_rank import score_profile

    if profile.get("already_following") and settings.ig_skip_already_following:
        result = rank_result or {}
        reasons = list(result.get("rank_reasons") or [])
        if isinstance(reasons, str):
            try:
                reasons = json.loads(reasons)
            except json.JSONDecodeError:
                reasons = [reasons]
        reasons.append("skipped: following")
        result["rank_reasons"] = reasons
        return False, result, "following"

    result = rank_result or score_profile(profile, city)

    if not llm_available() or settings.ig_llm_mode in ("off", "clip_only"):
        ok, result = should_queue(profile, city, require_city=require_city, rank_result=result)
        return ok, result, "clip"

    tier = _clip_tier(result, require_city)

    if tier == "reject":
        reasons = list(result.get("rank_reasons") or [])
        reasons.append("skipped: CLIP auto-reject")
        result["rank_reasons"] = reasons
        profile["rank_reasons"] = json.dumps(reasons)
        return False, result, "clip_reject"

    if tier == "accept" and settings.ig_llm_mode != "all":
        ok, result = should_queue(profile, city, require_city=require_city, rank_result=result)
        reasons = list(result.get("rank_reasons") or [])
        reasons.append("CLIP auto-accept")
        result["rank_reasons"] = reasons
        profile["rank_reasons"] = json.dumps(reasons)
        return ok, result, "clip_accept"

    # borderline or mode=all — single-profile LLM call
    handle = (profile.get("ig_handle") or "").lower()
    llm_out = classify_profiles([{**profile, **result}], city, require_city=require_city)
    row = llm_out.get(handle) or {}
    if row.get("llm_error"):
        ok, result = should_queue(profile, city, require_city=require_city, rank_result=result)
        return ok, result, "clip_fallback"

    queue = bool(row.get("queue"))
    reasons = list(result.get("rank_reasons") or [])
    reasons.append(f"LLM {row.get('confidence', '?')}: {row.get('reason', '')}")
    if row.get("exclude_reason"):
        reasons.append(str(row["exclude_reason"]))
    if not queue:
        reasons.append("skipped: LLM rejected")
    result["rank_reasons"] = reasons
    result["llm_confidence"] = row.get("confidence")
    profile["rank_reasons"] = json.dumps(reasons)
    profile["match_score"] = result.get("match_score", "")
    profile["city_score"] = result.get("city_score", "")

    if queue:
        # Still respect hard CLIP exclusion cap
        exclude = float(result.get("exclude_score") or 0)
        if exclude >= settings.ig_exclude_score_threshold:
            reasons.append("skipped: CLIP exclusion override")
            result["rank_reasons"] = reasons
            profile["rank_reasons"] = json.dumps(reasons)
            return False, result, "llm_overridden"

    return queue, result, "llm"
