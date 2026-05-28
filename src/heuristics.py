"""Cheap, no-ML photo quality heuristics."""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

_THRESH_PATH = Path(__file__).resolve().parent.parent / "models" / "thresholds.json"
if _THRESH_PATH.exists():
    _t = json.loads(_THRESH_PATH.read_text())
    MIN_WIDTH = _t["min_width"]
    MIN_HEIGHT = _t["min_height"]
    BLUR_THRESHOLD = _t["blur_threshold"]
    EXPOSURE_LOW = _t["exposure_low"]
    EXPOSURE_HIGH = _t["exposure_high"]
else:
    MIN_WIDTH = 1024
    MIN_HEIGHT = 768
    BLUR_THRESHOLD = 100.0
    EXPOSURE_LOW = 40
    EXPOSURE_HIGH = 215


@dataclass
class HeuristicResult:
    width: int
    height: int
    blur_score: float
    mean_luminance: float
    is_low_res: bool
    is_blurry: bool
    is_over_or_under_exposed: bool
    is_portrait: bool
    overall_score: float
    reasons: list[str]


def score_image(path: str | Path) -> HeuristicResult:
    img = cv2.imread(str(path))
    if img is None:
        raise ValueError(f"Could not read image: {path}")

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    luminance = float(gray.mean())

    is_low_res = w < MIN_WIDTH or h < MIN_HEIGHT
    is_blurry = blur < BLUR_THRESHOLD
    is_bad_exposure = luminance < EXPOSURE_LOW or luminance > EXPOSURE_HIGH
    is_portrait = h > w

    reasons: list[str] = []
    if is_low_res:
        reasons.append(f"low resolution ({w}x{h})")
    if is_blurry:
        reasons.append(f"blurry (laplacian var {blur:.1f})")
    if is_bad_exposure:
        reasons.append(f"bad exposure (luminance {luminance:.0f})")
    if is_portrait:
        reasons.append("portrait orientation")

    flags = [is_low_res, is_blurry, is_bad_exposure, is_portrait]
    score = 1.0 - (sum(flags) / len(flags))

    return HeuristicResult(
        width=w,
        height=h,
        blur_score=blur,
        mean_luminance=luminance,
        is_low_res=is_low_res,
        is_blurry=is_blurry,
        is_over_or_under_exposed=is_bad_exposure,
        is_portrait=is_portrait,
        overall_score=score,
        reasons=reasons,
    )


def score_listing(image_paths: list[str | Path]) -> dict:
    """Score a whole listing - aggregates photos plus a 'too few photos' check."""
    if not image_paths:
        return {"score": 0.0, "reasons": ["no photos"], "per_image": []}

    per = [score_image(p) for p in image_paths]
    too_few = len(image_paths) < 8
    avg = float(np.mean([r.overall_score for r in per]))

    listing_reasons: list[str] = []
    if too_few:
        listing_reasons.append(f"only {len(image_paths)} photos")
        avg *= 0.75

    return {
        "score": avg,
        "reasons": listing_reasons,
        "per_image": [asdict(r) for r in per],
    }


if __name__ == "__main__":
    import sys, json
    print(json.dumps(asdict(score_image(sys.argv[1])), indent=2))
