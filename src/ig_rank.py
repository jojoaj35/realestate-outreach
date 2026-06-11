"""CLIP text ranking for Instagram realtor discovery.

Mirrors the prompt-ensemble pattern in ``score.py``: embed a profile document and
compare against realtor / city / exclusion text prompts with softmax scores.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import settings
from score import _as_tensor, _load

# Prompt templates — city-specific prompts are interpolated at runtime.
PROMPT_TEMPLATES = {
    "realtor": [
        "Instagram profile of a residential real estate agent who sells homes",
        "Realtor helping buyers and sellers with listings",
        "Licensed real estate agent marketing homes for sale",
        "Real estate professional showing houses and closing deals",
    ],
    "generic_realtor": [
        "A generic real estate agent profile on social media",
        "Someone who works in real estate or property sales",
        "Real estate industry professional on Instagram",
    ],
    "city_agent": [
        "Real estate agent serving {city} Texas area",
        "Local realtor in {city} metro helping home buyers",
        "Listing agent active in {city} neighborhoods",
        "Realtor who sells homes in and around {city}",
    ],
    "exclude_lender": [
        "Mortgage lender loan officer",
        "Home lending and refinancing specialist",
        "Mortgage broker offering home loans",
    ],
    "exclude_photo": [
        "Real estate photographer videographer",
        "Listing media and staging company",
        "Drone photography for real estate marketing",
    ],
}

_ig_text_feats: dict[str, torch.Tensor] | None = None


def _city_prompts(city: str) -> list[str]:
    return [p.format(city=city) for p in PROMPT_TEMPLATES["city_agent"]]


def _ig_text_features(city: str) -> dict[str, torch.Tensor]:
    """Mean-pooled L2-normalized embeddings per class (cached per city)."""
    global _ig_text_feats
    cache_key = city.lower().strip()
    if _ig_text_feats is not None and _ig_text_feats.get("_city") == cache_key:
        return _ig_text_feats

    model, proc, device, _ = _load()
    feats: dict[str, torch.Tensor] = {"_city": cache_key}  # type: ignore[assignment]

    class_prompts = {
        "realtor": PROMPT_TEMPLATES["realtor"],
        "generic_realtor": PROMPT_TEMPLATES["generic_realtor"],
        "city_agent": _city_prompts(city),
        "exclude_lender": PROMPT_TEMPLATES["exclude_lender"],
        "exclude_photo": PROMPT_TEMPLATES["exclude_photo"],
    }

    for key, prompts in class_prompts.items():
        inputs = proc(text=prompts, return_tensors="pt", padding=True, truncation=True).to(device)
        with torch.no_grad():
            out = model.get_text_features(**inputs)
        t = _as_tensor(out)
        t = t / t.norm(dim=-1, keepdim=True)
        mean = t.mean(dim=0)
        feats[key] = mean / mean.norm()

    _ig_text_feats = feats
    return feats


def _binary_prob(feat: torch.Tensor, pos: str, neg: str, city: str) -> float:
    model, *_ = _load()
    scale = model.logit_scale.exp().item()
    tf = _ig_text_features(city)
    sp = float(feat @ tf[pos]) * scale
    sn = float(feat @ tf[neg]) * scale
    m = max(sp, sn)
    ep, en = math.exp(sp - m), math.exp(sn - m)
    return ep / (ep + en)


def build_profile_doc(profile: dict, city: str) -> str:
    """Single text document for embedding."""
    handle = profile.get("ig_handle") or profile.get("handle") or ""
    display = profile.get("display_name") or ""
    bio = profile.get("bio") or ""
    captions = profile.get("recent_post_captions") or profile.get("post_captions") or []
    if isinstance(captions, str):
        captions = [captions] if captions else []
    link_title = profile.get("link_in_bio_title") or ""
    link_url = profile.get("link_in_bio_url") or profile.get("external_url") or ""

    parts = [f"{display} @{handle}".strip(), bio]
    if captions:
        parts.append("Recent posts: " + "; ".join(c for c in captions[:6] if c))
    if link_title or link_url:
        parts.append(f"Link in bio: {link_title} {link_url}".strip())
    parts.append(f"City target: {city}")
    return "\n".join(p for p in parts if p)


def _embed_text(text: str, city: str) -> torch.Tensor:
    model, proc, device, _ = _load()
    inputs = proc(
        text=[text],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512,
    ).to(device)
    with torch.no_grad():
        out = model.get_text_features(**inputs)
    feat = _as_tensor(out)
    feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat[0]


def embed_texts(texts: list[str], city: str) -> np.ndarray:
    """Batch-embed profile documents; returns L2-normalized numpy array."""
    if not texts:
        return np.zeros((0, 512), dtype=np.float32)
    model, proc, device, _ = _load()
    inputs = proc(
        text=texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512,
    ).to(device)
    with torch.no_grad():
        out = model.get_text_features(**inputs)
    feat = _as_tensor(out)
    feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat.cpu().numpy().astype(np.float32)


def score_profile(profile: dict, city: str, doc: str | None = None) -> dict:
    """Return CLIP-based scores and human-readable reason chips."""
    doc = doc or build_profile_doc(profile, city)
    feat = _embed_text(doc, city)

    realtor_score = _binary_prob(feat, "realtor", "exclude_lender", city)
    city_score = _binary_prob(feat, "city_agent", "generic_realtor", city)
    lender_score = _binary_prob(feat, "exclude_lender", "realtor", city)
    photo_score = _binary_prob(feat, "exclude_photo", "realtor", city)
    exclude_score = max(lender_score, photo_score)

    reasons: list[str] = [
        f"realtor {realtor_score:.2f}",
        f"city {city_score:.2f}",
    ]
    if lender_score >= settings.ig_exclude_score_threshold:
        reasons.append("lender-like")
    if photo_score >= settings.ig_exclude_score_threshold:
        reasons.append("photographer-like")
    if exclude_score >= settings.ig_exclude_score_threshold:
        reasons.append(f"exclude {exclude_score:.2f}")

    return {
        "match_score": round(realtor_score, 4),
        "city_score": round(city_score, 4),
        "exclude_score": round(exclude_score, 4),
        "lender_score": round(lender_score, 4),
        "photo_score": round(photo_score, 4),
        "rank_reasons": reasons,
        "profile_doc": doc,
        "embedding": feat.cpu().numpy().astype(np.float32),
    }


def should_queue(
    profile: dict,
    city: str,
    require_city: bool = True,
    rank_result: dict | None = None,
) -> tuple[bool, dict]:
    """CLIP rank decision; returns (queue, rank_result with scores)."""
    if settings.ig_skip_already_following and profile.get("already_following"):
        result = rank_result or {}
        result = {
            **result,
            "rank_reasons": list(result.get("rank_reasons") or []) + ["skipped: following"],
        }
        return False, result

    result = rank_result or score_profile(profile, city)
    profile["match_score"] = result.get("match_score", "")
    profile["city_score"] = result.get("city_score", "")
    profile["rank_reasons"] = json.dumps(result.get("rank_reasons") or [])

    realtor_ok = result.get("match_score", 0) >= settings.ig_realtor_score_threshold
    city_ok = (not require_city) or result.get("city_score", 0) >= settings.ig_city_score_threshold
    exclude_ok = result.get("exclude_score", 1) < settings.ig_exclude_score_threshold

    if not realtor_ok:
        reasons = list(result.get("rank_reasons") or [])
        reasons.append("skipped: low realtor score")
        result["rank_reasons"] = reasons
    if require_city and not city_ok:
        reasons = list(result.get("rank_reasons") or [])
        reasons.append("skipped: low city score")
        result["rank_reasons"] = reasons
    if not exclude_ok:
        reasons = list(result.get("rank_reasons") or [])
        reasons.append("skipped: exclusion match")
        result["rank_reasons"] = reasons

    queue = realtor_ok and city_ok and exclude_ok
    profile["rank_reasons"] = json.dumps(result.get("rank_reasons") or [])
    return queue, result
