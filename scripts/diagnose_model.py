"""Diagnose why good listings get low pro scores.

For every training listing folder, recompute the per-listing scoring exactly the
way score_listings.py does (pro_style from CLIP k-NN, vertical straightness, and
the blended clip_score), then report the breakdown so we can see which component
is dragging professional listings down.

Run:  ./venv/bin/python scripts/diagnose_model.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import heuristics  # noqa: E402
import photo_quality  # noqa: E402
from score import classify, _image_feature, _binary_prob  # noqa: E402
from score_listings import _trimmed_mean  # noqa: E402

TRAIN = ROOT / "training_data"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _real_photos(folder: Path) -> list[Path]:
    """Skip degenerate thumbnails (e.g. 19x11 px) the scraper left behind."""
    out = []
    for p in sorted(folder.rglob("*")):
        if p.suffix.lower() not in IMG_EXTS:
            continue
        img = cv2.imread(str(p))
        if img is not None and min(img.shape[:2]) >= photo_quality.MIN_ANALYZE_DIM:
            out.append(p)
    return out


def score_folder(folder: Path, sample: int = 10) -> dict | None:
    paths = _real_photos(folder)[:sample]
    if not paths:
        return None
    pro_probs, txt_probs, craft_scores = [], [], []
    for p in paths:
        # k-NN pro prob (content-confounded) vs CLIP zero-shot text prompts.
        feat = _image_feature(p)
        pro_probs.append(classify(p)["pro_prob"])
        txt_probs.append(_binary_prob(feat, "pro", "amateur"))
        craft_scores.append(photo_quality.craft_score(p))
    return {
        "n": len(paths),
        "pro_style": _trimmed_mean(pro_probs),     # CLIP k-NN (content-keyed)
        "clip_text": _trimmed_mean(txt_probs),     # CLIP zero-shot text prompts
        "craft": _trimmed_mean(craft_scores),      # new objective craft score
    }


def report(label: str, root: Path) -> None:
    print(f"\n{'='*78}\n{label.upper()}  ({root})\n{'='*78}")
    print(f"{'listing':<48}{'knnPro':>8}{'clipTxt':>9}{'craft':>8}")
    rows = []
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            continue
        r = score_folder(folder)
        if not r:
            continue
        rows.append((folder.name, r))
        print(
            f"{folder.name[:46]:<48}"
            f"{r['pro_style']:>8.2f}{r['clip_text']:>9.2f}{r['craft']:>8.2f}"
        )
    if rows:
        avg_pro = np.mean([r["pro_style"] for _, r in rows])
        avg_txt = np.mean([r["clip_text"] for _, r in rows])
        avg_craft = np.mean([r["craft"] for _, r in rows])
        print("-" * 73)
        print(f"{'AVERAGE':<48}{avg_pro:>8.2f}{avg_txt:>9.2f}{avg_craft:>8.2f}")
        return {"pro": avg_pro, "txt": avg_txt, "craft": avg_craft}


if __name__ == "__main__":
    g = report("good (professional — should score HIGH)", TRAIN / "good")
    t = report("target (amateur — should score LOW)", TRAIN / "target")
    if g and t:
        print(f"\n{'='*78}\nSEPARATION (good avg − target avg; bigger = cleaner)\n{'='*78}")
        print(f"  CLIP k-NN pro_style : {g['pro']-t['pro']:+.2f}   (note: leave-one-in optimistic)")
        print(f"  CLIP text prompts   : {g['txt']-t['txt']:+.2f}")
        print(f"  objective craft     : {g['craft']-t['craft']:+.2f}")
