"""Combined scorer: heuristics + CLIP similarity to your 'good' centroid."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import CLIPModel, CLIPProcessor

# Make sibling-module imports work no matter the current working directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from heuristics import score_image as heur_score

MODEL_ID = "openai/clip-vit-base-patch32"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"

_clip = None


def _load():
    global _clip
    if _clip is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = CLIPModel.from_pretrained(MODEL_ID).to(device).eval()
        proc = CLIPProcessor.from_pretrained(MODEL_ID)
        centroid = np.load(MODELS_DIR / "good_centroid.npy")
        _clip = (model, proc, device, centroid)
    return _clip


def clip_similarity(image_path: str | Path) -> float:
    model, proc, device, centroid = _load()
    img = Image.open(image_path).convert("RGB")
    inputs = proc(images=[img], return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.get_image_features(**inputs)
        feat = out.pooler_output if hasattr(out, "pooler_output") else out
    feat = (feat / feat.norm(dim=-1, keepdim=True)).cpu().numpy()[0]
    return float(feat @ centroid)


def score(image_path: str | Path) -> dict:
    h = heur_score(image_path)
    sim = clip_similarity(image_path)
    # Map sim from [-1, 1] to [0, 1] roughly; in practice good photos cluster ~0.7-0.9
    sim_norm = max(0.0, min(1.0, (sim - 0.4) / 0.5))
    final = 0.4 * h.overall_score + 0.6 * sim_norm
    return {
        "final_score": final,
        "heuristic_score": h.overall_score,
        "clip_similarity": sim,
        "clip_normalized": sim_norm,
        "reasons": h.reasons,
    }


if __name__ == "__main__":
    import sys, json
    print(json.dumps(score(sys.argv[1]), indent=2))
