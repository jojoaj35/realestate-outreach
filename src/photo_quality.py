"""Content-agnostic photographic-craft signals.

The original scorer leaned on a CLIP k-NN that keyed on *scene content* (a
luxury kitchen vs a dated one) rather than on how well the photo was shot and
edited. That meant professionally shot photos of modest houses were flagged as
"amateur" and amateur snapshots of mansions slipped through.

This module measures objective, content-agnostic markers of photographic craft
so the score reflects the *photography*, not the house:

  - highlight clipping / window blow-out  (amateurs blow out windows; pros pull
    the window or bracket/flash to hold both interior and exterior detail)
  - crushed shadows / black clipping
  - overall dynamic range (flat, hazy, low-contrast captures)
  - white-balance neutrality (orange tungsten / green fluorescent casts on what
    should be neutral walls and ceilings)
  - sharpness / motion blur
  - vertical straightness / keystoning (camera tilted up/down) — delegated to
    ``heuristics.vertical_straightness``
  - portrait-orientation penalty (pros shoot landscape, wide-angle)

Every sub-score is in ``[0, 1]`` where 1.0 == good craft. The aggregate
``craft_score`` is a weighted blend. All weights and thresholds are named,
tunable module constants. None of these signals look at *what* the room is, only
at the optical/tonal quality of the capture.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import heuristics

# --- Aggregate blend weights (sum to 1.0) ----------------------------------
# Tuned against the user's hand-labeled photo-craft set (5 amateur, 1 pro):
#   * BRIGHTNESS dominates. On the labeled data the pro listing is uniformly
#     bright/airy (mean luma ~190) while every amateur listing sits at ~115-135.
#     The user's own rule: "the room is BRIGHT ... a sign that it was edited."
#     This high-key look is the residential-MLS professional standard, so it
#     carries the score (it is content-agnostic: luminance, not the house).
#   * WHITE BALANCE, STRAIGHTNESS, SHARPNESS, FRAMING are secondary craft tells.
#   * WINDOW PULL (highlight clipping) is only a light penalty for *egregious*
#     blow-out: counter-intuitively, bright edited photos clip slightly MORE
#     than dark amateur ones, so heavy weight here would punish the good work.
#   * DYNAMIC RANGE is intentionally ZERO-weighted: pro HDR/edited photos lift
#     shadows and pull highlights, which *lowers* global contrast — so global
#     dynamic range is anti-diagnostic in this domain (kept only for reporting).
#   * Crooked verticals matter but must NOT dominate — the user's good example
#     has slightly tilted verticals yet is professional because it is bright with
#     window pull. Hence straightness is moderate, brightness >> straightness.
W_BRIGHTNESS = 0.48      # bright/airy edited look vs dark/dull amateur capture (dominant)
W_WINDOW_PULL = 0.12     # windows held (not egregiously blown to white)
W_WHITE_BALANCE = 0.10   # neutral whites, no tungsten/fluorescent cast
W_STRAIGHTNESS = 0.08    # level camera — must not rescue a uniformly dark gallery
W_SHARPNESS = 0.08       # in focus, no motion blur
W_FRAMING = 0.08         # frames the room, not two converging blank walls
W_SHADOW = 0.04          # shadows not fully crushed to black
W_ORIENTATION = 0.02     # landscape (pro) vs portrait phone snapshot
W_DYNAMIC_RANGE = 0.00   # anti-diagnostic in this domain (see note above)

# --- Brightness (dark / dull -> under-exposed amateur capture) -------------
# Mean luminance. Calibrated to the high-key professional look: amateur phone
# shots of a room land ~115-140, professionally lit/edited interiors land ~170+.
BRIGHT_DARK_LUMA = 120       # <= this mean luminance -> 0.0 (dark/dull/unedited)
BRIGHT_GOOD_LUMA = 180       # >= this mean luminance -> 1.0 (bright, airy, edited)

# Dull-but-not-dark: flat, hazy mid-tones common on phone interiors (not dark enough
# to fail brightness, but still obviously unedited).
DULL_LUMA_LO = 125
DULL_LUMA_HI = 178
DULL_SPREAD_MAX = 0.48       # low global contrast in this luma band -> dull capture
DULL_CRAFT_MULT = 0.82       # multiply craft when dull (applied after blend)

# --- Window pull / highlight clipping --------------------------------------
# Only flags *egregious* blow-out (all three channels maxed across a big area).
# Light weight, because bright edited photos legitimately clip a little.
HILITE_CLIP_LEVEL = 250      # a pixel with all channels >= this is "blown"
HILITE_GOOD_FRAC = 0.015     # <= this blown fraction -> 1.0
HILITE_BAD_FRAC = 0.09       # >= this blown fraction -> 0.0 (windows nuked)

# --- Shadow clipping (crushed blacks) --------------------------------------
SHADOW_LUMA = 6              # a pixel at/below this luminance is "crushed"
SHADOW_GOOD_FRAC = 0.01
SHADOW_BAD_FRAC = 0.12

# --- Dynamic range / contrast (reported only; zero-weighted) ---------------
DR_LOW_SPREAD = 0.40
DR_HIGH_SPREAD = 0.75

# --- Framing / aim (room vs two converging blank walls) --------------------
# Amateurs aim into a corner so the frame is mostly two big featureless walls;
# a pro frames the actual room (furniture, depth, detail). We approximate "blank
# wall dominance" as the fraction of the frame with very low local texture.
FRAMING_FLAT_STD = 7.0       # local std below this == featureless wall
FRAMING_BLOCK = 16           # local-texture window size (px, on resized image)
FRAMING_GOOD_FLAT = 0.45     # <= this flat fraction -> 1.0 (well framed)
FRAMING_BAD_FLAT = 0.82      # >= this flat fraction -> 0.0 (aimed at walls)

# --- White balance (color cast on neutrals) --------------------------------
# White-patch assumption: the brightest interior surfaces (ceilings, walls,
# trim, window light) should be neutral. We measure the average chroma of the
# bright, non-clipped band in CIELAB; a correctly white-balanced photo leaves
# them near-gray, while tungsten/fluorescent casts shift them warm/green. This
# is content-agnostic — a garish but correctly-exposed room still reads neutral
# because saturated decor is rarely the *brightest* surface.
WB_BRIGHT_LUMA_LO = 175      # lower edge of the "bright surface" band
WB_CLIP_LUMA = 249           # exclude fully clipped pixels (chroma 0 by force)
WB_MIN_SAMPLE_FRAC = 0.01    # need this fraction of bright pixels to judge
WB_GOOD_CHROMA = 4.0         # mean LAB chroma <= this -> 1.0 (neutral)
WB_BAD_CHROMA = 22.0         # >= this -> 0.0 (strong cast)
WB_UNKNOWN_SCORE = 0.7       # not enough bright pixels to judge -> mild benefit

# --- Sharpness / blur ------------------------------------------------------
# Variance of the Laplacian. Content/resolution dependent, hence forgiving and
# lightly weighted; only clearly soft frames are penalised.
SHARP_BLURRY_VAR = 60.0      # <= this -> 0.0 (soft / motion blur)
SHARP_GOOD_VAR = 280.0       # >= this -> 1.0 (crisp)

# --- Working resolution ----------------------------------------------------
MAX_DIM = 1280               # downscale long edge before analysis (speed)
MIN_ANALYZE_DIM = 200        # below this (thumbnails) tonal stats are unreliable


def _lerp_score(value: float, lo: float, hi: float, *, invert: bool = False) -> float:
    """Map ``value`` onto ``[0, 1]`` between ``lo`` and ``hi`` (clamped).

    ``invert=False``: value>=hi -> 1.0, value<=lo -> 0.0 (bigger is better).
    ``invert=True`` : value<=lo -> 1.0, value>=hi -> 0.0 (smaller is better).
    """
    if hi == lo:
        return 0.5
    t = (value - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return 1.0 - t if invert else t


@dataclass
class PhotoQuality:
    craft_score: float
    window_pull_score: float
    brightness_score: float
    shadow_score: float
    white_balance_score: float
    straightness_score: float
    sharpness_score: float
    dynamic_range_score: float
    framing_score: float
    orientation_score: float
    # Raw measurements (useful for diagnostics / thresholds tuning).
    blown_frac: float
    mean_luma: float
    crushed_frac: float
    cast_chroma: float
    laplacian_var: float
    tonal_spread: float
    flat_frac: float
    vertical_dev_deg: float
    is_portrait: bool
    flags: list[str]


def _window_pull_score(bgr: np.ndarray) -> tuple[float, float]:
    """High when windows are held (low blown fraction), low when nuked white."""
    blown = float(np.mean(np.all(bgr >= HILITE_CLIP_LEVEL, axis=2)))
    score = _lerp_score(blown, HILITE_GOOD_FRAC, HILITE_BAD_FRAC, invert=True)
    return score, blown


def _brightness_score(gray: np.ndarray) -> tuple[float, float]:
    luma = float(gray.mean())
    return _lerp_score(luma, BRIGHT_DARK_LUMA, BRIGHT_GOOD_LUMA), luma


def _framing_score(gray: np.ndarray) -> tuple[float, float]:
    """Penalise frames dominated by featureless wall (camera aimed at a corner).

    Uses local standard deviation: a well-framed room is full of texture
    (furniture, edges, depth), while a corner shot is mostly two flat walls.
    """
    g = gray.astype(np.float32)
    k = (FRAMING_BLOCK, FRAMING_BLOCK)
    mean = cv2.blur(g, k)
    sq = cv2.blur(g * g, k)
    var = np.clip(sq - mean * mean, 0, None)
    std = np.sqrt(var)
    flat_frac = float(np.mean(std < FRAMING_FLAT_STD))
    return _lerp_score(flat_frac, FRAMING_GOOD_FLAT, FRAMING_BAD_FLAT, invert=True), flat_frac


def _shadow_score(gray: np.ndarray) -> tuple[float, float]:
    crushed = float(np.mean(gray <= SHADOW_LUMA))
    score = _lerp_score(crushed, SHADOW_GOOD_FRAC, SHADOW_BAD_FRAC, invert=True)
    return score, crushed


def _dynamic_range_score(gray: np.ndarray) -> tuple[float, float]:
    p2, p98 = np.percentile(gray, [2, 98])
    spread = float((p98 - p2) / 255.0)
    return _lerp_score(spread, DR_LOW_SPREAD, DR_HIGH_SPREAD), spread


def _white_balance_score(bgr: np.ndarray, gray: np.ndarray) -> tuple[float, float]:
    """Color cast on the bright surfaces that should be neutral (white-patch).

    Returns (score, mean_chroma). Content-agnostic: only the brightest,
    non-clipped band votes, so a correctly white-balanced photo of a garish room
    still scores well, while a tungsten/fluorescent cast shifts the whole band.
    """
    mask = (gray >= WB_BRIGHT_LUMA_LO) & (gray <= WB_CLIP_LUMA)
    if mask.mean() < WB_MIN_SAMPLE_FRAC:
        return WB_UNKNOWN_SCORE, 0.0
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    a = lab[:, :, 1][mask] - 128.0
    b = lab[:, :, 2][mask] - 128.0
    # Cast magnitude is the chroma of the *average* bright pixel (so opposing
    # tints can't cancel into a falsely-neutral per-pixel mean).
    chroma = float(np.hypot(a.mean(), b.mean()))
    return _lerp_score(chroma, WB_GOOD_CHROMA, WB_BAD_CHROMA, invert=True), chroma


def _sharpness_score(gray: np.ndarray) -> tuple[float, float]:
    var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return _lerp_score(var, SHARP_BLURRY_VAR, SHARP_GOOD_VAR), var


def _downscale(bgr: np.ndarray) -> np.ndarray:
    h, w = bgr.shape[:2]
    long_edge = max(h, w)
    if long_edge <= MAX_DIM:
        return bgr
    scale = MAX_DIM / long_edge
    return cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)


def _neutral(is_portrait: bool) -> PhotoQuality:
    """Score for images too small to judge (thumbnails): neutral, no opinion."""
    s = 0.5
    return PhotoQuality(
        craft_score=s, window_pull_score=s, brightness_score=s, shadow_score=s,
        white_balance_score=s, straightness_score=s, sharpness_score=s,
        dynamic_range_score=s, framing_score=s,
        orientation_score=0.0 if is_portrait else 1.0,
        blown_frac=0.0, mean_luma=0.0, crushed_frac=0.0, cast_chroma=0.0,
        laplacian_var=0.0, tonal_spread=0.0, flat_frac=0.0, vertical_dev_deg=0.0,
        is_portrait=is_portrait,
        flags=["too small to judge"] + (["portrait orientation"] if is_portrait else []),
    )


def analyze(path: str | Path) -> PhotoQuality:
    """Compute content-agnostic photographic-craft sub-scores for one image."""
    bgr_full = cv2.imread(str(path))
    if bgr_full is None:
        raise ValueError(f"Could not read image: {path}")

    h_full, w_full = bgr_full.shape[:2]
    is_portrait = h_full > w_full
    if min(h_full, w_full) < MIN_ANALYZE_DIM:
        return _neutral(is_portrait)
    bgr = _downscale(bgr_full)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    window_pull, blown = _window_pull_score(bgr)
    brightness, luma = _brightness_score(gray)
    shadow, crushed = _shadow_score(gray)
    wb, cast = _white_balance_score(bgr, gray)
    sharp, lap_var = _sharpness_score(gray)
    dr, spread = _dynamic_range_score(gray)
    framing, flat_frac = _framing_score(gray)
    straight, vdev, _ = heuristics.vertical_straightness(gray)
    orientation = 0.0 if is_portrait else 1.0

    craft = (
        W_BRIGHTNESS * brightness
        + W_WINDOW_PULL * window_pull
        + W_WHITE_BALANCE * wb
        + W_STRAIGHTNESS * straight
        + W_SHARPNESS * sharp
        + W_FRAMING * framing
        + W_SHADOW * shadow
        + W_ORIENTATION * orientation
        + W_DYNAMIC_RANGE * dr
    )
    if DULL_LUMA_LO <= luma <= DULL_LUMA_HI and spread < DULL_SPREAD_MAX:
        craft *= DULL_CRAFT_MULT

    flags: list[str] = []
    if DULL_LUMA_LO <= luma <= DULL_LUMA_HI and spread < DULL_SPREAD_MAX:
        flags.append(f"dull / flat (luma {luma:.0f}, low contrast)")
    if window_pull < 0.5:
        flags.append(f"blown-out windows ({blown*100:.1f}% clipped white)")
    if brightness < 0.5:
        flags.append(f"dark / dull (mean luma {luma:.0f})")
    if dr < 0.5:
        flags.append("flat / low contrast")
    if wb < 0.5 and cast > 0:
        flags.append(f"color cast (chroma {cast:.0f})")
    if straight < 0.4 and vdev > 0:
        flags.append(f"crooked verticals (~{vdev:.1f}° off)")
    if sharp < 0.5:
        flags.append(f"soft focus (lap var {lap_var:.0f})")
    if shadow < 0.5:
        flags.append(f"crushed shadows ({crushed*100:.1f}% black)")
    if framing < 0.5:
        flags.append("poor framing (too much blank wall)")
    if is_portrait:
        flags.append("portrait orientation")

    return PhotoQuality(
        craft_score=round(craft, 4),
        window_pull_score=round(window_pull, 4),
        brightness_score=round(brightness, 4),
        shadow_score=round(shadow, 4),
        white_balance_score=round(wb, 4),
        straightness_score=round(straight, 4),
        sharpness_score=round(sharp, 4),
        dynamic_range_score=round(dr, 4),
        framing_score=round(framing, 4),
        orientation_score=orientation,
        blown_frac=round(blown, 5),
        mean_luma=round(luma, 1),
        crushed_frac=round(crushed, 5),
        cast_chroma=round(cast, 2),
        laplacian_var=round(lap_var, 1),
        tonal_spread=round(spread, 3),
        flat_frac=round(flat_frac, 4),
        vertical_dev_deg=round(vdev, 2),
        is_portrait=is_portrait,
        flags=flags,
    )


def craft_score(path: str | Path) -> float:
    return analyze(path).craft_score


if __name__ == "__main__":
    import json
    print(json.dumps(asdict(analyze(sys.argv[1])), indent=2))
