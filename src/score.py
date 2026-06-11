"""Photo scorer: k-NN CLIP classifier + zero-shot drone detection.

Per photo:
  - ``pro_prob`` (0–1): k-NN in CLIP space vs your labeled pro/target embeddings
    (``models/good_embeddings.npy`` / ``models/target_embeddings.npy``). Falls
    back to zero-shot text prompts only when target embeddings are missing.
  - ``aerial_prob`` (0–1): zero-shot text prompts (drone/aerial vs ground-level).

Retrain embeddings after adding photos: ``python src/train_clip.py``.
``clip_similarity`` (centroid) is kept for backwards compatibility.
"""
from __future__ import annotations

import math
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

# Prompt ensembles. Each class is the average of several phrasings, which is
# noticeably more stable than a single prompt.
PROMPTS = {
    "pro": [
        "a professional real estate listing photo, HDR, wide-angle lens, evenly lit and color corrected",
        "a high-end interior photo taken by a professional photographer with a DSLR camera",
        "a bright, crisp, magazine-quality photograph of a home interior",
        "a professionally edited real estate photo with balanced windows and clean white walls",
    ],
    "amateur": [
        "an amateur real estate photo taken on a smartphone, uneven lighting, snapshot",
        "a casual iPhone photo of a room, slightly crooked and dim",
        "a low-quality cellphone picture of a house interior, dark and grainy",
        "an unedited point-and-shoot photo with harsh shadows and blown-out windows",
    ],
    "aerial": [
        "an aerial drone photograph taken from high in the sky looking down at a house and its property",
        "a bird's-eye aerial view of rooftops and a neighborhood from a drone",
        "an overhead aerial shot of land, a yard, and buildings from above",
        "a drone photo showing a property boundary from the air",
    ],
    "ground": [
        "a ground-level photograph of a house exterior taken from the street",
        "a normal eye-level interior real estate photo taken from inside a room",
        "a photo of a kitchen, bedroom, or bathroom taken from standing height",
        "a front yard photo taken from the sidewalk at ground level",
    ],
}

_clip = None
_text_feats: dict | None = None
_protos: dict | None = None

KNN_K = 5
KNN_SCALE = 20.0


def _load():
    global _clip
    if _clip is None:
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        model = CLIPModel.from_pretrained(MODEL_ID).to(device).eval()
        proc = CLIPProcessor.from_pretrained(MODEL_ID)
        centroid_path = MODELS_DIR / "good_centroid.npy"
        centroid = np.load(centroid_path) if centroid_path.exists() else None
        _clip = (model, proc, device, centroid)
    return _clip


def _prototypes() -> dict | None:
    """Labeled CLIP embeddings for the pro and target (amateur) photo sets."""
    global _protos
    if _protos is None:
        good_p = MODELS_DIR / "good_embeddings.npy"
        tgt_p = MODELS_DIR / "target_embeddings.npy"
        if good_p.exists() and tgt_p.exists():
            _protos = {"good": np.load(good_p), "target": np.load(tgt_p)}
        else:
            _protos = {}
    return _protos or None


def _text_features() -> dict:
    """Mean-pooled, L2-normalized text embedding per class (cached)."""
    global _text_feats
    if _text_feats is None:
        model, proc, device, _ = _load()
        feats = {}
        for key, prompts in PROMPTS.items():
            inputs = proc(text=prompts, return_tensors="pt", padding=True).to(device)
            with torch.no_grad():
                out = model.get_text_features(**inputs)
            t = _as_tensor(out)
            t = t / t.norm(dim=-1, keepdim=True)
            mean = t.mean(dim=0)
            feats[key] = mean / mean.norm()
        _text_feats = feats
    return _text_feats


def _as_tensor(out):
    """transformers >=5 returns an output object; older returns a tensor."""
    return out.pooler_output if hasattr(out, "pooler_output") else out


def _image_feature(image_path: str | Path):
    model, proc, device, _ = _load()
    img = Image.open(image_path).convert("RGB")
    inputs = proc(images=[img], return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.get_image_features(**inputs)
    feat = _as_tensor(out)
    feat = feat / feat.norm(dim=-1, keepdim=True)
    return feat[0]


def _binary_prob(feat, pos: str, neg: str) -> float:
    """Softmax probability of the ``pos`` class vs the ``neg`` class."""
    model, *_ = _load()
    scale = model.logit_scale.exp().item()
    tf = _text_features()
    sp = float(feat @ tf[pos]) * scale
    sn = float(feat @ tf[neg]) * scale
    m = max(sp, sn)
    ep, en = math.exp(sp - m), math.exp(sn - m)
    return ep / (ep + en)


def _knn_pro_prob(feat_np) -> float:
    """Pro-vs-amateur using your labeled photo sets (nearest-neighbor in CLIP space)."""
    protos = _prototypes()
    if not protos:
        return None  # caller falls back to text zero-shot
    sg = np.sort(protos["good"] @ feat_np)[::-1][:KNN_K].mean()
    st = np.sort(protos["target"] @ feat_np)[::-1][:KNN_K].mean()
    eg, et = math.exp(KNN_SCALE * sg), math.exp(KNN_SCALE * st)
    return float(eg / (eg + et))


def classify(image_path: str | Path) -> dict:
    """Return per-photo probabilities: professional-ness and aerial/drone.

    ``pro_prob`` uses your labeled pro/target photos when available (much more
    accurate); ``aerial_prob`` uses CLIP zero-shot text prompts.
    """
    feat = _image_feature(image_path)
    pro = _knn_pro_prob(feat.cpu().numpy())
    if pro is None:
        pro = _binary_prob(feat, "pro", "amateur")
    return {
        "pro_prob": pro,
        "aerial_prob": _binary_prob(feat, "aerial", "ground"),
    }


def clip_similarity(image_path: str | Path) -> float:
    """Legacy: cosine similarity to the 'good' centroid (kept for compatibility)."""
    model, proc, device, centroid = _load()
    if centroid is None:
        return 0.0
    img = Image.open(image_path).convert("RGB")
    inputs = proc(images=[img], return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.get_image_features(**inputs)
        feat = _as_tensor(out)
    feat = (feat / feat.norm(dim=-1, keepdim=True)).cpu().numpy()[0]
    return float(feat @ centroid)


def score(image_path: str | Path) -> dict:
    h = heur_score(image_path)
    c = classify(image_path)
    final = 0.4 * h.overall_score + 0.6 * c["pro_prob"]
    return {
        "final_score": final,
        "heuristic_score": h.overall_score,
        "pro_prob": c["pro_prob"],
        "aerial_prob": c["aerial_prob"],
        "reasons": h.reasons,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(score(sys.argv[1]), indent=2))
