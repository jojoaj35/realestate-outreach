"""Cheap, no-ML photo quality heuristics."""
from __future__ import annotations

import json
import math
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


# Vertical-straightness tuning. A pro shoots on a tripod with the camera level,
# so true-vertical edges (door/window frames, wall corners) stay within ~1-2°
# of vertical. Pointing the phone up/down makes those edges converge (keystone),
# pushing the mean deviation well past this. Score maps deviation -> [0,1].
VERT_NEAR_VERTICAL_DEG = 25.0   # a segment counts as "vertical-ish" within this
VERT_MIN_LEN_FRAC = 0.12        # ignore segments shorter than 12% of image height
# Pro real-estate work uses wide-angle lenses, which add a few degrees of
# perspective/lens distortion to "vertical" edges even on a level tripod, and the
# line detector also picks up near-vertical furniture/decor. The old 0.6°/3.0°
# band was far too strict and scored professional galleries near 0. These wider
# bounds only flag clearly keystoned phone shots (camera pointed up/down).
VERT_GOOD_DEG = 1.5             # <= this deviation -> straight (1.0)
VERT_BAD_DEG = 8.0             # >= this deviation -> clearly tilted/keystoned (0.0)


def vertical_straightness(gray: np.ndarray) -> tuple[float, float, int]:
    """Measure how vertical the building's vertical edges are.

    Returns (score, mean_deviation_degrees, n_lines):
      - score 1.0  = verticals are dead straight (level, professional)
      - score 0.0  = verticals converge a lot (camera pointed up/down, amateur)
    Falls back to a neutral 0.5 when there aren't enough vertical edges to judge.
    """
    h, w = gray.shape[:2]
    try:
        lsd = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
        lines = lsd.detect(gray)[0]
    except cv2.error:
        return 0.5, 0.0, 0
    if lines is None:
        return 0.5, 0.0, 0

    min_len = VERT_MIN_LEN_FRAC * h
    devs, weights = [], []
    for ln in lines:
        x1, y1, x2, y2 = ln[0]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < min_len:
            continue
        ang = math.degrees(math.atan2(abs(dx), abs(dy)))  # 0 = perfectly vertical
        if ang > VERT_NEAR_VERTICAL_DEG:
            continue
        devs.append(ang)
        weights.append(length)

    if len(devs) < 3:
        return 0.5, 0.0, len(devs)

    mean_dev = float(np.average(devs, weights=weights))
    if mean_dev <= VERT_GOOD_DEG:
        score = 1.0
    elif mean_dev >= VERT_BAD_DEG:
        score = 0.0
    else:
        score = 1.0 - (mean_dev - VERT_GOOD_DEG) / (VERT_BAD_DEG - VERT_GOOD_DEG)
    return score, mean_dev, len(devs)


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
