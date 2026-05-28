"""Calibrate heuristic thresholds from the 'good' training set.

Picks thresholds at the 5th percentile so ~95% of your good photos pass each check.
Writes models/thresholds.json.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TRAIN = ROOT / "training_data" / "good"
OUT = ROOT / "models" / "thresholds.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def main() -> None:
    blur, lum, widths, heights = [], [], [], []
    for p in TRAIN.rglob("*"):
        if p.suffix.lower() not in EXTS:
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        blur.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        lum.append(float(gray.mean()))
        widths.append(w)
        heights.append(h)

    blur = np.array(blur)
    lum = np.array(lum)

    thresholds = {
        "blur_threshold": float(np.percentile(blur, 5)) * 0.8,
        "exposure_low": float(np.percentile(lum, 5)) * 0.85,
        "exposure_high": float(np.percentile(lum, 95)) * 1.05,
        "min_width": int(np.percentile(widths, 5) * 0.5),
        "min_height": int(np.percentile(heights, 5) * 0.5),
        "stats": {
            "n_images": int(len(blur)),
            "blur_mean": float(blur.mean()),
            "blur_p5": float(np.percentile(blur, 5)),
            "blur_p50": float(np.percentile(blur, 50)),
            "lum_mean": float(lum.mean()),
            "lum_p5": float(np.percentile(lum, 5)),
            "lum_p95": float(np.percentile(lum, 95)),
        },
    }

    OUT.write_text(json.dumps(thresholds, indent=2))
    print(json.dumps(thresholds, indent=2))
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()
