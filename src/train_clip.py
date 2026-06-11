"""Build the photo classifier from YOUR labeled photos.

Two classes, both grounded in real images (no guessing from text prompts):
  - training_data/good     -> professionally shot / edited listings  (NOT targets)
  - training_data/target   -> amateur / phone-photo listings         (your targets)

Output (models/):
  good_embeddings.npy    CLIP embeddings of the pro set
  target_embeddings.npy  CLIP embeddings of the amateur/target set
  good_centroid.npy      mean pro embedding (kept for compatibility)

score.classify() loads these and decides pro-vs-amateur by nearest-neighbor
similarity in CLIP space, which separates the two classes far better than a
generic text prompt.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor
from tqdm import tqdm

MODEL_ID = "openai/clip-vit-base-patch32"
ROOT = Path(__file__).resolve().parent.parent
GOOD_DIR = ROOT / "training_data" / "good"
TARGET_DIR = ROOT / "training_data" / "target"
MODELS_DIR = ROOT / "models"
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


def load_model():
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Loading CLIP on {device}...")
    model = CLIPModel.from_pretrained(MODEL_ID).to(device).eval()
    processor = CLIPProcessor.from_pretrained(MODEL_ID)
    return model, processor, device


def collect_images(root: Path) -> list[Path]:
    files = [p for p in root.rglob("*") if p.suffix.lower() in IMG_EXTS]
    print(f"Found {len(files)} images under {root}")
    return files


def embed(paths: list[Path], model, processor, device, batch_size: int = 16) -> np.ndarray:
    vecs = []
    for i in tqdm(range(0, len(paths), batch_size), desc="Embedding"):
        images = []
        for p in paths[i : i + batch_size]:
            try:
                images.append(Image.open(p).convert("RGB"))
            except Exception as e:
                print(f"  skip {p.name}: {e}")
        if not images:
            continue
        inputs = processor(images=images, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model.get_image_features(**inputs)
            feats = out.pooler_output if hasattr(out, "pooler_output") else out
        feats = feats / feats.norm(dim=-1, keepdim=True)
        vecs.append(feats.cpu().numpy())
    return np.concatenate(vecs, axis=0) if vecs else np.zeros((0, 512))


def _knn_pro_prob(feat, good, target, k=5, scale=20.0):
    sg = np.sort(good @ feat)[::-1][:k].mean()
    st = np.sort(target @ feat)[::-1][:k].mean()
    eg, et = np.exp(scale * sg), np.exp(scale * st)
    return eg / (eg + et)


def _validate(good: np.ndarray, target: np.ndarray, k=5, scale=20.0) -> None:
    """Leave-one-out accuracy so we know the classifier actually separates."""
    correct = 0
    for j in range(len(good)):
        rest = np.delete(good, j, axis=0)
        if _knn_pro_prob(good[j], rest, target, k, scale) >= 0.5:
            correct += 1
    for j in range(len(target)):
        rest = np.delete(target, j, axis=0)
        if _knn_pro_prob(target[j], good, rest, k, scale) < 0.5:
            correct += 1
    total = len(good) + len(target)
    print(f"\nLeave-one-out accuracy: {correct}/{total} = {correct/total:.0%}")


def main():
    MODELS_DIR.mkdir(exist_ok=True, parents=True)
    good_paths = collect_images(GOOD_DIR)
    target_paths = collect_images(TARGET_DIR)
    if not good_paths:
        raise SystemExit(f"No pro images in {GOOD_DIR}")
    if not target_paths:
        raise SystemExit(f"No target images in {TARGET_DIR} — add example listings first.")

    model, processor, device = load_model()
    good = embed(good_paths, model, processor, device)
    target = embed(target_paths, model, processor, device)

    np.save(MODELS_DIR / "good_embeddings.npy", good)
    np.save(MODELS_DIR / "target_embeddings.npy", target)
    centroid = good.mean(axis=0)
    np.save(MODELS_DIR / "good_centroid.npy", centroid / np.linalg.norm(centroid))

    print(f"\nPro set: {len(good)} imgs   Target set: {len(target)} imgs")
    _validate(good, target)
    print(f"Saved prototypes -> {MODELS_DIR}")


if __name__ == "__main__":
    main()
