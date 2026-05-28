import cv2
import numpy as np

import heuristics


def _write(tmp_path, name, arr):
    path = tmp_path / name
    cv2.imwrite(str(path), arr)
    return path


def test_sharp_vs_blurry(tmp_path):
    rng = np.random.default_rng(0)
    sharp = rng.integers(0, 256, (1200, 1600, 3), dtype=np.uint8)
    blurry = cv2.GaussianBlur(sharp, (0, 0), sigmaX=12)

    assert heuristics.score_image(_write(tmp_path, "sharp.png", sharp)).is_blurry is False
    assert heuristics.score_image(_write(tmp_path, "blur.png", blurry)).is_blurry is True


def test_exposure_extremes(tmp_path):
    dark = np.zeros((1200, 1600, 3), dtype=np.uint8)
    bright = np.full((1200, 1600, 3), 255, dtype=np.uint8)
    assert heuristics.score_image(_write(tmp_path, "dark.png", dark)).is_over_or_under_exposed
    assert heuristics.score_image(_write(tmp_path, "bright.png", bright)).is_over_or_under_exposed


def test_portrait_flag(tmp_path):
    rng = np.random.default_rng(1)
    portrait = rng.integers(0, 256, (1600, 1200, 3), dtype=np.uint8)
    assert heuristics.score_image(_write(tmp_path, "portrait.png", portrait)).is_portrait is True


def test_score_listing_too_few_photos(tmp_path):
    rng = np.random.default_rng(2)
    img = rng.integers(0, 256, (1500, 2200, 3), dtype=np.uint8)
    paths = [_write(tmp_path, f"p{i}.png", img) for i in range(3)]
    result = heuristics.score_listing(paths)
    assert any("only 3 photos" in r for r in result["reasons"])
