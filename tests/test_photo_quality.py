"""Tests for the content-agnostic photo-craft signals.

These build synthetic images so the assertions are about *photographic* quality
(exposure, white balance, sharpness, orientation), never scene content.
"""
import cv2
import numpy as np

import photo_quality as pq


def _write(tmp_path, name, arr):
    path = tmp_path / name
    cv2.imwrite(str(path), arr)
    return path


def _wellexposed(h=900, w=1200, gray=150):
    """A neutral, mid-toned, textured landscape frame (good craft baseline)."""
    rng = np.random.default_rng(0)
    base = np.full((h, w, 3), gray, dtype=np.int16)
    noise = rng.integers(-25, 26, (h, w, 3))
    return np.clip(base + noise, 5, 245).astype(np.uint8)


def test_blown_windows_lower_window_pull(tmp_path):
    # Hold brightness constant (both bright) so this isolates window pull.
    img = _wellexposed(gray=185)
    blown = img.copy()
    blown[:, :400] = 255  # a nuked window region
    good = pq.analyze(_write(tmp_path, "good.png", img))
    bad = pq.analyze(_write(tmp_path, "blown.png", blown))
    assert good.window_pull_score > bad.window_pull_score
    assert bad.window_pull_score < 0.5
    assert good.brightness_score == bad.brightness_score == 1.0
    assert good.craft_score > bad.craft_score


def test_color_cast_lowers_white_balance(tmp_path):
    neutral = _wellexposed(gray=200)
    cast = neutral.copy().astype(np.int16)
    cast[:, :, 0] = np.clip(cast[:, :, 0] - 60, 0, 255)  # drop blue -> orange cast
    cast[:, :, 2] = np.clip(cast[:, :, 2] + 30, 0, 255)
    cast = cast.astype(np.uint8)
    good = pq.analyze(_write(tmp_path, "neutral.png", neutral))
    bad = pq.analyze(_write(tmp_path, "cast.png", cast))
    assert good.white_balance_score > bad.white_balance_score
    assert bad.white_balance_score < 0.6


def test_blur_lowers_sharpness(tmp_path):
    sharp = _wellexposed()
    blurry = cv2.GaussianBlur(sharp, (0, 0), sigmaX=8)
    assert pq.analyze(_write(tmp_path, "sharp.png", sharp)).sharpness_score > \
        pq.analyze(_write(tmp_path, "blur.png", blurry)).sharpness_score


def test_portrait_orientation_penalized(tmp_path):
    landscape = _wellexposed(h=900, w=1200)
    portrait = _wellexposed(h=1200, w=900)
    land = pq.analyze(_write(tmp_path, "land.png", landscape))
    port = pq.analyze(_write(tmp_path, "port.png", portrait))
    assert land.orientation_score == 1.0
    assert port.orientation_score == 0.0
    assert port.is_portrait is True
    assert land.craft_score > port.craft_score


def test_tiny_image_is_neutral_not_penalized(tmp_path):
    tiny = _wellexposed(h=19, w=11)
    q = pq.analyze(_write(tmp_path, "thumb.png", tiny))
    assert q.craft_score == 0.5
    assert "too small to judge" in q.flags


def test_content_agnostic_bright_white_room_not_blown(tmp_path):
    """A bright (but not clipped) white room must NOT read as blown-out."""
    bright = _wellexposed(gray=238)  # bright walls, channels well under 250
    q = pq.analyze(_write(tmp_path, "white_room.png", bright))
    assert q.window_pull_score > 0.8
    assert q.craft_score > 0.7


def test_dark_dull_lowers_brightness(tmp_path):
    """Dark/dull (under-exposed) photos must score low on brightness + craft."""
    bright = _wellexposed(gray=140)
    dark = _wellexposed(gray=60)
    b = pq.analyze(_write(tmp_path, "bright.png", bright))
    d = pq.analyze(_write(tmp_path, "dark.png", dark))
    assert b.brightness_score > d.brightness_score
    assert d.brightness_score < 0.4
    assert b.craft_score > d.craft_score
