"""Build the per-contact feature table for the propensity model.

One row per outreach contact (iMessage phone or Instagram thread), joined to its
conversation engagement and (for Instagram) handle/display-name attributes and
any scraped profile enrichment. The target is ``label`` (= booked via payment).
``likely_cash`` rows are flagged so training can hold them out of the negatives.

Output: ``data/booked/features.csv``.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd

from .paths import (
    BOOKED_DIR,
    FEATURES_CSV,
    IG_THREADS_JSONL,
    IMSG_THREADS_JSONL,
    LABELED_CSV,
)

IG_PROFILES_JSON = BOOKED_DIR / "ig_profiles.json"  # written by ig_enrich (optional)

# Keyword groups scanned in handle + display name (Instagram) for "agent type".
_KEYWORDS = {
    "kw_realtor": r"realtor|realty|real\s?estate|realestate|broker|agent|homes|properties|listing",
    "kw_team": r"team|group|properties|realty|homes|associates|collective|co\b",
    "kw_luxury": r"luxury|estate|exclusive|prestige|elite|premier",
    "kw_mortgage_lender": r"mortgage|lender|loan|lending|nmls",
    "kw_photographer": r"photo|photographer|media|films|productions|videograph",
    "kw_investor": r"invest|investor|wholesale|flip",
    "city_austin": r"austin|atx|atex",
    "city_satx": r"sanantonio|san\s?antonio|satx|210",
}


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def _span_days(first: str, last: str) -> float:
    try:
        a = dt.datetime.fromisoformat(first[:19])
        b = dt.datetime.fromisoformat(last[:19])
        return round(abs((b - a).total_seconds()) / 86400.0, 2)
    except (ValueError, TypeError):
        return 0.0


def _has_emoji(text: str) -> int:
    return int(any(ord(ch) > 0x2600 for ch in (text or "")))


def build(out: Path = FEATURES_CSV) -> pd.DataFrame:
    labeled = pd.read_csv(LABELED_CSV, dtype=str).fillna("")
    imsg = {r["phone"]: r for r in _load_jsonl(IMSG_THREADS_JSONL) if r.get("phone")}
    ig = {r["handle"]: r for r in _load_jsonl(IG_THREADS_JSONL) if r.get("handle")}
    profiles = {}
    if IG_PROFILES_JSON.exists():
        profiles = {k.lower(): v for k, v in json.loads(IG_PROFILES_JSON.read_text()).items()}

    rows = []
    for _, r in labeled.iterrows():
        channel = r["channel"]
        key = r["key"]
        thread = imsg.get(key, {}) if channel == "imsg" else ig.get(key, {})

        num_owner = int(thread.get("num_owner_msgs", 0) or 0)
        num_their = int(thread.get("num_their_msgs", 0) or 0)
        total = num_owner + num_their
        their_text = thread.get("their_text", "") or ""
        display = thread.get("display_name", "") if channel == "ig" else ""
        kw_blob = f"{key} {display}".lower()

        feat = {
            "key": key,
            "channel": channel,
            "person_id": r.get("person_id", ""),
            "name": r.get("name", ""),
            "handle": r.get("handle", ""),
            "phone": r.get("phone", ""),
            "label": int(r["booked"] == "1"),
            "likely_cash": int(r.get("likely_cash", "0") in ("1", 1)),
            "label_source": r.get("label_source", "none"),
            "match_confidence": float(r.get("match_confidence", 0) or 0),
            # engagement
            "is_ig": int(channel == "ig"),
            "is_imsg": int(channel == "imsg"),
            "replied": int(r.get("replied", "0") in ("1", 1)),
            "num_owner_msgs": num_owner,
            "num_their_msgs": num_their,
            "total_msgs": total,
            "reply_ratio": round(num_their / total, 3) if total else 0.0,
            "their_text_len": len(their_text),
            "num_links": int(thread.get("num_links", 0) or 0),
            "thread_span_days": _span_days(
                thread.get("first_ts") or thread.get("first_contacted", ""),
                thread.get("last_ts") or thread.get("last_message", ""),
            ),
            "name_len": len(r.get("name", "") or ""),
            "handle_len": len(r.get("handle", "") or ""),
            "display_has_emoji": _has_emoji(display),
        }
        for col, pat in _KEYWORDS.items():
            feat[col] = int(bool(re.search(pat, kw_blob)))

        # Optional scraped IG profile enrichment.
        prof = profiles.get((r.get("handle") or "").lower(), {})
        feat["follower_count"] = int(prof.get("follower_count", 0) or 0)
        feat["following_count"] = int(prof.get("following_count", 0) or 0)
        feat["post_count"] = int(prof.get("post_count", 0) or 0)
        feat["is_business"] = int(prof.get("is_business", 0) or 0)
        feat["bio_len"] = len(prof.get("bio", "") or "")
        feat["has_profile_data"] = int(bool(prof))
        rows.append(feat)

    df = pd.DataFrame(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(
        f"[features] {len(df)} rows x {df.shape[1]} cols -> {out}\n"
        f"  positives(label=1)={int(df.label.sum())}  "
        f"likely_cash={int(df.likely_cash.sum())}  "
        f"replied={int(df.replied.sum())}  with_profile={int(df.has_profile_data.sum())}"
    )
    return df


if __name__ == "__main__":
    build()
