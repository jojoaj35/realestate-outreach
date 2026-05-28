"""Train the one-class CLIP centroid from your 'good' photo set.

Output: models/good_centroid.npy + models/good_embeddings.npy
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor
from tqdm import tqdm

MODEL_ID = "openai/clip-vit-base-patch32"
TRAINING_DIR = Path(__file__).resolve().parent.parent / "training_data" / "good"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
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
        batch_paths = paths[i : i + batch_size]
        images = []
        for p in batch_paths:
            try:
                images.append(Image.open(p).convert("RGB"))
            except Exception as e:
                print(f"  skip {p.name}: {e}")
        if not images:
            continue
        inputs = processor(images=images, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model.get_image_features(**inputs)
            # transformers 5.x wraps the already-projected 512-dim embed in ModelOutput
            feats = out.pooler_output if hasattr(out, "pooler_output") else out
        feats = feats / feats.norm(dim=-1, keepdim=True)
        vecs.append(feats.cpu().numpy())
    return np.concatenate(vecs, axis=0) if vecs else np.zeros((0, 512))


def main():
    MODELS_DIR.mkdir(exist_ok=True, parents=True)
    paths = collect_images(TRAINING_DIR)
    if not paths:
        raise SystemExit(f"No images in {TRAINING_DIR}")

    model, processor, device = load_model()
    embeddings = embed(paths, model, processor, device)
    centroid = embeddings.mean(axis=0)
    centroid = centroid / np.linalg.norm(centroid)

    np.save(MODELS_DIR / "good_embeddings.npy", embeddings)
    np.save(MODELS_DIR / "good_centroid.npy", centroid)

    sims = embeddings @ centroid
    print(f"\nTrained on {len(embeddings)} images")
    print(f"Cosine-sim to centroid: mean={sims.mean():.3f}  min={sims.min():.3f}  "
          f"p10={np.percentile(sims, 10):.3f}  p50={np.percentile(sims, 50):.3f}")
    print(f"Saved -> {MODELS_DIR}/good_centroid.npy")


if __name__ == "__main__":
    main()
