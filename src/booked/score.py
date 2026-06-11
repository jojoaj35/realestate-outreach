"""Score Instagram profiles with the booking-propensity model.

Bridges the trained model (``models/booked_propensity.joblib``) into the live
Instagram outreach tool: given a discovered/queued profile it returns a
``book_score`` in [0, 1] = the modeled probability that this *type* of agent
books a paid shoot. Used to prioritize the DM queue (highest propensity first).

For a not-yet-contacted profile the engagement features are zero, so the score
reflects agent-type signal (handle/display keywords, follower count, business
flag, bio) — exactly the "what type of agent books" question.
"""
from __future__ import annotations

import re

import joblib
import pandas as pd

from .features import _KEYWORDS, _has_emoji
from .paths import MODEL_PATH


def parse_count(value) -> int:
    """'1,234' / '1.2K' / '3.4M' / 1200 -> int."""
    if value is None or value == "":
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().replace(",", "").upper()
    m = re.match(r"^([\d.]+)\s*([KM]?)$", s)
    if not m:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
    num = float(m.group(1))
    mult = {"K": 1_000, "M": 1_000_000}.get(m.group(2), 1)
    return int(num * mult)


class BookedScorer:
    """Loads the propensity model once and scores Instagram profiles."""

    def __init__(self, model_path=MODEL_PATH):
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model not found at {model_path}. Run `python run.py booked-train` first."
            )
        art = joblib.load(model_path)
        self.model = art["model"]
        self.cols = art["feature_cols"]
        self.model_name = art.get("model_name", "?")

    def features_for_ig(self, profile: dict) -> dict:
        handle = (profile.get("ig_handle") or profile.get("handle") or "").lstrip("@").lower()
        display = profile.get("display_name") or ""
        bio = profile.get("bio") or ""
        # Keyword blob mirrors training (handle + display name).
        kw_blob = f"{handle} {display}".lower()

        feat = {c: 0 for c in self.cols}
        if "is_ig" in feat:
            feat["is_ig"] = 1
        if "display_has_emoji" in feat:
            feat["display_has_emoji"] = _has_emoji(display)
        for col, pat in _KEYWORDS.items():
            if col in feat:
                feat[col] = int(bool(re.search(pat, kw_blob)))

        followers = parse_count(profile.get("follower_count"))
        following = parse_count(profile.get("following_count"))
        posts = parse_count(profile.get("post_count"))
        if "follower_count" in feat:
            feat["follower_count"] = followers
        if "following_count" in feat:
            feat["following_count"] = following
        if "post_count" in feat:
            feat["post_count"] = posts
        if "is_business" in feat:
            feat["is_business"] = int(bool(profile.get("is_business")))
        if "bio_len" in feat:
            feat["bio_len"] = len(bio)
        if "has_profile_data" in feat:
            feat["has_profile_data"] = int(bool(followers or posts))
        return feat

    def score(self, profile: dict) -> float:
        return self.score_many([profile])[0]

    def score_many(self, profiles: list[dict]) -> list[float]:
        if not profiles:
            return []
        X = pd.DataFrame([[self.features_for_ig(p)[c] for c in self.cols]
                          for p in profiles], columns=self.cols)
        return [round(float(p), 4) for p in self.model.predict_proba(X)[:, 1]]


_scorer: BookedScorer | None = None


def get_scorer() -> BookedScorer | None:
    """Cached scorer; returns None if the model isn't trained yet."""
    global _scorer
    if _scorer is None:
        try:
            _scorer = BookedScorer()
        except FileNotFoundError as exc:
            print(f"[score] {exc}")
            return None
    return _scorer


def score_ig_queue() -> int:
    """(Re)score every profile in the Instagram queue and persist book_score."""
    from ig_store import get_ig_store

    scorer = get_scorer()
    if scorer is None:
        return 0
    store = get_ig_store()
    rows = store.all()
    if not rows:
        print("[score] ig_queue is empty.")
        return 0
    scores = scorer.score_many(rows)
    for row, sc in zip(rows, scores):
        row["book_score"] = sc
    store._write_all(rows)  # bulk write keeps it fast vs per-row update
    top = sorted(zip(rows, scores), key=lambda x: -x[1])[:10]
    print(f"[score] scored {len(rows)} profiles (model={scorer.model_name}). Top prospects:")
    for row, sc in top:
        print(f"  {sc:.3f}  @{row.get('ig_handle','')[:32]:32s} {row.get('display_name','')[:30]}")
    return len(rows)


if __name__ == "__main__":
    score_ig_queue()
