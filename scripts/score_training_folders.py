"""Score labeled training folders locally (no Zillow fetch)."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import photo_quality  # noqa: E402
from score_listings import SAMPLE_SIZE, _apply_gallery_penalties, _gallery_craft_score  # noqa: E402

IMG = {".jpg", ".jpeg", ".png", ".webp"}


def score_folder(folder: Path) -> dict:
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMG)[:SAMPLE_SIZE]
    crafts, brights, frames, verts = [], [], [], []
    for p in paths:
        q = photo_quality.analyze(p)
        crafts.append(q.craft_score)
        brights.append(q.brightness_score)
        frames.append(q.framing_score)
        verts.append(q.straightness_score)
    craft = _apply_gallery_penalties(
        _gallery_craft_score(crafts), crafts, brights, frames,
    )
    return {
        "n": len(paths),
        "craft": craft,
        "bright": float(np.mean(brights)),
        "vert": float(np.mean(verts)),
    }


def main() -> None:
    for label in ("target", "good"):
        root = ROOT / "training_data" / label
        print(f"\n=== {label} ===")
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            r = score_folder(folder)
            print(f"  {folder.name[:50]:50s} craft={r['craft']:.3f}  bright={r['bright']:.2f}  vert={r['vert']:.2f}")


if __name__ == "__main__":
    main()
